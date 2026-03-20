# tests/db/test_api_keys_module.py
import importlib
import sys
import types
from datetime import UTC, datetime, timedelta, timezone

import pytest

MODULE_PATH = "src.db.api_keys"  # <-- change if your file lives elsewhere


# ------------------------ Minimal in-memory Supabase double ------------------------


class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    def __init__(self, store, name, supabase=None):
        self.store = store
        self.name = name
        self.supabase = supabase
        self._filters = []  # list of (field, op, value)
        self._select = None
        self._order = None
        self._desc = False
        self._delete_mode = False  # Track if we're in delete mode
        self._update_patch = None  # Track update data

    # query builders (chainable)
    def select(self, _cols="*"):
        self._select = _cols
        return self

    def eq(self, field, value):
        self._filters.append((field, "eq", value))
        return self

    def neq(self, field, value):
        self._filters.append((field, "neq", value))
        return self

    def order(self, field, desc=False):
        self._order = field
        self._desc = desc
        return self

    def _match(self, row):
        for field, op, val in self._filters:
            if op == "eq" and row.get(field) != val:
                return False
            if op == "neq" and row.get(field) == val:
                return False
        return True

    def _rows(self):
        return [r for r in self.store[self.name]]

    def _filtered(self):
        return [r for r in self._rows() if self._match(r)]

    def insert(self, data):
        # accept dict or list[dict]
        rows = data if isinstance(data, list) else [data]
        if self.supabase:
            failures = self.supabase.insert_failures.get(self.name, [])
            if failures:
                exc = failures.pop(0)
                raise exc
        for r in rows:
            if "id" not in r:
                r["id"] = len(self.store[self.name]) + 1
            if "created_at" not in r:
                r["created_at"] = datetime.now(UTC).isoformat()
            self.store[self.name].append(r)
        return self

    def update(self, patch):
        self._update_patch = patch
        return self

    def delete(self):
        self._delete_mode = True
        return self

    def execute(self):
        # Handle update - defer filtering until execute
        if self._update_patch is not None:
            out = []
            for r in self._filtered():
                r.update(self._update_patch)
                out.append(r)
            return _Result(out)

        # Handle delete - defer filtering until execute
        if self._delete_mode:
            to_delete = self._filtered()
            self.store[self.name] = [r for r in self._rows() if r not in to_delete]
            return _Result(to_delete)

        # Handle select
        rows = self._filtered()
        if self._order:
            rows.sort(key=lambda r: r.get(self._order), reverse=self._desc)
        return _Result(rows)


class FakeSupabase:
    def __init__(self):
        self.store = {
            "api_keys_new": [],
            "api_keys": [],
            "rate_limit_configs": [],
            "api_key_audit_logs": [],
        }
        self.insert_failures = {}

    def table(self, name):
        if name not in self.store:
            self.store[name] = []
        return _Table(self.store, name, supabase=self)

    def fail_next_insert(self, table_name, exception):
        queue = self.insert_failures.setdefault(table_name, [])
        queue.append(exception)


# ------------------------ Shared fixtures ------------------------


@pytest.fixture
def fake_supabase():
    return FakeSupabase()


def _is_connection_error(exception: Exception) -> bool:
    """Check if an exception is a connection-related error that should trigger retry."""
    error_message = str(exception).lower()
    connection_indicators = [
        "connection",
        "protocol",
        "stream",
        "terminated",
        "network",
        "timeout",
        "econnreset",
        "epipe",
        "eof",
        "socket",
        "ssl",
        "http/2",
        "h2",
        "localprotocolerror",
        "remoteprotocolerror",
        "stream reset",
        "goaway",
        "send_headers",
    ]
    return any(indicator in error_message for indicator in connection_indicators)


def _make_execute_with_retry(get_client_func):
    """Create an execute_with_retry function that uses the provided get_client function."""

    def execute_with_retry(
        operation,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        operation_name: str = "database operation",
    ):
        last_exception = None
        for attempt in range(max_retries + 1):
            try:
                client = get_client_func()
                return operation(client)
            except Exception as e:
                last_exception = e
                if _is_connection_error(e):
                    if attempt < max_retries:
                        continue
                else:
                    # Non-connection error, don't retry
                    raise
        if last_exception:
            raise last_exception
        return None

    return execute_with_retry


