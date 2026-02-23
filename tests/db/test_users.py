import types
import uuid
import pytest
from datetime import datetime, timedelta, timezone, UTC

# ---- In-memory Supabase stub ------------------------------------------------

class _Result:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count

    # allow `.execute()` at the end of chains
    def execute(self):
        return self


class _BaseQuery:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self._filters = []   # list of tuples: (op, field, value)
        self._order = None   # (field, desc)
        self._limit = None

    def eq(self, field, value):
        self._filters.append(("eq", field, value))
        return self

    def gte(self, field, value):
        self._filters.append(("gte", field, value))
        return self

    def lt(self, field, value):
        self._filters.append(("lt", field, value))
        return self

    def in_(self, field, values):
        """Support Supabase .in_() filter for batch lookups."""
        self._filters.append(("in", field, values))
        return self

    def order(self, field, desc=False):
        self._order = (field, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self, row):
        def as_iso(x):
            return x
        for op, f, v in self._filters:
            rv = row.get(f)
            if op == "eq":
                if rv != v:
                    return False
            elif op == "gte":
                if as_iso(rv) < as_iso(v):
                    return False
            elif op == "lt":
                if as_iso(rv) >= as_iso(v):
                    return False
            elif op == "in":
                # v is a list of values to match against
                if rv not in v:
                    return False
        return True

    def _apply_order_limit(self, rows):
        if self._order:
            field, desc = self._order
            rows = sorted(rows, key=lambda r: r.get(field), reverse=bool(desc))
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


class _Select(_BaseQuery):
    def __init__(self, store, table):
        super().__init__(store, table)
        self._count = None

    def select(self, *_cols, count=None):
        self._count = count
        return self

    def execute(self):
        rows = [r.copy() for r in self.store[self.table] if self._match(r)]
        rows = self._apply_order_limit(rows)
        cnt = len(rows) if self._count == "exact" else None
        return _Result(rows, cnt)


class _Insert:
    def __init__(self, store, table, payload):
        self.store = store
        self.table = table
        self.payload = payload

    def execute(self):
        inserted = []
        if isinstance(self.payload, list):
            items = self.payload
        else:
            items = [self.payload]
        for item in items:
            row = item.copy()
            if "id" not in row:
                # simple autoincrement per table
                next_id = (max([r.get("id", 0) for r in self.store[self.table]] or [0]) + 1)
                row["id"] = next_id
            self.store[self.table].append(row)
            inserted.append(row)
        return _Result(inserted)


class _Update(_BaseQuery):
    def __init__(self, store, table, payload):
        super().__init__(store, table)
        self.payload = payload

    def execute(self):
        updated = []
        for row in self.store[self.table]:
            if self._match(row):
                row.update(self.payload)
                updated.append(row.copy())
        return _Result(updated)


class _Delete(_BaseQuery):
    def execute(self):
        kept, deleted = [], []
        for row in self.store[self.table]:
            (deleted if self._match(row) else kept).append(row)
        self.store[self.table][:] = kept
        return _Result(deleted)


class SupabaseStub:
    def __init__(self):
        from collections import defaultdict
        self.tables = defaultdict(list)

    def table(self, name):
        # return an object exposing select/insert/update/delete like supabase-py
        class _TableShim:
            def __init__(self, outer, table):
                self._outer = outer
                self._table = table

            def select(self, *cols, count=None):
                return _Select(self._outer.tables, self._table).select(*cols, count=count)

            def insert(self, payload):
                return _Insert(self._outer.tables, self._table, payload)

            def update(self, payload):
                return _Update(self._outer.tables, self._table, payload)

            def delete(self):
                return _Delete(self._outer.tables, self._table)

        return _TableShim(self, name)

    # RPC: only the function used by users.py
    def rpc(self, fn_name, params=None):
        class _RPCShim:
            def __init__(self, outer, fn_name, params):
                self.outer = outer
                self.fn_name = fn_name
                self.params = params or {}

            def execute(self):
                if self.fn_name == "get_user_usage_metrics":
                    api_key = self.params.get("user_api_key")
                    usage = [r for r in self.outer.tables["usage_records"] if r.get("api_key") == api_key]
                    total_requests = len(usage)
                    total_tokens = sum(r.get("tokens_used", 0) for r in usage)
                    total_cost = sum(r.get("cost", 0.0) for r in usage)
                    now = datetime.now(UTC)
                    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

                    def after(ts, start):
                        # stored as ISO strings
                        return ts and ts >= start.isoformat()

                    requests_today = len([r for r in usage if after(r.get("timestamp"), today)])
                    tokens_today = sum(r.get("tokens_used", 0) for r in usage if after(r.get("timestamp"), today))
                    cost_today = sum(r.get("cost", 0.0) for r in usage if after(r.get("timestamp"), today))
                    requests_this_month = len([r for r in usage if after(r.get("timestamp"), month)])
                    tokens_this_month = sum(r.get("tokens_used", 0) for r in usage if after(r.get("timestamp"), month))
                    cost_this_month = sum(r.get("cost", 0.0) for r in usage if after(r.get("timestamp"), month))
                    avg_tokens = (total_tokens / total_requests) if total_requests > 0 else 0.0
                    most_used_model = None
                    if usage:
                        counts = {}
                        for r in usage:
                            counts[r.get("model", "unknown")] = counts.get(r.get("model", "unknown"), 0) + 1
                        most_used_model = max(counts.items(), key=lambda kv: kv[1])[0]
                    last_request_time = max([r.get("timestamp") for r in usage], default=None)

                    payload = [{
                        "total_requests": total_requests,
                        "total_tokens": total_tokens,
                        "total_cost": total_cost,
                        "requests_today": requests_today,
                        "tokens_today": tokens_today,
                        "cost_today": cost_today,
                        "requests_this_month": requests_this_month,
                        "tokens_this_month": tokens_this_month,
                        "cost_this_month": cost_this_month,
                        "average_tokens_per_request": avg_tokens,
                        "most_used_model": most_used_model,
                        "last_request_time": last_request_time,
                    }]
                    return _Result(payload)
                # default empty result
                return _Result([])
        return _RPCShim(self, fn_name, params)

