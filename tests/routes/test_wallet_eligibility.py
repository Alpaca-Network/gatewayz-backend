"""Tests for the US / Canada block outside the link flow:
POST /wallet/eligibility records the attestation (451 otherwise), and
GET /discount-credits keeps answering for a restricted account -- reads are
never gated."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import src.routes.discount_credits as discount_routes
import src.routes.wallet_auth_cardano as cardano_routes
from src.main import app
from src.security.deps import get_account_owner_id, get_current_user
from src.services.wallet import eligibility

client = TestClient(app)
USER = {"id": 42, "email": "staker@example.com"}


@pytest.fixture(autouse=True)
def _user(monkeypatch):
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_account_owner_id] = lambda: USER["id"]
    app.dependency_overrides[cardano_routes.wallet_eligibility_rl] = lambda: None
    monkeypatch.setattr(eligibility, "record_audit", lambda *a, **k: None)
    monkeypatch.setattr(eligibility.Config, "WALLET_LINK_COUNTRY_HEADER", "")
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


class TestAttest:
    def test_requires_a_caller(self):
        app.dependency_overrides.pop(get_current_user)
        app.dependency_overrides.pop(get_account_owner_id)
        response = client.post("/wallet/eligibility", json={"not_us_ca_person": True})
        assert response.status_code in (401, 403)

    def test_without_the_attestation_is_451(self):
        with patch.object(eligibility, "get_eligibility", return_value={}):
            response = client.post("/wallet/eligibility", json={})
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
            response = client.post("/wallet/eligibility", json={"not_us_ca_person": True})
        assert response.status_code == 200
        assert response.json()["data"]["eligibility"]["status"] == "eligible"
        record.assert_called_once_with(42, eligibility.ATTESTATION_VERSION, None)

    def test_restricted_account_is_451(self):
        with patch.object(eligibility, "get_eligibility", return_value={"status": "restricted"}):
            response = client.post("/wallet/eligibility", json={"not_us_ca_person": True})
        assert (response.status_code, response.json()["error"]["type"]) == (
            451,
            "region_restricted",
        )


def test_restricted_account_can_still_read_its_discount_credits():
    """Reads are never gated: the eligibility record is not even consulted."""
    with (
        patch.object(eligibility, "get_eligibility") as lookup,
        patch.object(discount_routes, "get_balance", return_value=None),
        patch.object(discount_routes, "list_ledger", return_value=[]),
    ):
        response = client.get("/discount-credits")
    assert response.status_code == 200
    assert "discount" in response.json()["data"]
    lookup.assert_not_called()