@pytest.fixture
def mod(fake_supabase, monkeypatch):
    # Set KEY_HASH_SALT for API key creation tests
    monkeypatch.setenv("KEY_HASH_SALT", "0123456789abcdef0123456789abcdef")

    # stub out get_supabase_client and execute_with_retry
    supabase_mod = types.SimpleNamespace(
        get_supabase_client=lambda: fake_supabase,
        execute_with_retry=_make_execute_with_retry(lambda: fake_supabase),
        is_connection_error=_is_connection_error,
        refresh_supabase_client=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "src.config.supabase_config", supabase_mod)

    # stub plan entitlements
    plans_mod = types.SimpleNamespace(
        check_plan_entitlements=lambda user_id: {"monthly_request_limit": 5000}
    )
    monkeypatch.setitem(sys.modules, "src.db.plans", plans_mod)

    # stub audit logger
    security_mod = types.SimpleNamespace(
        get_audit_logger=lambda: types.SimpleNamespace(
            log_api_key_creation=lambda *args, **kwargs: None,
            log_api_key_deletion=lambda *args, **kwargs: None,
        )
    )
    monkeypatch.setitem(sys.modules, "src.security.security", security_mod)

    # ensure deterministic secrets.token_urlsafe
    import secrets as real_secrets

    monkeypatch.setattr(real_secrets, "token_urlsafe", lambda n=32: "TOK", raising=True)

    # preload a default users module targetable by the code's late imports
    fake_users_mod = types.SimpleNamespace(get_user=lambda api_key: None)
    monkeypatch.setitem(sys.modules, "src.db.users", fake_users_mod)

    # import the module fresh
    m = importlib.import_module(MODULE_PATH)
    importlib.reload(m)
    return m


# ------------------------ Tests ------------------------


def test_check_key_name_uniqueness(mod, fake_supabase):
    # no keys yet -> unique
    assert mod.check_key_name_uniqueness(user_id=1, key_name="A") is True

    # add one
    fake_supabase.table("api_keys_new").insert({"user_id": 1, "key_name": "A"}).execute()
    assert mod.check_key_name_uniqueness(1, "A") is False
    # exclude this key id -> becomes unique for rename on self
    key_id = fake_supabase.store["api_keys_new"][0]["id"]
    assert mod.check_key_name_uniqueness(1, "A", exclude_key_id=key_id) is True


def test_create_api_key_primary_sets_trial_and_prefix_and_audit(monkeypatch, mod, fake_supabase):
    # user 99, enforce plan limit, deterministic token -> "gw_live_TOK"
    api_key, key_id = mod.create_api_key(
        user_id=99,
        key_name="Main",
        environment_tag="live",
        scope_permissions=None,  # uses defaults
        expiration_days=2,
        max_requests=999999,  # will be clamped to plan's monthly_request_limit (5000)
        ip_allowlist=["1.2.3.4"],
        domain_referrers=["https://x.y"],
        is_primary=True,
    )
    assert api_key.startswith("gw_live_") and "TOK" in api_key

    # row exists in api_keys_new
    rows = fake_supabase.store["api_keys_new"]
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == 99
    assert row["is_primary"] is True
    assert row["scope_permissions"]["read"] == ["*"]
    assert row["max_requests"] == 5000  # clamped
    assert "trial_end_date" in row and row["subscription_status"] == "trial"

    # rate limit config created
    rlc = fake_supabase.store["rate_limit_configs"]
    assert len(rlc) == 1
    assert rlc[0]["api_key_id"] == row["id"]

    # audit log created
    logs = fake_supabase.store["api_key_audit_logs"]
    assert logs and logs[0]["action"] == "create"


def test_create_api_key_refreshes_schema_cache_on_pgrst204(monkeypatch, mod, fake_supabase):
    from postgrest import APIError

    # Simulate PostgREST schema cache error
    error = APIError({"code": "PGRST204", "message": "Could not find column in schema cache"})
    fake_supabase.fail_next_insert("api_keys_new", error)

    refresh_calls = {"count": 0}

    def fake_refresh(client):
        refresh_calls["count"] += 1
        return True

    monkeypatch.setattr(mod, "refresh_postgrest_schema_cache", fake_refresh)

    api_key, key_id = mod.create_api_key(user_id=7, key_name="RetryKey")

    assert api_key.startswith("gw_live_")
    assert refresh_calls["count"] == 1
    assert len(fake_supabase.store["api_keys_new"]) == 1


