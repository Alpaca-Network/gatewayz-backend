"""Tests for src.routes.session_transfer (one-time cross-site sign-in codes)."""

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import src.routes.session_transfer as st
from src.main import app
from src.security.privy_token import PrivyTokenError
from src.services.auth_rate_limiting import AuthRateLimitResult

client = TestClient(app)

_ALLOWED = AuthRateLimitResult(allowed=True, remaining=10)
_BLOCKED = AuthRateLimitResult(allowed=False, remaining=0, retry_after=30)
_API_KEY = "gw_live_test_key_value_1234567890"
_USER = {"id": 42, "key_id": 7, "privy_user_id": "did:privy:abc"}
_PROFILE = {
    "id": 42,
    "privy_user_id": "did:privy:abc",
    "username": "alice",
    "email": "alice@example.com",
    "auth_method": "email",
}
_KEY_ROW = {
    "id": 7,
    "user_id": 42,
    "api_key": _API_KEY,
    "encrypted_key": None,
    "key_version": None,
    "is_primary": True,
    "created_at": "2026-01-01",
}


class FakeRedis:
    """Dict-backed stand-in for the setex/getdel calls the route makes."""

    def __init__(self):
        self.store = {}

    def setex(self, key, ttl, value):
        self.store[key] = (value, time.time() + ttl)

    def getdel(self, key):
        item = self.store.pop(key, None)
        if item is None or item[1] < time.time():
            return None
        return item[0]


@pytest.fixture
def redis():
    fake = FakeRedis()
    with patch.object(st, "get_redis_client", return_value=fake):
        yield fake


@pytest.fixture(autouse=True)
def _allow_rate_limit():
    with patch.object(st, "check_auth_rate_limit", new=AsyncMock(return_value=_ALLOWED)) as m:
        yield m


@pytest.fixture
def api_key_auth():
    with (
        patch.object(st, "get_api_key", new=AsyncMock(return_value=_API_KEY)),
        patch.object(st, "get_user", return_value=dict(_USER)),
    ):
        yield


@pytest.fixture
def key_lookup():
    with (
        patch.object(st, "_load_key_row", return_value=dict(_KEY_ROW)) as load,
        patch.object(st, "get_user_by_id", return_value=dict(_PROFILE)),
    ):
        yield load