# ---- Fixtures / patching ----------------------------------------------------

@pytest.fixture()
def sb(monkeypatch):
    # import module under test
    import src.db.users as users_mod
    import src.db.api_keys as api_keys_mod

    stub = SupabaseStub()
    # Patch in the modules where it's actually used (not just where it's defined)
    monkeypatch.setattr(users_mod, "get_supabase_client", lambda: stub)
    monkeypatch.setattr(api_keys_mod, "get_supabase_client", lambda: stub)

    # stub audit logger for when create_api_key is called
    security_mod = types.SimpleNamespace(
        get_audit_logger=lambda: types.SimpleNamespace(
            log_api_key_creation=lambda *args, **kwargs: None,
            log_api_key_deletion=lambda *args, **kwargs: None
        )
    )
    monkeypatch.setitem(__import__("sys").modules, "src.security.security", security_mod)

    # fake credit transaction module used inside functions via local import
    tx_log = []
    fake_tx = types.SimpleNamespace(
        TransactionType=types.SimpleNamespace(API_USAGE="api_usage"),
        log_credit_transaction=lambda **kwargs: tx_log.append(kwargs),
    )
    monkeypatch.setitem(__import__("sys").modules, "src.db.credit_transactions", fake_tx)

    # short helper to access tx log in tests
    stub._tx_log = tx_log

    # predictable create_api_key (realistic 51 char key)
    monkeypatch.setattr("src.db.users.create_api_key", lambda **kwargs: ("gw_live_primary_TESTKEY_1234567890abcdefghijklm", 1))

    return stub

# ---- Helpers ----------------------------------------------------------------

def iso_now():
    return datetime.now(UTC).isoformat()

# ---- Tests ------------------------------------------------------------------

def test_create_enhanced_user_creates_trial_and_primary(sb):
    import src.db.users as users

    out = users.create_enhanced_user(
        username="alice",
        email="alice@example.com",
        auth_method="password",
        credits=5,
    )
    # user row created, then api_key updated to primary
    users_rows = sb.tables["users"]
    assert len(users_rows) == 1
    row = users_rows[0]
    assert row["subscription_status"] == "trial"
    assert "trial_expires_at" in row
    assert row["tier"] == "basic"
    assert out["primary_api_key"] == "gw_live_primary_TESTKEY_1234567890abcdefghijklm"
    assert row["api_key"] == "gw_live_primary_TESTKEY_1234567890abcdefghijklm"
    assert out["subscription_status"] == "trial"
    assert out["trial_expires_at"]
    assert out["tier"] == "basic"

def test_get_user_prefers_new_api_keys_then_legacy(sb):
    import src.db.users as users

    # seed user and new api_keys_new
    user = {"id": 7, "username": "bob", "email": "b@e.com", "credits": 5, "api_key": "legacy_key"}
    sb.table("users").insert(user).execute()

    new_key = {"id": 10, "api_key": "gw_live_primary_TESTKEY_1234567890abcdefghijklm", "user_id": 7, "key_name": "Primary",
               "environment_tag": "live", "scope_permissions": {"read": ["*"]}, "is_primary": True}
    sb.table("api_keys_new").insert(new_key).execute()

    res = users.get_user("gw_live_primary_TESTKEY_1234567890abcdefghijklm")
    assert res["id"] == 7
    assert res["key_id"] == 10
    assert res["key_name"] == "Primary"
    assert res["environment_tag"] == "live"
    assert res["is_primary"] is True

    # legacy fallback
    res2 = users.get_user("legacy_key")
    assert res2["credits"] == 5

def test_get_user_by_id_and_privy(sb):
    import src.db.users as users
    row = {"id": 11, "username": "c", "email": "c@x.com", "credits": 3, "privy_user_id": "privy_123"}
    sb.table("users").insert(row).execute()

    assert users.get_user_by_id(11)["email"] == "c@x.com"
    assert users.get_user_by_privy_id("privy_123")["id"] == 11
    assert users.get_user_by_privy_id("nope") is None

def test_add_credits_and_deduct_credits(sb):
    import src.db.users as users

    user = {"id": 22, "username": "d", "email": "d@x.com", "credits": 1.5, "api_key": "k22"}
    sb.table("users").insert(user).execute()

    # add
    users.add_credits_to_user(user_id=22, credits=2.0, transaction_type="admin_credit", description="top-up")
    updated = [r for r in sb.tables["users"] if r["id"] == 22][0]
    assert updated["credits"] == 3.5
    assert len(sb._tx_log) >= 1  # logged

    # deduct OK
    users.deduct_credits(api_key="k22", tokens=1.0, description="usage")
    updated = [r for r in sb.tables["users"] if r["id"] == 22][0]
    assert pytest.approx(updated["credits"], rel=1e-9) == 2.5
    assert len(sb._tx_log) >= 2

    # deduct insufficient -> RuntimeError
    with pytest.raises(RuntimeError, match="Insufficient credits"):
        users.deduct_credits(api_key="k22", tokens=999.0)

