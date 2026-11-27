"""Tests for src/routes/billing_card.py. The service is mocked; nothing calls
Stripe or the database."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.billing_card as routes
from src.config.config import Config
from src.main import app
from src.security.deps import get_account_owner_id, get_current_user
from src.services.billing import card_auto_topup as cat

USER_ID = 41
client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ENABLED", True)
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ALL_ACCOUNTS", False)
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_USER_IDS", frozenset({USER_ID}))
    app.dependency_overrides[get_current_user] = lambda: {"id": USER_ID}
    app.dependency_overrides[get_account_owner_id] = lambda: USER_ID
    app.dependency_overrides[routes.card_rl] = lambda: None
    yield
    for dep in (get_current_user, get_account_owner_id, routes.card_rl):
        app.dependency_overrides.pop(dep, None)


def test_get_reports_feature_off_without_reading_the_account(monkeypatch):
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ENABLED", False)
    with patch.object(cat, "_fresh_user") as fresh:
        r = client.get("/user/billing/card")
    assert r.status_code == 200
    assert r.json()["data"]["feature_enabled"] is False
    fresh.assert_not_called()


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/user/billing/card/setup-session", None),
        ("post", "/user/billing/card/confirm", {"session_id": "cs_test_1"}),
        ("delete", "/user/billing/card", None),
        ("put", "/user/billing/auto-topup", {"enabled": True}),
    ],
)
def test_changes_are_404_while_off(monkeypatch, method, path, body):
    monkeypatch.setattr(Config, "CARD_AUTO_TOPUP_ENABLED", False)
    r = getattr(client, method)(path, **({"json": body} if body else {}))
    assert r.status_code == 404


def test_setup_session_returns_the_checkout_url():
    session = {"session_id": "cs_t", "url": "https://x"}
    with patch.object(cat, "create_setup_session", return_value=session):
        r = client.post("/user/billing/card/setup-session")
    assert r.status_code == 200
    assert r.json()["data"]["url"] == "https://x"


def test_service_errors_map_to_their_status():
    refused = cat.CardSetupError("card_required", 409)
    with patch.object(cat, "set_preferences", side_effect=refused):
        r = client.put("/user/billing/auto-topup", json={"enabled": True})
    assert r.status_code == 409


def test_unexpected_errors_are_502_without_details():
    with patch.object(cat, "create_setup_session", side_effect=RuntimeError("secret detail")):
        r = client.post("/user/billing/card/setup-session")
    assert r.status_code == 502
    assert "secret" not in r.text