def _issue():
    resp = client.post("/auth/transfer-code", headers={"Authorization": f"Bearer {_API_KEY}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _redeem(code):
    return client.post("/auth/transfer-code/redeem", json={"code": code})


def _assert_invalid(resp):
    """400 with the one shared message (the app's error handler wraps it)."""
    assert resp.status_code == 400
    assert resp.json()["error"]["context"]["parameter_value"] == st.INVALID_CODE_DETAIL


def test_issue_returns_code_and_stores_only_hash(redis, api_key_auth):
    body = _issue()
    assert body["expires_in"] == 60
    assert len(body["code"]) >= 43
    stored_keys = list(redis.store)
    assert stored_keys == [f"session_transfer:{st.code_hash(body['code'])}"]
    value, _ = redis.store[stored_keys[0]]
    assert body["code"] not in value
    assert _API_KEY not in value
    record = json.loads(value)
    assert record["user_id"] == 42
    assert record["key_id"] == 7
    assert record["used_at"] is None


def test_issue_requires_credentials(redis):
    with patch.object(
        st, "get_api_key", new=AsyncMock(side_effect=HTTPException(status_code=401))
    ):
        resp = client.post("/auth/transfer-code")
    assert resp.status_code == 401
    assert redis.store == {}


def test_issue_with_verified_privy_token(redis):
    with (
        patch.object(st, "verify_privy_access_token") as verify,
        patch.object(st, "get_user_by_privy_id", return_value=dict(_PROFILE)),
    ):
        resp = client.post(
            "/auth/transfer-code",
            headers={"Authorization": "Bearer privy.jwt.token"},
            json={"privy_user_id": "did:privy:abc"},
        )
    assert resp.status_code == 200
    verify.assert_called_once_with("privy.jwt.token", expected_sub="did:privy:abc")
    (value, _) = next(iter(redis.store.values()))
    assert json.loads(value)["key_id"] is None


def test_issue_rejects_bad_privy_token(redis):
    with patch.object(
        st, "verify_privy_access_token", side_effect=PrivyTokenError("bad_signature", "x")
    ):
        resp = client.post(
            "/auth/transfer-code",
            headers={"Authorization": "Bearer forged"},
            json={"privy_user_id": "did:privy:abc"},
        )
    assert resp.status_code == 401
    assert redis.store == {}


def test_redeem_once_then_fails(redis, api_key_auth, key_lookup):
    code = _issue()["code"]
    first = _redeem(code)
    assert first.status_code == 200
    data = first.json()
    assert data["api_key"] == _API_KEY
    assert data["user_id"] == 42
    assert data["privy_user_id"] == "did:privy:abc"
    assert first.headers["cache-control"] == "no-store"
    key_lookup.assert_called_once_with(42, 7)

    second = _redeem(code)
    _assert_invalid(second)


def test_redeem_unknown_code(redis, key_lookup):
    resp = _redeem("x" * 43)
    _assert_invalid(resp)


def test_redeem_expired_code(redis, api_key_auth, key_lookup):
    code = _issue()["code"]
    later = time.time() + st.CODE_TTL_SECONDS + 1
    with patch.object(st.time, "time", return_value=later):
        resp = _redeem(code)
    _assert_invalid(resp)


def test_redeem_old_record_rejected_even_without_ttl(redis, key_lookup):
    code = "y" * 43
    record = {"user_id": 42, "key_id": 7, "created_at": time.time() - 120, "used_at": None}
    redis.store[f"session_transfer:{st.code_hash(code)}"] = (json.dumps(record), time.time() + 99)
    resp = _redeem(code)
    _assert_invalid(resp)


def test_redeem_revoked_key_gives_same_error(redis, api_key_auth):
    code = _issue()["code"]
    with patch.object(st, "_load_key_row", return_value=None):
        resp = _redeem(code)
    _assert_invalid(resp)


def test_redeem_is_rate_limited(redis, _allow_rate_limit):
    _allow_rate_limit.return_value = _BLOCKED
    resp = _redeem("z" * 43)
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "30"


def test_redeem_without_redis_is_503():
    with patch.object(st, "get_redis_client", return_value=None):
        resp = _redeem("z" * 43)
    assert resp.status_code == 503


def test_redeem_rejects_short_code(redis):
    resp = _redeem("short")
    assert resp.status_code == 422


def test_codes_and_keys_never_logged(redis, api_key_auth, key_lookup, caplog):
    caplog.set_level(logging.DEBUG)
    code = _issue()["code"]
    assert _redeem(code).status_code == 200
    assert _redeem(code).status_code == 400
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert code not in text
    assert st.code_hash(code) not in text
    assert _API_KEY not in text


def test_load_key_row_filters_by_issuing_key():
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.execute.return_value = MagicMock(data=[dict(_KEY_ROW)])
    fake_client = MagicMock()
    fake_client.table.return_value = query
    with patch.object(st.supabase_config, "get_supabase_client", return_value=fake_client):
        row = st._load_key_row(42, 7)
    assert row["id"] == 7
    eq_calls = [c.args for c in query.eq.call_args_list]
    assert ("user_id", 42) in eq_calls
    assert ("is_active", True) in eq_calls
    assert ("id", 7) in eq_calls


def test_load_key_row_prefers_primary_without_key_id():
    rows = [
        {**_KEY_ROW, "id": 1, "is_primary": False, "created_at": "2025-01-01"},
        {**_KEY_ROW, "id": 2, "is_primary": True, "created_at": "2026-01-01"},
    ]
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.execute.return_value = MagicMock(data=rows)
    fake_client = MagicMock()
    fake_client.table.return_value = query
    with patch.object(st.supabase_config, "get_supabase_client", return_value=fake_client):
        row = st._load_key_row(42, None)
    assert row["id"] == 2
    assert all(c.args[0] != "id" for c in query.eq.call_args_list)