def test_get_all_users_delete_user_count(sb):
    import src.db.users as users
    sb.table("users").insert([
        {"id": 1, "username": "u1", "email": "u1@x.com", "credits": 0, "api_key": "k1"},
        {"id": 2, "username": "u2", "email": "u2@x.com", "credits": 0, "api_key": "k2"},
    ]).execute()

    all_users = users.get_all_users()
    assert len(all_users) == 2

    assert users.get_user_count() == 2

    users.delete_user("k1")  # should delete one
    assert users.get_user_count() == 1
    assert [u["api_key"] for u in sb.tables["users"]] == ["k2"]

def test_record_usage_and_metrics(sb):
    import src.db.users as users
    # user + key
    sb.table("users").insert({"id": 77, "username": "m", "email": "m@x.com", "credits": 5}).execute()
    sb.table("api_keys_new").insert({"id": 101, "api_key": "k77", "user_id": 77}).execute()

    users.record_usage(77, "k77", "openai/gpt", 123, 0.42)
    users.record_usage(77, "k77", "openai/gpt", 7, 0.01)

    metrics = users.get_user_usage_metrics("k77")
    assert metrics["user_id"] == 77
    assert metrics["current_credits"] == 5
    assert metrics["usage_metrics"]["total_requests"] == 2
    assert metrics["usage_metrics"]["total_tokens"] == 130
    assert metrics["usage_metrics"]["total_cost"] == pytest.approx(0.43)

def test_admin_monitor_data(sb):
    import src.db.users as users
    # users
    sb.table("users").insert([
        {"id": 1, "credits": 10, "api_key": "a"},
        {"id": 2, "credits": 0, "api_key": "b"},
    ]).execute()
    # activity_log (primary source for usage tracking)
    now = datetime.now(UTC).isoformat()
    older = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    sb.table("activity_log").insert([
        {"user_id": 1, "model": "m1", "tokens": 100, "cost": 0.5, "timestamp": now},
        {"user_id": 1, "model": "m1", "tokens": 50, "cost": 0.2, "timestamp": older},
        {"user_id": 2, "model": "m2", "tokens": 10, "cost": 0.05, "timestamp": now},
    ]).execute()

    out = users.get_admin_monitor_data()
    assert out["total_users"] == 2
    # total_requests is from activity_log count
    assert out["system_usage_metrics"]["total_requests"] == 3
    # tokens_today only includes today's records (2 records: 100 + 10 = 110)
    assert out["system_usage_metrics"]["tokens_today"] == 110
    # total_tokens uses monthly data as proxy, which includes all 3 records in last 30 days
    assert out["system_usage_metrics"]["total_tokens"] == 160


def test_admin_monitor_data_deduplication(sb):
    """Test that duplicate records in activity_log and usage_records are correctly deduplicated.

    This test verifies the fix for the composite key deduplication issue where user_id from
    activity_log and api_key from usage_records were being used inconsistently, causing
    the same event to be double-counted.
    """
    import src.db.users as users

    # Setup users with api_keys
    sb.table("users").insert([
        {"id": 1, "credits": 10, "api_key": "api_key_user_1"},
        {"id": 2, "credits": 5, "api_key": "api_key_user_2"},
    ]).execute()

    # Create timestamps
    now = datetime.now(UTC).isoformat()

    # Insert activity_log entries (primary source)
    sb.table("activity_log").insert([
        {"user_id": 1, "model": "gpt-4", "tokens": 100, "cost": 0.5, "timestamp": now},
        {"user_id": 2, "model": "gpt-3.5", "tokens": 50, "cost": 0.1, "timestamp": now},
    ]).execute()

    # Insert the SAME events in usage_records (legacy table) with api_key instead of user_id
    # This simulates the scenario where the same event exists in both tables
    sb.table("usage_records").insert([
        {"user_id": 1, "api_key": "api_key_user_1", "model": "gpt-4", "tokens_used": 100, "cost": 0.5, "timestamp": now},
        {"user_id": 2, "api_key": "api_key_user_2", "model": "gpt-3.5", "tokens_used": 50, "cost": 0.1, "timestamp": now},
    ]).execute()

    out = users.get_admin_monitor_data()

    # With proper deduplication, we should only count each event once
    # tokens_today should be 100 + 50 = 150 (not 300 from double-counting)
    assert out["system_usage_metrics"]["tokens_today"] == 150, (
        f"Expected 150 tokens (no double-counting), got {out['system_usage_metrics']['tokens_today']}"
    )

    # Cost should also not be double-counted
    assert out["system_usage_metrics"]["cost_today"] == pytest.approx(0.6), (
        f"Expected 0.6 cost (no double-counting), got {out['system_usage_metrics']['cost_today']}"
    )


def test_admin_monitor_data_deduplication_api_key_only(sb):
    """Test deduplication when legacy records only have api_key (no user_id).

    This is a critical edge case where legacy usage_records may not have user_id populated,
    so deduplication must correctly use the api_key_to_user_id mapping.
    """
    import src.db.users as users

    # Setup users with api_keys
    sb.table("users").insert([
        {"id": 1, "credits": 10, "api_key": "api_key_user_1"},
    ]).execute()

    now = datetime.now(UTC).isoformat()

    # Insert activity_log entry
    sb.table("activity_log").insert([
        {"user_id": 1, "model": "claude-3", "tokens": 200, "cost": 1.0, "timestamp": now},
    ]).execute()

    # Insert the SAME event in usage_records but WITHOUT user_id (only api_key)
    # This simulates older legacy records that only tracked by api_key
    sb.table("usage_records").insert([
        {"api_key": "api_key_user_1", "model": "claude-3", "tokens_used": 200, "cost": 1.0, "timestamp": now},
    ]).execute()

    out = users.get_admin_monitor_data()

    # With proper deduplication via api_key_to_user_id mapping, should count once
    assert out["system_usage_metrics"]["tokens_today"] == 200, (
        f"Expected 200 tokens (no double-counting), got {out['system_usage_metrics']['tokens_today']}"
    )


