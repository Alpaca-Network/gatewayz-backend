"""Setting a key's purpose through POST/PUT /user/api-keys, and storing it.

Includes the fix for PUT /user/api-keys/{key_id} editing the CALLER's key
instead of key_id: for a privacy flag that bug is a fail-open (the key the
user marked "validator" kept being logged).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from src.routes import api_keys as routes
from src.schemas import CreateApiKeyRequest, UpdateApiKeyRequest


def _run(coro):
    """Run a coroutine on a private loop.

    Not asyncio.run(): that unsets the thread's current event loop on exit,
    which breaks later tests in the same worker that call get_event_loop().
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


CALLER_KEY = "gw_live_caller_key_A"
TARGET_KEY = "gw_live_target_key_B"
USER = {"id": 5}


def _target_row(purpose="general"):
    return {
        "id": 22,
        "key_name": "validator-node",
        "api_key": TARGET_KEY,
        "environment_tag": "test",
        "scope_permissions": {},
        "is_active": True,
        "is_primary": False,
        "requests_used": 0,
        "ip_allowlist": [],
        "domain_referrers": [],
        "purpose": purpose,
    }


@pytest.fixture
def create_env():
    create = Mock(return_value=("gw_test_new", 22))
    with (
        patch.object(routes, "get_user", return_value=USER),
        patch.object(
            routes, "check_auth_rate_limit", AsyncMock(return_value=SimpleNamespace(allowed=True))
        ),
        patch.object(routes, "validate_api_key_permissions", return_value=True),
        patch.object(routes, "create_api_key", create),
    ):
        yield create


class TestCreate:
    def _create(self, **body):
        req = CreateApiKeyRequest(key_name="node", environment_tag="test", **body)
        return _run(routes.create_user_api_key(req, api_key=CALLER_KEY))

    def test_validator_purpose_is_passed_to_storage(self, create_env):
        out = self._create(purpose="validator")
        assert create_env.call_args.kwargs["purpose"] == "validator"
        assert out["purpose"] == "validator"

    @pytest.mark.parametrize("body", [{}, {"purpose": "general"}, {"purpose": None}])
    def test_general_key_call_is_unchanged(self, create_env, body):
        out = self._create(**body)
        assert "purpose" not in create_env.call_args.kwargs
        assert out["purpose"] == "general"

    def test_invalid_purpose_is_a_400_and_nothing_is_created(self, create_env):
        with pytest.raises(HTTPException) as exc:
            self._create(purpose="nolog")
        assert exc.value.status_code == 400
        assert create_env.call_count == 0


class TestUpdate:
    def _update(self, purpose, update_ok=True):
        update = Mock(return_value=update_ok)
        with (
            patch.object(routes, "get_user", return_value=USER),
            patch.object(routes, "validate_api_key_permissions", return_value=True),
            patch.object(routes, "get_api_key_by_id", return_value=_target_row()),
            patch.object(routes, "update_api_key", update),
        ):
            _run(
                routes.update_user_api_key_endpoint(
                    22, UpdateApiKeyRequest(purpose=purpose), api_key=CALLER_KEY
                )
            )
        return update

    def test_updates_the_key_in_the_path_not_the_callers_key(self):
        update = self._update("validator")
        key_used, user_id, updates = update.call_args.args
        assert key_used == TARGET_KEY  # was CALLER_KEY before the fix
        assert updates == {"purpose": "validator"}

    def test_general_clears_the_flag(self):
        assert self._update("general").call_args.args[2] == {"purpose": None}

    def test_invalid_purpose_is_a_400(self):
        with pytest.raises(HTTPException) as exc:
            self._update("private")
        assert exc.value.status_code == 400


class _InsertRecorder:
    def __init__(self):
        self.payloads: list[dict] = []

    def table(self, name):
        chain = MagicMock()
        for m in ("select", "eq", "update", "limit"):
            getattr(chain, m).return_value = chain
        chain.execute.return_value = SimpleNamespace(data=[{"id": 22, "user_id": 5}])

        def _insert(payload):
            if name == "api_keys_new":
                self.payloads.append(payload)
            return chain

        chain.insert.side_effect = _insert
        return chain


class TestStorage:
    def _create(self, monkeypatch, **kw):
        from src.db import api_keys

        monkeypatch.setenv("KEY_HASH_SALT", "0123456789abcdef0123456789abcdef")
        client = _InsertRecorder()
        monkeypatch.setattr(api_keys, "get_supabase_client", lambda: client)
        monkeypatch.setattr(api_keys, "check_key_name_uniqueness", lambda *a, **k: True)
        api_keys.create_api_key(user_id=5, key_name="node", environment_tag="test", **kw)
        assert len(client.payloads) == 1
        return client.payloads[0]

    def test_validator_row_carries_purpose(self, monkeypatch):
        assert self._create(monkeypatch, purpose="validator")["purpose"] == "validator"

    def test_general_row_has_no_purpose_column(self, monkeypatch):
        # Byte-identical insert payload for every key that isn't a validator key.
        assert "purpose" not in self._create(monkeypatch)

    def test_update_accepts_purpose_and_invalidates_the_cached_user(self, monkeypatch):
        from src.db import api_keys

        client = _InsertRecorder()
        updates_seen = []
        real_table = client.table

        def table(name):
            chain = real_table(name)
            if name == "api_keys_new":
                chain.update.side_effect = lambda data: (updates_seen.append(data), chain)[1]
            return chain

        client.table = table
        monkeypatch.setattr(api_keys, "get_supabase_client", lambda: client)
        invalidate = Mock()
        monkeypatch.setattr("src.db.users.invalidate_user_cache", invalidate)
        assert api_keys.update_api_key(TARGET_KEY, 5, {"purpose": "validator"}) is True
        assert updates_seen and updates_seen[0]["purpose"] == "validator"
        invalidate.assert_called_once_with(TARGET_KEY)
