"""Tests for GET /discount-credits (src/routes/discount_credits.py): the
caller's discount credits balance and recent movements. The DB is mocked."""

from __future__ import annotations

import re
from datetime import date
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
    monkeypatch.setattr(billing.Config, "VAULT_DISCOUNT_ALL_ACCOUNTS", True)


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
        "daily_cap": body["data"]["discount"]["daily_cap"],
    }
    # No cap columns on the mocked row: the default cap, nothing used today.
    cap = body["data"]["discount"]["daily_cap"]
    assert cap["daily_cap_usd"] == "50.000000"
    assert cap["used_today_usd"] == "0.000000"
    assert cap["left_today_usd"] == "50.000000"
    assert cap["resets_at"].endswith("T00:00:00Z")
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
        # The share is shown only to accounts it applies to.
        "discount_percent": None,
        "reward_share": "0",
        "balance_usd": "0.000000",
        "lifetime_granted_usd": "0.000000",
        "lifetime_used_usd": "0.000000",
        "daily_cap": {
            "daily_cap_usd": None,
            "used_today_usd": "0.000000",
            "left_today_usd": "0.000000",
            "resets_at": None,
            "next_daily_cap_usd": None,
            "next_daily_cap_from": None,
        },
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
    """Granting is internal only (src/db/inference_discount.py::grant_discount).
    The only write route sets a limit on use; it cannot change a balance."""
    allowed = {"/discount-credits": {"GET"}, "/discount-credits/daily-cap": {"PUT"}}
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if "discount" in path:
            assert allowed.get(path) == methods, (path, methods)
        assert "delegation" not in path


def test_an_account_outside_the_pilot_list_sees_the_disabled_shape_and_no_db_read():
    """With a pilot list set, an account that is not on it gets exactly what
    everyone gets while the switch is off."""
    app.dependency_overrides[get_current_user] = lambda: USER
    try:
        with (
            patch.object(billing.Config, "VAULT_DISCOUNT_BILLING_ENABLED", True),
            patch.object(billing.Config, "VAULT_DISCOUNT_ALL_ACCOUNTS", False),
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
            patch.object(billing.Config, "VAULT_DISCOUNT_ALL_ACCOUNTS", False),
            patch.object(billing.Config, "VAULT_DISCOUNT_PILOT_USER_IDS", frozenset({USER["id"]})),
            patch.object(routes, "get_balance", return_value=BALANCE),
            patch.object(routes, "list_ledger", return_value=[]),
        ):
            body = client.get("/discount-credits").json()
        assert body["data"]["discount"]["enabled"] is True
        assert body["data"]["discount"]["balance_usd"] == "12.500000"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# --- The daily cap -----------------------------------------------------------

TODAY = date(2026, 11, 25)


def _cap(row):
    return routes._cap_view(row, today=TODAY)


def test_cap_view_counts_only_use_made_today():
    row = {"daily_cap_usd": "50", "cap_used_usd": "12.5", "cap_day": "2026-11-25"}
    view = _cap(row)
    assert view["used_today_usd"] == "12.500000"
    assert view["left_today_usd"] == "37.500000"
    assert view["resets_at"] == "2026-11-26T00:00:00Z"

    stale = _cap({**row, "cap_day": "2026-11-24"})
    assert stale["used_today_usd"] == "0.000000"
    assert stale["left_today_usd"] == "50.000000"


def test_cap_view_shows_a_pending_raise_until_its_day_comes():
    row = {
        "daily_cap_usd": "50",
        "cap_used_usd": "50",
        "cap_day": "2026-11-25",
        "next_daily_cap_usd": "80",
        "next_daily_cap_from": "2026-11-26",
    }
    view = _cap(row)
    assert view["daily_cap_usd"] == "50.000000"
    assert view["left_today_usd"] == "0.000000"
    assert view["next_daily_cap_usd"] == "80.000000"
    assert view["next_daily_cap_from"] == "2026-11-26"

    next_day = routes._cap_view(row, today=date(2026, 11, 26))
    assert next_day["daily_cap_usd"] == "80.000000"
    assert next_day["used_today_usd"] == "0.000000"
    assert next_day["next_daily_cap_usd"] is None and next_day["next_daily_cap_from"] is None


def test_setting_the_cap_returns_the_new_view(enabled):
    row = {"daily_cap_usd": "20", "cap_used_usd": "0", "cap_day": "2026-11-25"}
    with (
        patch.object(routes, "set_daily_cap", return_value={"status": "set"}) as set_cap,
        patch.object(routes, "get_balance", return_value=row),
    ):
        response = client.put("/discount-credits/daily-cap", json={"daily_cap_usd": "20"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "set"
    assert data["daily_cap"]["daily_cap_usd"] == "20.000000"
    set_cap.assert_called_once_with(42, Decimal("20"))


def test_a_raise_is_reported_as_scheduled(enabled):
    with (
        patch.object(routes, "set_daily_cap", return_value={"status": "scheduled"}),
        patch.object(routes, "get_balance", return_value={"daily_cap_usd": "50"}),
    ):
        response = client.put("/discount-credits/daily-cap", json={"daily_cap_usd": 80})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "scheduled"


@pytest.mark.parametrize("bad", [-1, "1000000.01", "abc", None, "1.0000001"])
def test_a_bad_cap_is_refused_before_the_database(enabled, bad):
    with patch.object(routes, "set_daily_cap") as set_cap:
        response = client.put("/discount-credits/daily-cap", json={"daily_cap_usd": bad})
    assert response.status_code == 422
    set_cap.assert_not_called()


def test_an_account_outside_the_program_cannot_set_a_cap(disabled):
    with patch.object(routes, "set_daily_cap") as set_cap:
        response = client.put("/discount-credits/daily-cap", json={"daily_cap_usd": 10})
    assert response.status_code == 404
    set_cap.assert_not_called()


def test_a_database_failure_is_a_503_not_a_silent_success(enabled):
    with patch.object(routes, "set_daily_cap", return_value=None):
        response = client.put("/discount-credits/daily-cap", json={"daily_cap_usd": 10})
    assert response.status_code == 503


def test_setting_the_cap_needs_a_signed_in_caller():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        response = client.put("/discount-credits/daily-cap", json={"daily_cap_usd": 10})
    finally:
        app.dependency_overrides.update(saved)
    assert response.status_code in (401, 403)