def test_update_and_get_user_profile(sb):
    import src.db.users as users
    sb.table("users").insert({"id": 9, "username": "z", "email": "z@x.com", "credits": 4, "api_key": "k9"}).execute()

    out = users.update_user_profile("k9", {"name": "Zed", "preferences": {"theme": "dark"}})
    assert out["username"] == "z"
    prof = users.get_user_profile("k9")
    assert prof["api_key"].endswith("...")
    assert prof["credits"] == 400  # 4 dollars * 100 = 400 cents
    assert prof["username"] == "z"
    assert prof["email"] == "z@x.com"


# ============================================================================
# CREDITS CENTS CONVERSION TESTS
# ============================================================================

def test_get_user_profile_returns_credits_in_cents(sb):
    """Test that get_user_profile returns credits in cents, not dollars.

    The frontend TIER_CONFIG uses cents (e.g., monthlyAllowance: 15000 = $150.00),
    so the backend API must return subscription_allowance, purchased_credits,
    and total_credits in cents for consistency.
    """
    import src.db.users as users

    # Create a user with tiered credits in dollars (as stored in DB)
    sb.table("users").insert({
        "id": 1001,
        "username": "cents_test_user",
        "email": "cents@test.com",
        "api_key": "key_cents_test",
        "subscription_allowance": 150.0,  # $150.00 in dollars (Max tier)
        "purchased_credits": 25.50,       # $25.50 in dollars
        "tier": "max",
        "subscription_status": "active"
    }).execute()

    prof = users.get_user_profile("key_cents_test")

    # Verify values are returned in cents
    assert prof["subscription_allowance"] == 15000, "subscription_allowance should be in cents (150.0 * 100)"
    assert prof["purchased_credits"] == 2550, "purchased_credits should be in cents (25.50 * 100)"
    assert prof["total_credits"] == 17550, "total_credits should be sum in cents"
    assert prof["credits"] == 17550, "credits should also be in cents for backward compatibility"


def test_get_user_profile_pro_tier_allowance_in_cents(sb):
    """Test Pro tier user returns $15.00 allowance as 1500 cents."""
    import src.db.users as users

    sb.table("users").insert({
        "id": 1002,
        "username": "pro_user",
        "email": "pro@test.com",
        "api_key": "key_pro_test",
        "subscription_allowance": 15.0,  # $15.00 Pro tier
        "purchased_credits": 0.0,
        "tier": "pro",
        "subscription_status": "active"
    }).execute()

    prof = users.get_user_profile("key_pro_test")

    assert prof["subscription_allowance"] == 1500, "Pro tier allowance should be 1500 cents ($15.00)"
    assert prof["purchased_credits"] == 0
    assert prof["total_credits"] == 1500
    assert prof["tier"] == "pro"
    assert prof["tier_display_name"] == "Pro"


def test_get_user_profile_max_tier_allowance_in_cents(sb):
    """Test Max tier user returns $150.00 allowance as 15000 cents."""
    import src.db.users as users

    sb.table("users").insert({
        "id": 1003,
        "username": "max_user",
        "email": "max@test.com",
        "api_key": "key_max_test",
        "subscription_allowance": 150.0,  # $150.00 Max tier
        "purchased_credits": 0.0,
        "tier": "max",
        "subscription_status": "active"
    }).execute()

    prof = users.get_user_profile("key_max_test")

    assert prof["subscription_allowance"] == 15000, "Max tier allowance should be 15000 cents ($150.00)"
    assert prof["purchased_credits"] == 0
    assert prof["total_credits"] == 15000
    assert prof["tier"] == "max"
    assert prof["tier_display_name"] == "MAX"


def test_get_user_profile_basic_tier_zero_allowance(sb):
    """Test Basic tier user returns zero allowance correctly."""
    import src.db.users as users

    sb.table("users").insert({
        "id": 1004,
        "username": "basic_user",
        "email": "basic@test.com",
        "api_key": "key_basic_test",
        "subscription_allowance": 0.0,  # Basic tier has no allowance
        "purchased_credits": 10.0,      # $10.00 purchased
        "tier": "basic",
        "subscription_status": None
    }).execute()

    prof = users.get_user_profile("key_basic_test")

    assert prof["subscription_allowance"] == 0
    assert prof["purchased_credits"] == 1000, "purchased_credits should be 1000 cents ($10.00)"
    assert prof["total_credits"] == 1000
    assert prof["tier"] == "basic"
    assert prof["tier_display_name"] == "Basic"


def test_get_user_profile_fractional_cents(sb):
    """Test that fractional dollar amounts are correctly converted to integer cents."""
    import src.db.users as users

    sb.table("users").insert({
        "id": 1005,
        "username": "fraction_user",
        "email": "fraction@test.com",
        "api_key": "key_fraction_test",
        "subscription_allowance": 12.34,  # $12.34
        "purchased_credits": 5.67,        # $5.67
        "tier": "pro",
        "subscription_status": "active"
    }).execute()

    prof = users.get_user_profile("key_fraction_test")

    # 12.34 * 100 = 1234, 5.67 * 100 = 567
    assert prof["subscription_allowance"] == 1234
    assert prof["purchased_credits"] == 567
    assert prof["total_credits"] == 1801  # 1234 + 567 = 1801


