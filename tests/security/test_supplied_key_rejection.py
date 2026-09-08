"""A key that was SUPPLIED and rejected must not become anonymous.

Third instance of the same misdirection pattern as the model-id 503 and the
spent-cap 429: the caller made a mistake, and the response pointed somewhere
else. A typo'd key on /v1/messages produced

    "Model 'anthropic/claude-sonnet-4-6' is not available for anonymous users.
     Anonymous access is limited to free models: ..."

which sends an integrator to read our model catalog when the actual problem is
one wrong character in their Authorization header.

Sending NO credentials still resolves to anonymous — that is the whole point of
an optional-auth endpoint and is unchanged. The distinction is between "no key"
and "a key you rejected".
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.security import deps


class _Creds(HTTPAuthorizationCredentials):
    def __init__(self, token: str):
        super().__init__(scheme="Bearer", credentials=token)


async def test_no_credentials_still_resolves_to_anonymous():
    assert await deps.get_optional_api_key_strict(credentials=None, request=None) is None


async def test_a_valid_supplied_key_is_returned(monkeypatch):
    async def _ok(credentials, request, log_security_violations=True):
        return "gw_live_valid"

    monkeypatch.setattr(deps, "get_api_key", _ok)
    got = await deps.get_optional_api_key_strict(credentials=_Creds("gw_live_valid"), request=None)
    assert got == "gw_live_valid"


async def test_a_supplied_but_invalid_key_is_rejected_not_ignored(monkeypatch):
    async def _reject(credentials, request, log_security_violations=True):
        raise deps.ceiling_http_exception("Invalid API key")

    monkeypatch.setattr(deps, "get_api_key", _reject)
    with pytest.raises(HTTPException) as exc:
        await deps.get_optional_api_key_strict(credentials=_Creds("gw_live_typo"), request=None)
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "invalid_api_key"


async def test_an_exhausted_cap_is_not_swallowed_into_anonymous(monkeypatch):
    # The lenient dependency turned a 402 into anonymous access, so a partner
    # whose cap ran out saw a model-availability message instead of the ceiling
    # they had actually hit.
    async def _reject(credentials, request, log_security_violations=True):
        raise deps.ceiling_http_exception("API key request limit reached")

    monkeypatch.setattr(deps, "get_api_key", _reject)
    with pytest.raises(HTTPException) as exc:
        await deps.get_optional_api_key_strict(credentials=_Creds("gw_live_spent"), request=None)
    assert exc.value.status_code == 402
    assert exc.value.detail["error"]["code"] == "request_cap_exhausted"


async def test_the_lenient_dependency_is_unchanged(monkeypatch):
    # 29 other call sites still want a stale browser token to degrade to
    # anonymous rather than 401 a public page. Only the billed inference routes
    # opt into strict.
    async def _reject(credentials, request, log_security_violations=True):
        raise HTTPException(status_code=401, detail="Invalid API key")

    monkeypatch.setattr(deps, "get_api_key", _reject)
    assert await deps.get_optional_api_key(credentials=_Creds("stale"), request=None) is None


def test_both_inference_routes_use_the_strict_chain():
    """Guards the deduping: `api_key` and `identity` must resolve through the
    SAME key dependency, or FastAPI's per-request cache misses and every
    authenticated request re-runs validate_api_key_security -- including its
    last_used_at write to Supabase. identity.py's docstring documents that
    hazard; this test is what stops someone reintroducing it."""
    import inspect

    from src.routes import chat

    sig = inspect.signature(chat.chat_completions)
    key_dep = sig.parameters["api_key"].default.dependency
    identity_dep = sig.parameters["identity"].default.dependency
    assert key_dep is deps.get_optional_api_key_strict

    identity_sig = inspect.signature(identity_dep)
    assert identity_sig.parameters["api_key"].default.dependency is (
        deps.get_optional_api_key_strict
    )