def test_create_api_key_schema_cache_error_fallback(monkeypatch, mod, fake_supabase):
    """Ensure we recover from PostgREST schema cache misses for new columns."""
    from postgrest import APIError

    monkeypatch.setenv("KEY_HASH_SALT", "0123456789abcdef0123456789abcdef")

    error = APIError(
        {
            "code": "PGRST204",
            "message": "Could not find the 'key_version' column of 'api_keys_new' in the schema cache",
        }
    )
    fake_supabase.fail_next_insert("api_keys_new", error)

    monkeypatch.setattr(mod, "refresh_postgrest_schema_cache", lambda client: True)

    api_key, key_id = mod.create_api_key(user_id=1, key_name="Primary", is_primary=True)

    assert api_key.startswith("gw_live_")
    assert key_id == 1

    stored_row = fake_supabase.store["api_keys_new"][0]
    assert "key_version" not in stored_row
    assert stored_row["api_key"] == api_key


def test_get_user_api_keys_builds_fields(mod, fake_supabase):
    # load 2 keys
    now = datetime.now(UTC)
    fake_supabase.table("api_keys_new").insert(
        [
            {
                "user_id": 7,
                "key_name": "K1",
                "api_key": "gw_live_A",
                "is_active": True,
                "requests_used": 10,
                "max_requests": 100,
                "environment_tag": "live",
                "expiration_date": (now + timedelta(days=5)).isoformat(),
                "scope_permissions": {"read": ["*"]},
                "last_used_at": now.isoformat(),
            },
            {
                "user_id": 7,
                "key_name": "K2",
                "api_key": "gw_live_B",
                "is_active": False,
                "requests_used": 0,
                "max_requests": None,
                "environment_tag": "test",
                "expiration_date": None,
                "scope_permissions": {},
                "last_used_at": None,
            },
        ]
    ).execute()

    out = mod.get_user_api_keys(7)
    assert len(out) == 2
    k1 = next(k for k in out if k["key_name"] == "K1")
    assert k1["requests_remaining"] == 90
    assert 0 < k1["days_remaining"] <= 5


def test_delete_api_key_new_and_legacy(mod, fake_supabase):
    # Test deleting keys from api_keys_new table
    fake_supabase.table("api_keys_new").insert(
        {
            "user_id": 2,
            "key_name": "Key1",
            "api_key": "gw_live_X",
            "is_active": True,
            "requests_used": 0,
        }
    ).execute()
    fake_supabase.table("api_keys_new").insert(
        {
            "user_id": 2,
            "key_name": "Key2",
            "api_key": "gw_test_Y",
            "is_active": True,
            "requests_used": 0,
        }
    ).execute()

    assert mod.delete_api_key("gw_live_X", user_id=2) is True
    # Check that only one key remains
    assert len(fake_supabase.store["api_keys_new"]) == 1
    assert fake_supabase.store["api_keys_new"][0]["api_key"] == "gw_test_Y"
    # audit log for delete created
    assert fake_supabase.store["api_key_audit_logs"]

    # Delete the second key
    assert mod.delete_api_key("gw_test_Y", user_id=2) is True
    assert not fake_supabase.store["api_keys_new"]  # all deleted


def test_validate_api_key_prefers_api_keys_then_fallback(monkeypatch, mod, fake_supabase):
    # 1) Found in api_keys_new table and active with not-expired
    now = datetime.now(UTC)
    fake_supabase.table("api_keys_new").insert(
        {
            "id": 11,
            "user_id": 777,
            "key_name": "K1",
            "api_key": "gw_live_1",
            "is_active": True,
            "expiration_date": (now + timedelta(days=1)).isoformat(),
            "max_requests": 100,
            "requests_used": 0,
        }
    ).execute()

    # Late-imported get_user must exist and return the user for a key
    users_mod = sys.modules["src.db.users"]
    users_mod.get_user = lambda api_key: {"id": 777} if api_key == "gw_live_1" else None

    out = mod.validate_api_key("gw_live_1")
    assert out and out["user_id"] == 777 and out["key_id"] == 11

    # 2) Not in api_keys_new -> fallback to users table (legacy keys in users table only)
    users_mod.get_user = lambda api_key: {"id": 888} if api_key == "legacy_2" else None
    out2 = mod.validate_api_key("legacy_2")
    assert out2 and out2["user_id"] == 888 and out2["key_name"] == "Legacy Key"


def test_increment_api_key_usage_updates_new_or_legacy(monkeypatch, mod, fake_supabase):
    # Test incrementing usage for keys in api_keys_new
    fake_supabase.table("api_keys_new").insert(
        {"api_key": "gw_live_Y", "requests_used": 1, "is_active": True}
    ).execute()
    mod.increment_api_key_usage("gw_live_Y")
    row = fake_supabase.store["api_keys_new"][0]
    assert row["requests_used"] == 2 and row.get("last_used_at")

    # Test incrementing for another key
    fake_supabase.table("api_keys_new").insert(
        {"api_key": "gw_test_Z", "requests_used": 5, "is_active": True}
    ).execute()
    mod.increment_api_key_usage("gw_test_Z")
    row2 = [r for r in fake_supabase.store["api_keys_new"] if r["api_key"] == "gw_test_Z"][0]
    assert row2["requests_used"] == 6