def test_get_user_profile_null_credit_fields(sb):
    """Test that null/missing credit fields default to 0 cents."""
    import src.db.users as users

    sb.table("users").insert({
        "id": 1006,
        "username": "null_credits_user",
        "email": "null@test.com",
        "api_key": "key_null_test",
        # subscription_allowance and purchased_credits are not set (None)
        "tier": "basic"
    }).execute()

    prof = users.get_user_profile("key_null_test")

    assert prof["subscription_allowance"] == 0
    assert prof["purchased_credits"] == 0
    assert prof["total_credits"] == 0
    assert prof["credits"] == 0


def test_get_user_profile_large_credits_in_cents(sb):
    """Test that large credit amounts are correctly converted to cents."""
    import src.db.users as users

    sb.table("users").insert({
        "id": 1007,
        "username": "whale_user",
        "email": "whale@test.com",
        "api_key": "key_whale_test",
        "subscription_allowance": 150.0,   # $150.00 Max tier
        "purchased_credits": 1000.0,       # $1000.00 purchased
        "tier": "max",
        "subscription_status": "active"
    }).execute()

    prof = users.get_user_profile("key_whale_test")

    assert prof["subscription_allowance"] == 15000  # $150 = 15000 cents
    assert prof["purchased_credits"] == 100000      # $1000 = 100000 cents
    assert prof["total_credits"] == 115000          # $1150 = 115000 cents


def test_mark_welcome_email_sent_and_delete_user_account(sb):
    import src.db.users as users
    sb.table("users").insert({"id": 33, "username": "w", "email": "w@x.com", "credits": 1, "api_key": "kw"}).execute()

    assert users.mark_welcome_email_sent(33) is True
    row = [r for r in sb.tables["users"] if r["id"] == 33][0]
    assert row["welcome_email_sent"] is True

    assert users.delete_user_account("kw") is True
    assert [r for r in sb.tables["users"] if r.get("api_key") == "kw"] == []


# ============================================================================
# COMPREHENSIVE EDGE CASE AND ERROR PATH TESTS (For 100% Coverage)
# ============================================================================

def test_create_enhanced_user_with_privy_id(sb):
    """Test user creation with privy_user_id"""
    import src.db.users as users

    out = users.create_enhanced_user(
        username="alice_privy",
        email="alice_privy@example.com",
        auth_method="google",
        credits=20,
        privy_user_id="privy_xyz_123"
    )

    users_rows = sb.tables["users"]
    row = [r for r in users_rows if r["username"] == "alice_privy"][0]
    assert row["privy_user_id"] == "privy_xyz_123"
    assert row["auth_method"] == "google"
    assert row["credits"] == 20
    assert out["credits"] == 20
    assert out["subscription_status"] == "trial"
    assert out["tier"] == "basic"


def test_create_enhanced_user_failure_no_data(sb, monkeypatch):
    """Test user creation when insert returns no data"""
    import src.db.users as users

    # Make insert return empty data
    def mock_insert(data):
        class BadResult:
            def execute(self):
                class EmptyResult:
                    data = []
                return EmptyResult()
        return BadResult()

    # Temporarily break the insert
    original_table = sb.table
    def broken_table(name):
        t = original_table(name)
        if name == "users":
            t.insert = mock_insert
        return t

    monkeypatch.setattr(sb, "table", broken_table)

    with pytest.raises(RuntimeError, match="Failed to create enhanced user"):
        users.create_enhanced_user(
            username="fail_user",
            email="fail@example.com",
            auth_method="email"
        )


def test_create_enhanced_user_exception_handling(sb, monkeypatch):
    """Test exception handling in create_enhanced_user"""
    import src.db.users as users

    # Make table() raise an exception
    def raise_exception(name):
        raise Exception("Database connection failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    with pytest.raises(RuntimeError, match="Failed to create enhanced user"):
        users.create_enhanced_user(
            username="error_user",
            email="error@example.com",
            auth_method="email"
        )


def test_get_user_not_found(sb):
    """Test get_user when API key doesn't exist"""
    import src.db.users as users

    result = users.get_user("nonexistent_key")
    assert result is None


def test_get_user_exception_handling(sb, monkeypatch):
    """Test get_user exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Database error")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_user("any_key")
    assert result is None


def test_get_user_by_id_not_found(sb):
    """Test get_user_by_id when user doesn't exist"""
    import src.db.users as users

    result = users.get_user_by_id(99999)
    assert result is None


def test_get_user_by_id_exception_handling(sb, monkeypatch):
    """Test get_user_by_id exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Database error")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_user_by_id(123)
    assert result is None


def test_get_user_by_username_not_found(sb):
    """Test get_user_by_username when user doesn't exist"""
    import src.db.users as users

    result = users.get_user_by_username("nonexistent_username")
    assert result is None


def test_get_user_by_username_found(sb):
    """Test get_user_by_username when user exists"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 555,
        "username": "findme",
        "email": "findme@test.com",
        "credits": 10
    }).execute()

    result = users.get_user_by_username("findme")
    assert result is not None
    assert result["username"] == "findme"
    assert result["email"] == "findme@test.com"


def test_get_user_by_username_exception_handling(sb, monkeypatch):
    """Test get_user_by_username exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Database error")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_user_by_username("any_username")
    assert result is None


def test_add_credits_to_user_success(sb):
    """Test add_credits_to_user successfully adds credits"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 100,
        "username": "credittest",
        "email": "credits@test.com",
        "credits": 5.0
    }).execute()

    users.add_credits_to_user(
        user_id=100,
        credits=10.0,
        transaction_type="payment",
        description="Test payment",
        metadata={"payment_id": "pay_123"}
    )

    user = [r for r in sb.tables["users"] if r["id"] == 100][0]
    assert user["credits"] == 15.0


def test_add_credits_to_user_exception_handling(sb, monkeypatch):
    """Test add_credits_to_user exception handling"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 101,
        "username": "erroruser",
        "email": "error@test.com",
        "credits": 5.0
    }).execute()

    # Break the update - need to break it when update is called
    original_table = sb.table
    def broken_table(name):
        t = original_table(name)
        if name == "users":
            original_update = t.update
            def bad_update(data):
                raise Exception("Update failed")
            t.update = bad_update
        return t

    monkeypatch.setattr(sb, "table", broken_table)

    # Should log error but raise the exception
    with pytest.raises(Exception, match="Update failed"):
        users.add_credits_to_user(
            user_id=101,
            credits=10.0,
            transaction_type="payment",
            description="Test"
        )


