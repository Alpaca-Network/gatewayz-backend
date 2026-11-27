"""Stage 2 of removing plaintext API keys: hash lookups, no plaintext writes.

Covers src/db/api_key_lookup.py and the call sites that must no longer
store or log the plaintext key (create_api_key, rate_limit_usage).
"""

import logging
from types import SimpleNamespace

import pytest
from postgrest import APIError

from src.db import api_key_lookup, api_keys, rate_limits
from src.utils.crypto import sha256_key_hash

SALT = "0123456789abcdef0123456789abcdef"
KEY = "gw_live_" + "x" * 39 + "0001"  # fake key, 51 chars like a real one


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.filters: list[tuple[str, object]] = []
        self.op = "select"
        self.payload = None

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append((column, tuple(values)))
        return self

    def limit(self, *_a):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        self.client.calls.append(self)
        if self.op == "insert" and self.client.insert_error is not None:
            raise self.client.insert_error
        return SimpleNamespace(data=self.client.respond(self))


class _Client:
    """Records every query; `rows` maps (table, column) -> rows to return."""

    def __init__(self, rows=None, insert_error=None):
        self.rows = rows or {}
        self.calls: list[_Query] = []
        self.insert_error = insert_error

    def table(self, name):
        return _Query(self, name)

    def respond(self, query):
        if query.op == "insert":
            return [{"id": 99, **query.payload}]
        for column, _value in query.filters:
            if (query.table, column) in self.rows:
                return self.rows[(query.table, column)]
        return []

    def filter_columns(self, table):
        return [c for q in self.calls if q.table == table for c, _ in q.filters]

    def all_values(self):
        values = []
        for q in self.calls:
            values += [v for _, v in q.filters]
            if isinstance(q.payload, dict):
                values += list(q.payload.values())
        return values


@pytest.fixture(autouse=True)
def _salt(monkeypatch):
    monkeypatch.setenv("KEY_HASH_SALT", SALT)
    monkeypatch.delenv(api_key_lookup.FALLBACK_ENV_VAR, raising=False)


def _no_key_in_logs(caplog):
    assert KEY not in caplog.text
    assert KEY[8:20] not in caplog.text


class TestFindKeyRow:
    def test_hash_hit_never_queries_plaintext(self, caplog):
        client = _Client({("api_keys_new", "key_hash"): [{"id": 1, "user_id": 7}]})
        with caplog.at_level(logging.DEBUG):
            row = api_key_lookup.find_key_row(client, KEY)
        assert row == {"id": 1, "user_id": 7}
        assert client.filter_columns("api_keys_new") == ["key_hash"]
        assert client.calls[0].filters == [("key_hash", sha256_key_hash(KEY))]
        assert "api_key_plaintext_fallback" not in caplog.text
        _no_key_in_logs(caplog)

    def test_fallback_hit_when_flag_on_logs_row_id_only(self, caplog):
        client = _Client({("api_keys_new", "api_key"): [{"id": 42, "user_id": 7}]})
        with caplog.at_level(logging.WARNING):
            row = api_key_lookup.find_key_row(client, KEY)
        assert row["id"] == 42
        assert client.filter_columns("api_keys_new") == ["key_hash", "api_key"]
        assert "api_key_plaintext_fallback" in caplog.text
        assert "42" in caplog.text
        _no_key_in_logs(caplog)

    def test_no_fallback_when_flag_off(self, monkeypatch, caplog):
        monkeypatch.setenv(api_key_lookup.FALLBACK_ENV_VAR, "false")
        client = _Client({("api_keys_new", "api_key"): [{"id": 42}]})
        with caplog.at_level(logging.DEBUG):
            assert api_key_lookup.find_key_row(client, KEY) is None
        assert client.filter_columns("api_keys_new") == ["key_hash"]
        _no_key_in_logs(caplog)

    def test_extra_filters_apply_to_both_lookups(self):
        client = _Client()
        api_key_lookup.find_key_row(client, KEY, filters={"user_id": 5})
        assert all(("user_id", 5) in q.filters for q in client.calls)


class TestUserLookup:
    def test_resolves_through_api_keys_new_first(self):
        client = _Client(
            {
                ("api_keys_new", "key_hash"): [{"id": 1, "user_id": 7}],
                ("users", "id"): [{"id": 7, "email": "x"}],
            }
        )
        assert api_key_lookup.find_user_row(client, KEY)["id"] == 7
        assert "api_key" not in client.filter_columns("users")

    def test_legacy_users_column_only_behind_flag(self, monkeypatch, caplog):
        client = _Client({("users", "api_key"): [{"id": 8}]})
        with caplog.at_level(logging.WARNING):
            assert api_key_lookup.resolve_user_id(client, KEY) == 8
        assert "table=users" in caplog.text
        _no_key_in_logs(caplog)

        monkeypatch.setenv(api_key_lookup.FALLBACK_ENV_VAR, "0")
        client = _Client({("users", "api_key"): [{"id": 8}]})
        assert api_key_lookup.resolve_user_id(client, KEY) is None
        assert "api_key" not in client.filter_columns("users")


