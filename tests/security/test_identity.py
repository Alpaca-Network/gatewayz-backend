"""Tests for src.security.identity (gatewayz-backend#2254).

RequestIdentity is the single composed view of "who is this request from" —
api-key user, wallet-linked user, or anonymous. It does not replace
get_api_key/get_user_id/get_optional_*; it composes them. Exercised through
the real FastAPI dependency chain (TestClient + a tiny throwaway router) so
these tests catch wiring mistakes a unit test of the function alone would
miss.
"""

import sys
import types
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.security.identity import ANONYMOUS, RequestIdentity, get_request_identity


def _fake_user_wallets_module(*, rows=None, raises=None):
    """Build a throwaway `src.db.user_wallets` module for sys.modules patching.

    W1 owns the real module (not created here per the brief); this fakes just
    enough of its shape (`get_wallets_for_user(user_id) -> list[dict]`) to
    exercise the lazy import in `src.security.identity` without depending on
    a real module existing on disk -- `unittest.mock.patch("src.db.user_wallets....")`
    can't target an attribute path through a module that doesn't exist yet.
    """
    mod = types.ModuleType("src.db.user_wallets")

    def get_wallets_for_user(user_id):
        if raises is not None:
            raise raises
        return rows or []

    mod.get_wallets_for_user = get_wallets_for_user
    return mod


# --- Tiny throwaway router exposing the identity as JSON ------------------

test_app = FastAPI()


@test_app.get("/_test/identity")
async def _identity_probe(identity: RequestIdentity = Depends(get_request_identity)):
    return {
        "kind": identity.kind,
        "user_id": identity.user_id,
        "api_key": identity.api_key,
        "auth_method": identity.auth_method,
        "is_guest": identity.is_guest,
        "wallet_addresses": list(identity.wallet_addresses),
        "is_anonymous": identity.is_anonymous,
        "primary_wallet": identity.primary_wallet,
    }


client = TestClient(test_app)


def _user(**overrides):
    base = {"id": 42, "auth_method": "email", "environment_tag": "live", "credits": 0}
    base.update(overrides)
    return base


# --- ANONYMOUS constant -----------------------------------------------------


def test_anonymous_constant_shape():
    assert ANONYMOUS.kind == "anonymous"
    assert ANONYMOUS.user_id is None
    assert ANONYMOUS.api_key is None
    assert ANONYMOUS.auth_method is None
    assert ANONYMOUS.is_guest is True
    assert ANONYMOUS.wallet_addresses == ()
    assert ANONYMOUS.is_anonymous is True
    assert ANONYMOUS.primary_wallet is None


def test_request_identity_is_frozen():
    import dataclasses

    assert dataclasses.is_dataclass(RequestIdentity)
    try:
        ANONYMOUS.user_id = 1  # type: ignore[misc]
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised


# --- (a) valid API key -------------------------------------------------------