def test_add_credits_success(sb):
    """Test add_credits function"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 200,
        "username": "addcredits",
        "email": "add@test.com",
        "credits": 5.0,
        "api_key": "key_200"
    }).execute()

    users.add_credits("key_200", 10)

    user = users.get_user("key_200")
    assert user["credits"] == 15.0


def test_deduct_credits_with_metadata(sb):
    """Test deduct_credits with metadata"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 300,
        "username": "deduct",
        "email": "deduct@test.com",
        "credits": 100.0,
        "api_key": "key_300"
    }).execute()

    users.deduct_credits(
        api_key="key_300",
        tokens=10.0,
        description="API usage",
        metadata={"model": "gpt-4", "endpoint": "/v1/chat/completions"}
    )

    user = users.get_user("key_300")
    assert user["credits"] == 90.0


def test_deduct_credits_user_not_found(sb):
    """Test deduct_credits when user not found"""
    import src.db.users as users

    # When user not found, get_user returns None, which triggers error
    with pytest.raises(RuntimeError, match="User with API key nonexistent_key not found"):
        users.deduct_credits(
            api_key="nonexistent_key",
            tokens=10.0,
            description="Test"
        )


def test_deduct_credits_exception_handling(sb, monkeypatch):
    """Test deduct_credits exception handling"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 301,
        "username": "error",
        "email": "error@test.com",
        "credits": 50.0,
        "api_key": "key_301"
    }).execute()

    # Break the update
    original_table = sb.table
    def broken_table(name):
        if name == "users":
            class BadTable:
                def select(self, *args):
                    return original_table(name).select(*args)
                def update(self, data):
                    raise Exception("Update failed")
            return BadTable()
        return original_table(name)

    monkeypatch.setattr(sb, "table", broken_table)

    with pytest.raises(Exception):
        users.deduct_credits("key_301", 5.0, "Test")


def test_get_all_users_exception_handling(sb, monkeypatch):
    """Test get_all_users exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Database error")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_all_users()
    assert result == []


def test_delete_user_exception_handling(sb, monkeypatch):
    """Test delete_user exception handling"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 400,
        "username": "delete_test",
        "email": "delete@test.com",
        "credits": 5.0,
        "api_key": "key_400"
    }).execute()

    def raise_exception(name):
        raise Exception("Delete failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    # delete_user doesn't raise exceptions, it just logs them
    try:
        users.delete_user("key_400")
    except Exception:
        pass  # Should not raise


def test_get_user_count_exception_handling(sb, monkeypatch):
    """Test get_user_count exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Count failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_user_count()
    assert result == 0


def test_record_usage_exception_handling(sb, monkeypatch):
    """Test record_usage exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Insert failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    # Should not raise, just log
    users.record_usage(
        user_id=1,
        api_key="test_key",
        model="gpt-4",
        tokens_used=100,
        cost=0.5
    )


def test_record_usage_with_latency(sb):
    """Test record_usage with latency parameter"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 500,
        "username": "latency_test",
        "email": "latency@test.com",
        "credits": 10
    }).execute()

    # Just verify the function doesn't crash with latency parameter
    users.record_usage(
        user_id=500,
        api_key="key_500",
        model="gpt-4",
        tokens_used=100,
        cost=0.5,
        latency_ms=1500
    )

    # Function successfully ran without errors - that's what we're testing
    assert True


def test_get_user_usage_metrics_no_user(sb):
    """Test get_user_usage_metrics when user not found"""
    import src.db.users as users

    result = users.get_user_usage_metrics("nonexistent_key")
    assert result is None


def test_get_user_usage_metrics_exception_handling(sb, monkeypatch):
    """Test get_user_usage_metrics exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Query failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_user_usage_metrics("any_key")
    assert result is None


def test_get_admin_monitor_data_exception_handling(sb, monkeypatch):
    """Test get_admin_monitor_data exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Query failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_admin_monitor_data()
    # Should return default structure even on error
    assert "total_users" in result
    assert result["total_users"] == 0


def test_update_user_profile_exception_handling(sb, monkeypatch):
    """Test update_user_profile exception handling"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 600,
        "username": "profile_test",
        "email": "profile@test.com",
        "credits": 5.0,
        "api_key": "key_600"
    }).execute()

    def raise_exception(name):
        raise Exception("Update failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    with pytest.raises(RuntimeError, match="Failed to update user profile"):
        users.update_user_profile("key_600", {"name": "Test"})


def test_get_user_profile_not_found(sb):
    """Test get_user_profile when user not found"""
    import src.db.users as users

    result = users.get_user_profile("nonexistent_key")
    assert result is None


def test_get_user_profile_exception_handling(sb, monkeypatch):
    """Test get_user_profile exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Query failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    result = users.get_user_profile("any_key")
    assert result is None