def test_get_api_key_usage_stats_new_vs_legacy(mod, fake_supabase):
    # Test stats for key with usage
    fake_supabase.table("api_keys_new").insert(
        {
            "api_key": "gw_live_S",
            "key_name": "SKey",
            "is_active": True,
            "requests_used": 10,
            "max_requests": 100,
            "environment_tag": "live",
            "last_used_at": "2025-01-01T00:00:00+00:00",
        }
    ).execute()
    out_new = mod.get_api_key_usage_stats("gw_live_S")
    assert out_new["requests_remaining"] == 90
    assert out_new["usage_percentage"] == 10.0

    # Test stats for another key
    fake_supabase.table("api_keys_new").insert(
        {
            "api_key": "gw_test_T",
            "key_name": "TKey",
            "is_active": True,
            "requests_used": 7,
            "max_requests": 100,
            "environment_tag": "test",
            "created_at": "2025-01-02T00:00:00+00:00",
            "last_used_at": "2025-01-03T00:00:00+00:00",
        }
    ).execute()
    out_test = mod.get_api_key_usage_stats("gw_test_T")
    assert out_test["requests_used"] == 7
    assert out_test["usage_percentage"] == 7.0
    assert out_test["environment_tag"] == "test"


def test_update_api_key_name_uniqueness_and_expiration_and_rate_limit(mod, fake_supabase):
    # seed two keys for same user
    fake_supabase.table("api_keys_new").insert(
        [
            {"id": 1, "user_id": 5, "key_name": "A", "api_key": "gw_live_A", "is_active": True},
            {
                "id": 2,
                "user_id": 5,
                "key_name": "B",
                "api_key": "gw_live_B",
                "is_active": True,
                "max_requests": 100,
            },
        ]
    ).execute()
    # a rate_limit_config row for id=2
    fake_supabase.table("rate_limit_configs").insert(
        {"api_key_id": 2, "max_requests": 100}
    ).execute()

    # try to rename B -> A (should fail uniqueness)
    with pytest.raises(RuntimeError):
        mod.update_api_key("gw_live_B", user_id=5, updates={"key_name": "A"})

    # update expiration_days and max_requests
    assert (
        mod.update_api_key(
            "gw_live_B", user_id=5, updates={"expiration_days": 2, "max_requests": 250}
        )
        is True
    )

    # check updated key and rate_limit_config
    k = [r for r in fake_supabase.store["api_keys_new"] if r["id"] == 2][0]
    assert k["max_requests"] == 250 and "expiration_date" in k
    rlc = [r for r in fake_supabase.store["rate_limit_configs"] if r["api_key_id"] == 2][0]
    assert rlc["max_requests"] == 250

    # audit log recorded
    assert fake_supabase.store["api_key_audit_logs"]


def test_validate_api_key_permissions(mod, fake_supabase):
    # gw_temp => always true
    assert mod.validate_api_key_permissions("gw_temp_abcd", "read", "anything") is True

    # new key with explicit scope
    fake_supabase.table("api_keys_new").insert(
        {
            "api_key": "gw_live_perm",
            "is_active": True,
            "scope_permissions": {"read": ["*"], "write": ["dataset1"]},
        }
    ).execute()
    assert mod.validate_api_key_permissions("gw_live_perm", "read", "x") is True
    assert mod.validate_api_key_permissions("gw_live_perm", "write", "dataset1") is True
    assert mod.validate_api_key_permissions("gw_live_perm", "write", "dataset2") is False

    # inactive key -> false
    fake_supabase.table("api_keys_new").insert(
        {"api_key": "gw_live_inact", "is_active": False, "scope_permissions": {"read": ["*"]}}
    ).execute()
    assert mod.validate_api_key_permissions("gw_live_inact", "read", "x") is False


def test_get_api_key_by_id(mod, fake_supabase):
    exp = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    fake_supabase.table("api_keys_new").insert(
        {
            "id": 123,
            "user_id": 4,
            "key_name": "K",
            "api_key": "gw_live_K",
            "is_active": True,
            "max_requests": 100,
            "requests_used": 10,
            "expiration_date": exp,
            "environment_tag": "live",
        }
    ).execute()

    out = mod.get_api_key_by_id(123, user_id=4)
    assert out and out["id"] == 123 and out["requests_remaining"] == 90
    assert out["days_remaining"] is not None and out["days_remaining"] >= 2