class TestRowHelpers:
    def test_plaintext_from_row_decrypts_when_column_is_null(self, monkeypatch):
        monkeypatch.setattr(api_key_lookup, "decrypt_api_key", lambda token, ver: f"dec:{token}")
        assert api_key_lookup.plaintext_from_row({"api_key": None, "encrypted_key": "t"}) == "dec:t"
        assert api_key_lookup.plaintext_from_row({"api_key": KEY}) == KEY
        assert api_key_lookup.plaintext_from_row({"api_key": None}) is None

    def test_key_label_is_not_secret(self):
        assert api_key_lookup.key_label({"id": 3, "last4": "abcd"}) == "...abcd"
        assert api_key_lookup.key_label({"id": 3, "api_key": KEY}) == "...0001"

    def test_storage_fields_never_include_plaintext(self, monkeypatch):
        monkeypatch.setattr(api_key_lookup, "encrypt_api_key", lambda k: ("ciphertext", 2))
        fields = api_key_lookup.new_key_storage_fields(KEY)
        assert fields["api_key"] is None
        assert fields["key_hash"] == sha256_key_hash(KEY)
        assert fields["encrypted_key"] == "ciphertext"
        assert fields["key_version"] == 2
        assert fields["last4"] == KEY[-4:]
        assert KEY not in fields.values()

    def test_storage_fields_require_encryption(self, monkeypatch):
        def _fail(_k):
            raise RuntimeError("no keyring")

        monkeypatch.setattr(api_key_lookup, "encrypt_api_key", _fail)
        with pytest.raises(ValueError):
            api_key_lookup.new_key_storage_fields(KEY)


class TestCreateApiKey:
    def _setup(self, monkeypatch, client):
        monkeypatch.setattr(api_keys, "get_supabase_client", lambda: client)
        monkeypatch.setattr(api_keys, "check_key_name_uniqueness", lambda *a, **k: True)
        monkeypatch.setattr(api_keys, "check_plan_entitlements", lambda *_: {})
        monkeypatch.setattr(api_key_lookup, "encrypt_api_key", lambda k: ("ciphertext", 1))

    def test_insert_payload_has_no_plaintext(self, monkeypatch):
        client = _Client()
        self._setup(monkeypatch, client)
        new_key, key_id = api_keys.create_api_key(user_id=5, key_name="k")
        assert key_id == 99
        payload = next(q.payload for q in client.calls if q.table == "api_keys_new")
        assert payload["api_key"] is None
        assert payload["key_hash"] == sha256_key_hash(new_key)
        assert payload["encrypted_key"] == "ciphertext"
        assert payload["last4"] == new_key[-4:]
        assert new_key not in payload.values()

    def test_insert_error_does_not_log_or_raise_the_key(self, monkeypatch, caplog):
        # PostgREST puts the failing row in `details`; that used to be logged.
        error = APIError(
            {
                "message": "duplicate key value violates unique constraint",
                "code": "23505",
                "details": f"Key (api_key)=({KEY}) already exists.",
            }
        )
        client = _Client(insert_error=error)
        self._setup(monkeypatch, client)
        with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as exc:
            api_keys.create_api_key(user_id=5, key_name="k")
        assert "23505" in str(exc.value)
        assert KEY not in str(exc.value)
        assert "already exists." not in caplog.text
        _no_key_in_logs(caplog)


class TestRateLimitUsageByHash:
    def test_writes_hash_and_placeholder_only(self, monkeypatch, caplog):
        client = _Client({("api_keys_new", "key_hash"): [{"id": 1}]})
        monkeypatch.setattr(rate_limits, "get_supabase_client", lambda: client)
        monkeypatch.setattr(rate_limits, "get_user", lambda _k: {"id": 7})
        # Flag off: with it on, the read path may still try the old plaintext
        # rows of the current window (that is the transition fallback).
        monkeypatch.setenv(api_key_lookup.FALLBACK_ENV_VAR, "false")
        with caplog.at_level(logging.DEBUG):
            rate_limits.update_rate_limit_usage(KEY, tokens_used=10)

        inserts = [q.payload for q in client.calls if q.op == "insert"]
        assert len(inserts) == 3
        key_hash = sha256_key_hash(KEY)
        for row in inserts:
            assert row["key_hash"] == key_hash
            assert row["api_key"] == "hash:" + key_hash[:12]
        assert KEY not in client.all_values()
        _no_key_in_logs(caplog)

    def test_reads_by_hash(self, monkeypatch):
        client = _Client()
        monkeypatch.setattr(rate_limits, "get_supabase_client", lambda: client)
        monkeypatch.setattr(
            rate_limits,
            "get_user_rate_limits",
            lambda _k: {
                "requests_per_minute": 10,
                "requests_per_hour": 10,
                "requests_per_day": 10,
                "tokens_per_minute": 10,
                "tokens_per_hour": 10,
                "tokens_per_day": 10,
            },
        )
        monkeypatch.setenv(api_key_lookup.FALLBACK_ENV_VAR, "false")
        assert rate_limits.check_rate_limit(KEY)["allowed"] is True
        usage_queries = [q for q in client.calls if q.table == "rate_limit_usage"]
        assert usage_queries
        assert usage_queries[0].filters == [("key_hash", sha256_key_hash(KEY))]
        assert "api_key" not in client.filter_columns("rate_limit_usage")