@patch("src.security.identity.get_user")
def test_valid_api_key_resolves_api_key_identity(mock_get_user):
    mock_get_user.return_value = _user(id=7, auth_method="email", credits=5)

    fake_mod = _fake_user_wallets_module(
        rows=[{"wallet_address": "0xabc", "is_primary": True, "source": "link"}]
    )
    with patch.dict(sys.modules, {"src.db.user_wallets": fake_mod}):
        resp = client.get("/_test/identity", headers={"Authorization": "Bearer gw_test_key"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "api_key"
    assert body["user_id"] == 7
    assert body["api_key"] == "gw_test_key"
    assert body["auth_method"] == "email"
    assert body["is_anonymous"] is False
    assert body["wallet_addresses"] == ["0xabc"]
    assert body["primary_wallet"] == "0xabc"


# --- (b) wallet-linked user ---------------------------------------------------


@patch("src.security.identity.get_user")
def test_wallet_linked_user_has_primary_wallet(mock_get_user):
    mock_get_user.return_value = _user(id=9, auth_method="wallet", credits=0)

    fake_mod = _fake_user_wallets_module(
        rows=[{"wallet_address": "0xdef", "is_primary": True, "source": "signup"}]
    )
    with patch.dict(sys.modules, {"src.db.user_wallets": fake_mod}):
        resp = client.get("/_test/identity", headers={"Authorization": "Bearer gw_test_key2"})

    body = resp.json()
    assert body["kind"] == "api_key"
    assert body["auth_method"] == "wallet"
    assert body["primary_wallet"] == "0xdef"
    # wallet auth_method with no payment signal -> guest
    assert body["is_guest"] is True


@patch("src.security.identity.get_user")
def test_wallet_linked_user_with_payment_signal_is_not_guest(mock_get_user):
    mock_get_user.return_value = _user(id=10, auth_method="wallet", credits=25.0)

    fake_mod = _fake_user_wallets_module(
        rows=[{"wallet_address": "0xfee", "is_primary": True, "source": "signup"}]
    )
    with patch.dict(sys.modules, {"src.db.user_wallets": fake_mod}):
        resp = client.get("/_test/identity", headers={"Authorization": "Bearer gw_test_key3"})

    body = resp.json()
    assert body["is_guest"] is False


# --- (c) no Authorization header -> ANONYMOUS -------------------------------


def test_no_header_returns_anonymous():
    resp = client.get("/_test/identity")
    body = resp.json()
    assert body["kind"] == "anonymous"
    assert body["is_anonymous"] is True
    assert body["user_id"] is None
    assert body["wallet_addresses"] == []


# --- (d) invalid key -> matches get_optional_api_key semantics -------------
# In IS_TESTING mode, validate_api_key_security short-circuits and accepts
# any bearer token, so a real "invalid key" (bad format/expired/inactive) is
# simulated by making the underlying validator raise, exactly as it would
# against a real DB. get_optional_api_key swallows the resulting
# HTTPException and returns None -> get_request_identity resolves ANONYMOUS.


def test_invalid_key_falls_back_to_anonymous():
    with patch(
        "src.security.deps.validate_api_key_security",
        side_effect=ValueError("inactive"),
    ):
        resp = client.get("/_test/identity", headers={"Authorization": "Bearer bad-key"})

    body = resp.json()
    assert body["kind"] == "anonymous"
    assert body["is_anonymous"] is True


# A validly-formatted key with no matching user is a *different* case from
# "invalid key": get_optional_api_key still returns the key (kind stays
# "api_key", is_anonymous False) even though the get_user(api_key) lookup
# below finds nothing -- this preserves parity with the pre-existing
# `is_anonymous = api_key is None` derivation in chat.py, which never looked
# at whether a user existed.


@patch("src.security.identity.get_user")
def test_key_with_no_matching_user_keeps_api_key_kind(mock_get_user):
    mock_get_user.return_value = None

    resp = client.get("/_test/identity", headers={"Authorization": "Bearer nonexistent"})
    body = resp.json()
    assert body["kind"] == "api_key"
    assert body["is_anonymous"] is False
    assert body["user_id"] is None
    assert body["wallet_addresses"] == []


# --- (e) user_wallets import missing/raising -> wallets () without error ----


@patch("src.security.identity.get_user")
def test_missing_user_wallets_module_yields_empty_wallets(mock_get_user):
    mock_get_user.return_value = _user(id=11, auth_method="email")

    with patch.dict("sys.modules", {"src.db.user_wallets": None}):
        resp = client.get("/_test/identity", headers={"Authorization": "Bearer gw_test_key4"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["wallet_addresses"] == []
    assert body["primary_wallet"] is None


@patch("src.security.identity.get_user")
def test_wallet_lookup_raising_yields_empty_wallets(mock_get_user):
    mock_get_user.return_value = _user(id=12, auth_method="email")

    fake_mod = _fake_user_wallets_module(raises=RuntimeError("db down"))
    with patch.dict(sys.modules, {"src.db.user_wallets": fake_mod}):
        resp = client.get("/_test/identity", headers={"Authorization": "Bearer gw_test_key5"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["wallet_addresses"] == []