def test_get_user_all_api_keys_usage(mod, fake_supabase):
    fake_supabase.table("api_keys_new").insert(
        [
            {
                "user_id": 10,
                "api_key": "gw_live_1",
                "key_name": "K1",
                "is_active": True,
                "requests_used": 5,
                "max_requests": 100,
                "environment_tag": "live",
            },
            {
                "user_id": 10,
                "api_key": "gw_test_2",
                "key_name": "K2",
                "is_active": False,
                "requests_used": 0,
                "max_requests": None,
                "environment_tag": "test",
            },
        ]
    ).execute()

    out = mod.get_user_all_api_keys_usage(10)
    assert out["user_id"] == 10
    assert out["total_keys"] == 2
    by_name = {k["key_name"]: k for k in out["keys"]}
    assert by_name["K1"]["requests_remaining"] == 95
    assert by_name["K2"]["max_requests"] is None


# ----------------------------- tests: HTTP/2 connection error handling -----------------------------


def test_get_api_key_by_key_retries_on_http2_error(monkeypatch, mod, fake_supabase):
    """HTTP/2 connection errors should trigger retry via execute_with_retry"""
    # Seed a key
    fake_supabase.table("api_keys_new").insert(
        {
            "api_key": "gw_live_retry",
            "user_id": 1,
            "key_name": "RetryKey",
            "is_active": True,
        }
    ).execute()

    call_count = 0
    original_table = fake_supabase.table

    def failing_then_succeeding_table(name):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("LocalProtocolError: StreamIDTooLowError: 173 is lower than 193")
        return original_table(name)

    fake_supabase.table = failing_then_succeeding_table

    result = mod.get_api_key_by_key("gw_live_retry")

    # Should succeed after retry
    assert result is not None
    assert result["api_key"] == "gw_live_retry"
    assert call_count == 2  # Initial call + 1 retry


def test_get_api_key_by_key_retries_on_connection_terminated(monkeypatch, mod, fake_supabase):
    """ConnectionTerminated errors should trigger retry"""
    fake_supabase.table("api_keys_new").insert(
        {
            "api_key": "gw_live_ct",
            "user_id": 1,
            "key_name": "CTKey",
            "is_active": True,
        }
    ).execute()

    call_count = 0
    original_table = fake_supabase.table

    def failing_then_succeeding_table(name):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("<ConnectionTerminated error_code:9, last_stream_id:191>")
        return original_table(name)

    fake_supabase.table = failing_then_succeeding_table

    result = mod.get_api_key_by_key("gw_live_ct")

    assert result is not None
    assert result["api_key"] == "gw_live_ct"
    assert call_count == 2


def test_get_api_key_by_key_retries_on_send_headers_error(monkeypatch, mod, fake_supabase):
    """SEND_HEADERS state errors should trigger retry"""
    fake_supabase.table("api_keys_new").insert(
        {
            "api_key": "gw_live_sh",
            "user_id": 1,
            "key_name": "SHKey",
            "is_active": True,
        }
    ).execute()

    call_count = 0
    original_table = fake_supabase.table

    def failing_then_succeeding_table(name):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Invalid input StreamInputs.SEND_HEADERS in state 5")
        return original_table(name)

    fake_supabase.table = failing_then_succeeding_table

    result = mod.get_api_key_by_key("gw_live_sh")

    assert result is not None
    assert result["api_key"] == "gw_live_sh"
    assert call_count == 2


def test_get_api_key_by_key_returns_none_after_max_retries(monkeypatch, mod, fake_supabase):
    """Should return None after max retries are exhausted"""
    call_count = 0

    def always_failing_table(name):
        nonlocal call_count
        call_count += 1
        raise Exception("LocalProtocolError: StreamIDTooLowError: connection broken")

    fake_supabase.table = always_failing_table

    result = mod.get_api_key_by_key("gw_live_fail")

    # Should return None after retries exhausted
    assert result is None
    # execute_with_retry uses max_retries=2, so initial + 2 retries = 3 calls
    assert call_count == 3


def test_get_api_key_by_key_no_retry_on_non_connection_error(monkeypatch, mod, fake_supabase):
    """Non-connection errors should not trigger retry"""
    call_count = 0

    def non_connection_error_table(name):
        nonlocal call_count
        call_count += 1
        raise ValueError("Invalid data format - missing required field")

    fake_supabase.table = non_connection_error_table

    result = mod.get_api_key_by_key("gw_live_other")

    # Should return None without retrying
    assert result is None
    assert call_count == 1  # Only 1 call, no retries for non-connection errors
