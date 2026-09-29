"""Hardening regressions: key validation, rate-limit accounting, clamping, IP trust."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# ---- 1. /v1/messages x-api-key gets full key-security validation -------------------------


def test_messages_x_api_key_runs_validate_api_key_security():
    from src.main import app

    body = {
        "model": "claude-3-5-haiku-latest",
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "hi"}],
    }
    calls = {}

    def fake_validate(api_key, client_ip=None, referer=None):
        calls["key"] = api_key
        raise ValueError("IP address not allowed")

    chat = AsyncMock(return_value={})
    with (
        patch("src.routes.messages.validate_api_key_security", new=fake_validate),
        patch("src.routes.chat.chat_completions", new=chat),
    ):
        resp = TestClient(app).post(
            "/v1/messages",
            json=body,
            headers={"x-api-key": "gw_live_abc123", "anthropic-version": "2023-06-01"},
        )
    assert calls.get("key") == "gw_live_abc123"
    assert resp.status_code in (401, 402, 403), resp.text
    chat.assert_not_called()


# ---- 2. allowed rate-limit results are never cached -------------------------------------


@pytest.mark.asyncio
async def test_allowed_results_are_not_cached_so_every_request_counts():
    from src.services import rate_limiting as mod

    mgr = mod.RateLimitManager(redis_client=None)
    inner = AsyncMock(
        return_value=mod.RateLimitResult(
            allowed=True,
            remaining_requests=1,
            remaining_tokens=1,
            reset_time=mod.datetime.now(mod.UTC),
        )
    )
    mgr.rate_limiter.check_rate_limit = inner
    for _ in range(3):
        await mgr.check_rate_limit("gw_key_x", tokens_used=0)
    assert inner.await_count == 3


# ---- 3. user-set rate limits are clamped ------------------------------------------------


def _cfg(v):
    return {
        f: v
        for f in (
            "requests_per_minute",
            "requests_per_hour",
            "requests_per_day",
            "tokens_per_minute",
            "tokens_per_hour",
            "tokens_per_day",
            "burst_limit",
            "concurrency_limit",
        )
    }


@pytest.mark.asyncio
async def test_user_cannot_raise_own_limits_above_tier_max():
    from src.routes import rate_limits as mod

    captured = {}
    with (
        patch.object(mod, "get_user", return_value={"id": 1, "role": "user"}),
        patch.object(mod, "get_api_key_by_id", return_value={"api_key": "k"}),
        patch.object(
            mod, "update_rate_limit_config", side_effect=lambda k, c: captured.update(c) or True
        ),
    ):
        await mod.update_user_rate_limits_advanced(5, _cfg(10**9), api_key="k")
    assert captured["requests_per_minute"] <= 250
    assert captured["tokens_per_day"] <= 1_000_000
    assert captured["burst_limit"] <= 100
    assert captured["concurrency_limit"] <= 50


@pytest.mark.asyncio
async def test_admin_can_exceed_tier_max():
    from src.routes import rate_limits as mod

    captured = {}
    with (
        patch.object(mod, "get_user", return_value={"id": 1, "role": "admin"}),
        patch.object(mod, "get_api_key_by_id", return_value={"api_key": "k"}),
        patch.object(
            mod, "update_rate_limit_config", side_effect=lambda k, c: captured.update(c) or True
        ),
    ):
        await mod.update_user_rate_limits_advanced(5, _cfg(10**9), api_key="k")
    assert captured["requests_per_minute"] == 10**9


@pytest.mark.asyncio
async def test_bulk_update_clamps_too():
    from src.routes import rate_limits as mod

    captured = {}
    with (
        patch.object(mod, "get_user", return_value={"id": 1, "role": "user"}),
        patch.object(
            mod, "bulk_update_rate_limit_configs", side_effect=lambda u, c: captured.update(c) or 2
        ),
    ):
        await mod.bulk_update_user_rate_limits(_cfg(10**9), api_key="k")
    assert captured["requests_per_hour"] <= 1000


# ---- 4. middleware: only a validated key skips IP limiting; XFF not trusted -------------


def _mw():
    from src.middleware.security_middleware import SecurityMiddleware

    return SecurityMiddleware(MagicMock(), redis_client=None)


def _req(auth="", xff=None, host="9.9.9.9"):
    r = MagicMock()
    h = {"Authorization": auth}
    if xff:
        h["X-Forwarded-For"] = xff
    r.headers = MagicMock()
    r.headers.get = lambda k, d="": h.get(k, d)
    r.client.host = host
    return r


@pytest.mark.asyncio
async def test_garbage_authorization_header_is_not_authenticated():
    mw = _mw()
    with patch("src.db.users.get_user", return_value=None):
        assert not await mw._is_authenticated_request(_req("Bearer " + "x" * 40))
        assert not await mw._is_authenticated_request(_req("gw_" + "x" * 40))


@pytest.mark.asyncio
async def test_valid_key_is_authenticated():
    mw = _mw()
    with patch("src.db.users.get_user", return_value={"id": 3}):
        assert await mw._is_authenticated_request(_req("Bearer gw_" + "x" * 40))


@pytest.mark.asyncio
async def test_client_ip_uses_rightmost_hop_not_spoofed_leftmost():
    mw = _mw()
    ip = await mw._get_client_ip(_req(xff="1.1.1.1, 5.5.5.5"))
    assert ip == "5.5.5.5"


# ---- 5. security.py fails closed on DB errors -------------------------------------------


def test_validate_api_key_fails_closed_on_db_error(monkeypatch):
    from src.security import security as sec

    monkeypatch.setattr(sec.Config, "IS_TESTING", False)
    monkeypatch.delenv("TESTING", raising=False)
    client = MagicMock()
    client.table.side_effect = RuntimeError("db down")
    legacy = MagicMock(return_value={"id": 1})
    with (
        patch("src.config.supabase_config.get_supabase_client", return_value=client),
        patch("src.db.users.get_user", legacy),
    ):
        with pytest.raises(ValueError):
            sec.validate_api_key_security("gw_revoked_key")
    legacy.assert_not_called()


# ---- 6. dev key only when APP_ENV explicitly development and not on Railway --------------


def _is_dev(**env):
    import os
    import subprocess
    import sys

    base = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "RAILWAY_ENVIRONMENT")}
    base.update(env)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.config.config import Config; print(Config.IS_DEVELOPMENT)",
        ],
        env=base,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return out.stdout.strip().splitlines()[-1] == "True"


def test_unset_app_env_is_not_development():
    assert _is_dev() is False


def test_explicit_development_is_development():
    assert _is_dev(APP_ENV="development") is True


def test_development_on_railway_is_not_development():
    assert _is_dev(APP_ENV="development", RAILWAY_ENVIRONMENT="production") is False
