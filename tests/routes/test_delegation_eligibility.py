"""Tests for the US / Canada block on the delegated-staking routes:
POST /delegation/eligibility records the attestation (451 otherwise), and
GET /delegation/rewards keeps showing a restricted account its positions --
reads and exits are never gated."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.delegation as delegation_routes
from src.main import app
from src.security.deps import get_current_user
from src.services.delegation import eligibility

client = TestClient(app)
USER = {"id": 42, "email": "staker@example.com"}
WALLET = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _user(monkeypatch):
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: USER
    monkeypatch.setattr(eligibility, "record_audit", lambda *a, **k: None)
    monkeypatch.setattr(eligibility.Config, "DELEGATION_COUNTRY_HEADER", "")
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


class TestAttest:
    def test_requires_a_caller(self):
        app.dependency_overrides.pop(get_current_user)
        response = client.post("/delegation/eligibility", json={"not_us_ca_person": True})
        assert response.status_code in (401, 403)

    def test_without_the_attestation_is_451(self):
        with patch.object(eligibility, "get_eligibility", return_value={}):
            response = client.post("/delegation/eligibility", json={})
        assert (response.status_code, response.json()["error"]["type"]) == (
            451,
            "attestation_required",
        )

    def test_attesting_records_it(self):
        row = {"status": "eligible", "attested_at": "2026-10-14T00:00:00+00:00"}
        with (
            patch.object(eligibility, "get_eligibility", side_effect=[{}, row]),
            patch.object(eligibility, "record_attestation", return_value=row) as record,
        ):
            response = client.post("/delegation/eligibility", json={"not_us_ca_person": True})
        assert response.status_code == 200
        assert response.json()["data"]["eligibility"]["status"] == "eligible"
        record.assert_called_once_with(42, eligibility.ATTESTATION_VERSION, None)

    def test_restricted_account_is_451(self):
        with patch.object(eligibility, "get_eligibility", return_value={"status": "restricted"}):
            response = client.post("/delegation/eligibility", json={"not_us_ca_person": True})
        assert (response.status_code, response.json()["error"]["type"]) == (
            451,
            "region_restricted",
        )


def test_restricted_account_still_sees_its_positions():
    measurement = {
        "asset": "eth",
        "amount": "1000000000000000000",
        "usd_value": "2000",
        "measured_at": "2026-10-14T06:10:00+00:00",
    }
    with (
        patch.object(eligibility, "get_eligibility", return_value={"status": "restricted"}),
        patch.object(
            delegation_routes, "get_wallets_for_user", return_value=[{"wallet_address": WALLET}]
        ),
        patch.object(delegation_routes, "get_latest_measurements", return_value=[measurement]),
        patch.object(delegation_routes, "get_active_rates", return_value={}),
        patch.object(delegation_routes, "list_accruals_for_wallet", return_value=[]),
        patch.object(delegation_routes, "get_balance", return_value=None),
        patch.object(delegation_routes, "list_attributions_for_account", return_value=[]),
        patch.object(delegation_routes, "list_ledger", return_value=[]),
    ):
        response = client.get("/delegation/rewards")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["eligibility"]["status"] == "restricted"
    assert [p["wallet_address"] for p in data["positions"]] == [WALLET]
