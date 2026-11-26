"""Only the account's own sign-in key may change who controls the account or
where its discount credits go (src/security/deps.py::get_account_owner_id).

An ordinary API key made for inference -- which may sit in a third-party
tool -- must not be able to link a wallet (and then sign in with it, or
receive the account's credits), unlink one, or record the eligibility
attestation."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import app
from src.security import deps
from src.security.deps import get_account_owner_id, get_api_key, get_current_user

client = TestClient(app)


def _owner(api_key: str, user: dict) -> int:
    return asyncio.run(get_account_owner_id(api_key=api_key, user=user))


def test_a_primary_key_is_the_account_owner():
    assert _owner("gw_live_primary", {"id": 5, "is_primary": True}) == 5


def test_a_temporary_session_key_is_the_account_owner():
    assert _owner("gw_temp_session", {"id": 5}) == 5


@pytest.mark.parametrize("is_primary", [False, None, "true", 1])
def test_any_other_key_is_refused(is_primary):
    with pytest.raises(HTTPException) as excinfo:
        _owner("gw_live_inference", {"id": 5, "is_primary": is_primary})
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "account_key_required"


ROUTES = [
    ("post", "/auth/wallet/cardano/nonce", {"stake_address": "stake1" + "u" * 53}),
    ("post", "/auth/wallet/cardano/link", {"stake_address": "s", "signature": "00", "key": "00"}),
    ("post", "/wallet/eligibility", {"not_us_ca_person": True}),
    ("post", "/auth/wallet/link/nonce", {"wallet_address": "0x" + "a" * 40}),
    ("post", "/auth/wallet/link", {"message": "m", "signature": "0x00"}),
    ("delete", "/auth/wallets/0x" + "a" * 40, None),
    # Not a wallet route, but the same rule: the daily cap on discount
    # credits is set with the account's own key only.
    ("put", "/discount-credits/daily-cap", {"daily_cap_usd": "10"}),
]


@pytest.mark.parametrize("method, path, body", ROUTES)
def test_wallet_routes_refuse_an_inference_key(method, path, body):
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_api_key] = lambda: "gw_live_inference"
    app.dependency_overrides[get_current_user] = lambda: {"id": 5, "is_primary": False}
    try:
        call = getattr(client, method)
        response = call(path, json=body) if body is not None else call(path)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
    # 422 is also acceptable only if validation runs first; the owner check
    # must never let the request through.
    assert response.status_code in (403, 422), (path, response.status_code)
    if response.status_code == 403:
        assert "account_key_required" in response.text


def test_the_dependency_is_used_by_every_wallet_route():
    """A route that changes wallets must depend on get_account_owner_id."""
    from fastapi.routing import APIRoute

    def depends_on_owner(route: APIRoute) -> bool:
        stack = list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            if dep.call is deps.get_account_owner_id:
                return True
            stack.extend(dep.dependencies)
        return False

    guarded = {(m.lower(), p) for m, p, _ in [(m, p, b) for m, p, b in ROUTES]}
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (method.lower(), route.path)
            if key in {(m, p) for m, p in guarded} or (
                method == "DELETE" and route.path == "/auth/wallets/{wallet_address}"
            ):
                assert depends_on_owner(route), route.path
                checked += 1
    assert checked >= 7