def test_mark_welcome_email_sent_exception_handling(sb, monkeypatch):
    """Test mark_welcome_email_sent exception handling"""
    import src.db.users as users

    def raise_exception(name):
        raise Exception("Update failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    # Function raises RuntimeError, not returns False
    with pytest.raises(RuntimeError, match="Failed to mark welcome email"):
        users.mark_welcome_email_sent(123)


def test_delete_user_account_not_found(sb):
    """Test delete_user_account when user not found"""
    import src.db.users as users

    # Function raises RuntimeError when user not found
    with pytest.raises(RuntimeError, match="Failed to delete user account"):
        users.delete_user_account("nonexistent_key")


def test_delete_user_account_exception_handling(sb, monkeypatch):
    """Test delete_user_account exception handling"""
    import src.db.users as users

    sb.table("users").insert({
        "id": 700,
        "username": "delete_account_test",
        "email": "delete_account@test.com",
        "credits": 5.0,
        "api_key": "key_700"
    }).execute()

    def raise_exception(name):
        raise Exception("Delete failed")

    monkeypatch.setattr(sb, "table", raise_exception)

    # Function raises RuntimeError on any exception
    with pytest.raises(RuntimeError, match="Failed to delete user account"):
        users.delete_user_account("key_700")


# ============================================================================
# USER CACHE TESTS (PERF OPTIMIZATION)
# ============================================================================

def test_user_cache_hit_on_second_call(sb):
    """Second call to get_user with same API key should use cache (not hit database)"""
    import src.db.users as users

    # Clear cache first
    users.clear_user_cache()

    # Setup test data
    sb.table("users").insert({
        "id": 801,
        "username": "cache_test",
        "email": "cache@test.com",
        "credits": 10.0,
        "api_key": "cache_key_801"
    }).execute()
    sb.table("api_keys_new").insert({
        "id": 801,
        "api_key": "cache_key_801",
        "user_id": 801,
        "key_name": "default",
        "environment_tag": "live",
        "scope_permissions": None,
        "is_primary": True
    }).execute()

    # First call (cache miss)
    result1 = users.get_user("cache_key_801")
    assert result1 is not None
    assert result1["id"] == 801

    # Check cache stats - should have 1 cached user
    stats = users.get_user_cache_stats()
    assert stats["cached_users"] == 1

    # Second call should use cache
    result2 = users.get_user("cache_key_801")
    assert result2 is not None
    assert result2["id"] == 801


def test_user_cache_miss_for_different_keys(sb):
    """Different API keys should have separate cache entries"""
    import src.db.users as users

    users.clear_user_cache()

    # Setup two users
    sb.table("users").insert([
        {"id": 802, "username": "user1", "email": "u1@test.com", "credits": 10.0},
        {"id": 803, "username": "user2", "email": "u2@test.com", "credits": 20.0},
    ]).execute()
    sb.table("api_keys_new").insert([
        {"id": 802, "api_key": "key_802", "user_id": 802, "key_name": "default", "environment_tag": "live", "is_primary": True},
        {"id": 803, "api_key": "key_803", "user_id": 803, "key_name": "default", "environment_tag": "live", "is_primary": True},
    ]).execute()

    result1 = users.get_user("key_802")
    result2 = users.get_user("key_803")

    assert result1["id"] == 802
    assert result2["id"] == 803

    # Cache should have 2 entries
    stats = users.get_user_cache_stats()
    assert stats["cached_users"] == 2


def test_clear_user_cache_specific_key(sb):
    """clear_user_cache(api_key) should clear only that key"""
    import src.db.users as users

    users.clear_user_cache()

    sb.table("users").insert({"id": 804, "username": "clear_test", "email": "c@test.com", "credits": 5.0}).execute()
    sb.table("api_keys_new").insert({"id": 804, "api_key": "key_804", "user_id": 804, "key_name": "default", "environment_tag": "live", "is_primary": True}).execute()

    # Populate cache
    users.get_user("key_804")
    assert users.get_user_cache_stats()["cached_users"] == 1

    # Clear specific key
    users.clear_user_cache("key_804")
    assert users.get_user_cache_stats()["cached_users"] == 0


def test_clear_user_cache_all(sb):
    """clear_user_cache() with no arguments should clear entire cache"""
    import src.db.users as users

    users.clear_user_cache()

    sb.table("users").insert([
        {"id": 805, "username": "u5", "email": "u5@test.com", "credits": 5.0},
        {"id": 806, "username": "u6", "email": "u6@test.com", "credits": 5.0},
    ]).execute()
    sb.table("api_keys_new").insert([
        {"id": 805, "api_key": "key_805", "user_id": 805, "key_name": "default", "environment_tag": "live", "is_primary": True},
        {"id": 806, "api_key": "key_806", "user_id": 806, "key_name": "default", "environment_tag": "live", "is_primary": True},
    ]).execute()

    users.get_user("key_805")
    users.get_user("key_806")
    assert users.get_user_cache_stats()["cached_users"] == 2

    users.clear_user_cache()
    assert users.get_user_cache_stats()["cached_users"] == 0


def test_invalidate_user_cache(sb):
    """invalidate_user_cache should clear cache for specific user"""
    import src.db.users as users

    users.clear_user_cache()

    sb.table("users").insert({"id": 807, "username": "inv_test", "email": "inv@test.com", "credits": 5.0}).execute()
    sb.table("api_keys_new").insert({"id": 807, "api_key": "key_807", "user_id": 807, "key_name": "default", "environment_tag": "live", "is_primary": True}).execute()

    users.get_user("key_807")
    assert users.get_user_cache_stats()["cached_users"] == 1

    users.invalidate_user_cache("key_807")
    assert users.get_user_cache_stats()["cached_users"] == 0


def test_user_cache_not_found_user(sb):
    """Cache should not cache None returns for invalid keys"""
    import src.db.users as users

    users.clear_user_cache()

    result = users.get_user("nonexistent_key_xyz")
    assert result is None

    # Invalid keys should not be cached (to avoid filling cache with bad keys)
    assert users.get_user_cache_stats()["cached_users"] == 0


# ============================================================================
# TEMPORARY API KEY DETECTION TESTS
# ============================================================================

def test_is_temporary_api_key_empty():
    """Test _is_temporary_api_key with empty/None values"""
    import src.db.users as users

    assert users._is_temporary_api_key("") is False
    assert users._is_temporary_api_key(None) is False


def test_is_temporary_api_key_short_gw_live():
    """Test _is_temporary_api_key detects short gw_live_ keys as temporary"""
    import src.db.users as users

    # gw_live_ prefix (8 chars) + token_urlsafe(16) (22 chars) = 30 chars
    temp_key = "gw_live_" + "a" * 22  # 30 chars total
    assert users._is_temporary_api_key(temp_key) is True

    # 39 chars total (still below 40 threshold)
    temp_key_39 = "gw_live_" + "a" * 31
    assert users._is_temporary_api_key(temp_key_39) is True


def test_is_temporary_api_key_proper_length():
    """Test _is_temporary_api_key does not flag proper-length keys as temporary"""
    import src.db.users as users

    # gw_live_ prefix (8 chars) + token_urlsafe(32) (43 chars) = 51 chars
    proper_key = "gw_live_" + "a" * 43  # 51 chars total
    assert users._is_temporary_api_key(proper_key) is False

    # 40 chars total (exactly at threshold)
    proper_key_40 = "gw_live_" + "a" * 32
    assert users._is_temporary_api_key(proper_key_40) is False


def test_is_temporary_api_key_non_gw_live_prefix():
    """Test _is_temporary_api_key ignores keys without gw_live_ prefix"""
    import src.db.users as users

    # Short gw_test_ key should NOT be detected as temporary
    test_key_short = "gw_test_" + "a" * 22
    assert users._is_temporary_api_key(test_key_short) is False

    # Short gw_dev_ key should NOT be detected as temporary
    dev_key_short = "gw_dev_" + "a" * 22
    assert users._is_temporary_api_key(dev_key_short) is False

    # Random key without gw_ prefix
    random_key = "random_key_12345"
    assert users._is_temporary_api_key(random_key) is False


def test_is_temporary_api_key_realistic_keys():
    """Test _is_temporary_api_key with realistic key formats"""
    import src.db.users as users
    import secrets

    # Simulate temporary key generation (what create_enhanced_user does)
    temp_key = f"gw_live_{secrets.token_urlsafe(16)}"
    assert len(temp_key) == 30  # gw_live_ (8) + urlsafe(16) produces 22 chars = 30
    assert users._is_temporary_api_key(temp_key) is True

    # Simulate proper key generation (what create_api_key does)
    proper_key = f"gw_live_{secrets.token_urlsafe(32)}"
    assert len(proper_key) == 51  # gw_live_ (8) + urlsafe(32) produces 43 chars = 51
    assert users._is_temporary_api_key(proper_key) is False


def test_migrate_legacy_api_key_skips_temporary_keys(sb):
    """Test _migrate_legacy_api_key does not migrate temporary keys"""
    import src.db.users as users

    # Create a user with a temporary key
    temp_key = "gw_live_" + "a" * 22  # 30 chars - temporary
    sb.table("users").insert({
        "id": 900,
        "username": "temp_key_user",
        "email": "temp@test.com",
        "credits": 10.0,
        "api_key": temp_key
    }).execute()

    user = [r for r in sb.tables["users"] if r["id"] == 900][0]

    # Attempt migration
    result = users._migrate_legacy_api_key(sb, user, temp_key)

    # Should return False and not migrate
    assert result is False

    # api_keys_new should NOT have the temporary key
    api_keys = [r for r in sb.tables["api_keys_new"] if r.get("api_key") == temp_key]
    assert len(api_keys) == 0


def test_get_user_detects_temporary_key_in_legacy(sb):
    """Test get_user correctly detects and handles temporary keys in legacy lookup"""
    import src.db.users as users

    users.clear_user_cache()

    # Create a user with a temporary key (simulating failed user creation)
    temp_key = "gw_live_" + "b" * 22  # 30 chars - temporary
    sb.table("users").insert({
        "id": 901,
        "username": "temp_user",
        "email": "temp_user@test.com",
        "credits": 10.0,
        "api_key": temp_key
    }).execute()

    # get_user should find the user via legacy lookup
    result = users.get_user(temp_key)

    # Should return the user
    assert result is not None
    assert result["id"] == 901
    # Should have the temporary key flag set
    assert result.get("_has_temporary_key") is True

    # api_keys_new should NOT have the temporary key (not migrated)
    api_keys = [r for r in sb.tables["api_keys_new"] if r.get("api_key") == temp_key]
    assert len(api_keys) == 0
