"""Tests for GET /discount-credits (src/routes/discount_credits.py): the
caller's discount credits balance and recent movements. The DB is mocked."""

from __future__ import annotations

import re
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.discount_credits as routes
from src.main import app
from src.security.deps import get_current_user
from src.services.discount_credits import billing

client = TestClient(app)
USER = {"id": 42, "email": "user@example.com"}

BALANCE = {
    "user_id": 42,
    "balance_usd": "12.5",
    "lifetime_granted_usd": "20",
    "lifetime_used_usd": "7.5",
}
LEDGER = [
    {
        "id": 3,
        "created_at": "2026-11-20T00:00:03+00:00",
        "kind": "use",
        "amount_usd": "0.99",
        "balance_after": "12.5",
        "request_id": "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b",
        "ref": "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b",
        "metadata": {"model": "m"},
    },
    {
        "id": 2,
        "created_at": "2026-11-20T00:00:02+00:00",
        "kind": "reverse",
        "amount_usd": "0.5",
        "balance_after": "13.49",
        "ref": "internal-ref",
    },
    {
        "id": 1,
        "created_at": "2026-11-20T00:00:01+00:00",
        "kind": "grant",
        "amount_usd": "20",
        "balance_after": "20",
        "ref": "vault:mint:1",
    },
]


@pytest.fixture(autouse=True)
def _user():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: USER
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", True)
    monkeypatch.setattr(billing.Config, "VAULT_DISCOUNT_PERCENT", Decimal("0.99"))


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.setattr(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", False)
    monkeypatch.setattr(billing.Config, "VAULT_DISCOUNT_PERCENT", Decimal("0.99"))


def test_requires_a_caller():
    app.dependency_overrides.pop(get_current_user)
    with (
        patch.object(routes, "get_balance") as balance,
        patch.object(routes, "list_ledger") as ledger,
    ):
        response = client.get("/discount-credits")
    assert response.status_code in (401, 403)
    balance.assert_not_called()
    ledger.assert_not_called()


def test_enabled_shape(enabled):
    with (
        patch.object(routes, "get_balance", return_value=BALANCE) as balance,
        patch.object(routes, "list_ledger", return_value=LEDGER) as ledger,
    ):
        response = client.get("/discount-credits")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert set(body["data"]) == {"discount", "discount_history", "disclaimer"}
    assert body["data"]["discount"] == {
        "enabled": True,
        "discount_percent": "0.99",
        "reward_share": "0",
        "balance_usd": "12.500000",
        "lifetime_granted_usd": "20.000000",
        "lifetime_used_usd": "7.500000",
    }
    balance.assert_called_once_with(42)
    ledger.assert_called_once_with(42, limit=50)
    history = body["data"]["discount_history"]
    assert [set(item) for item in history] == [
        {"created_at", "kind", "amount_usd", "balance_after", "ref"}
    ] * 3
    # A use / refund never shows its billing request id; a grant shows its ref.
    assert [(h["kind"], h["ref"]) for h in history] == [
        ("use", "inference"),
        ("reverse", "refund"),
        ("grant", "vault:mint:1"),
    ]
    assert history[0]["amount_usd"] == "0.990000"
    assert history[0]["balance_after"] == "12.500000"


def test_enabled_account_without_a_balance_row_reads_zero(enabled):
    with (
        patch.object(routes, "get_balance", return_value=None),
        patch.object(routes, "list_ledger", return_value=[]),
    ):
        data = client.get("/discount-credits").json()["data"]
    assert data["discount"]["balance_usd"] == "0.000000"
    assert data["discount_history"] == []


def test_disabled_shape_is_the_same_keys_with_zeros_and_no_db_read(disabled):
    with (
        patch.object(routes, "get_balance") as balance,
        patch.object(routes, "list_ledger") as ledger,
    ):
        response = client.get("/discount-credits")
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"discount", "discount_history", "disclaimer"}
    assert data["discount"] == {
        "enabled": False,
        "discount_percent": "0.99",
        "reward_share": "0",
        "balance_usd": "0.000000",
        "lifetime_granted_usd": "0.000000",
        "lifetime_used_usd": "0.000000",
    }
    assert data["discount_history"] == []
    balance.assert_not_called()
    ledger.assert_not_called()


def test_disclaimer_is_neutral_and_carries_no_number_or_promise(disabled):
    with patch.object(routes, "get_balance"), patch.object(routes, "list_ledger"):
        text = client.get("/discount-credits").json()["data"]["disclaimer"]
    assert text == routes.DISCLAIMER
    assert "discount credits" in text.lower() and "vault" in text.lower()
    assert not re.search(r"\d", text)
    lowered = text.lower()
    for word in ("reward", "staking", "delegat", "return", "apy", "inference credits", "free"):
        assert word not in lowered
    assert "not a guaranteed" in lowered


def test_no_route_grants_or_adjusts_discount_credits():
    """Granting is internal only (src/db/inference_discount.py::grant_discount)."""
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if "discount" in path:
            assert path == "/discount-credits" and methods == {"GET"}
        assert "delegation" not in path


def test_an_account_outside_the_pilot_list_sees_the_disabled_shape_and_no_db_read():
    """With a pilot list set, an account that is not on it gets exactly what
    everyone gets while the switch is off."""
    app.dependency_overrides[get_current_user] = lambda: USER
    try:
        with (
            patch.object(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", True),
            patch.object(
                billing.Config, "VAULT_DISCOUNT_PILOT_USER_IDS", frozenset({USER["id"] + 1})
            ),
            patch.object(routes, "get_balance") as get_balance,
            patch.object(routes, "list_ledger") as list_ledger,
        ):
            body = client.get("/discount-credits").json()
        assert body["data"]["discount"]["enabled"] is False
        assert body["data"]["discount_history"] == []
        get_balance.assert_not_called()
        list_ledger.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_an_account_on_the_pilot_list_sees_its_balance():
    app.dependency_overrides[get_current_user] = lambda: USER
    try:
        with (
            patch.object(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", True),
            patch.object(billing.Config, "VAULT_DISCOUNT_PILOT_USER_IDS", frozenset({USER["id"]})),
            patch.object(routes, "get_balance", return_value=BALANCE),
            patch.object(routes, "list_ledger", return_value=[]),
        ):
            body = client.get("/discount-credits").json()
        assert body["data"]["discount"]["enabled"] is True
        assert body["data"]["discount"]["balance_usd"] == "12.500000"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
