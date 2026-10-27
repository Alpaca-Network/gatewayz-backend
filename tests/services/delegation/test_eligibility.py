"""Tests for src/services/delegation/eligibility.py -- the US / Canada block.
Counsel cleared delegated staking everywhere except the US and Canada; an
account must attest before it starts delegating or is granted anything, and a
restricted country is sticky. The DB and the audit log are mocked."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.services.delegation import eligibility, rewards

USER_ID = 42
ATTESTED = {
    "user_id": USER_ID,
    "status": "eligible",
    "attested_at": "2026-10-14T00:00:00+00:00",
    "attestation_version": eligibility.ATTESTATION_VERSION,
}


class FakeStore:
    def __init__(self, row=None):
        self.row = row if row is not None else {}
        self.fail_reads = False
        self.attested: list[tuple] = []
        self.restricted: list[tuple] = []

    def get_eligibility(self, user_id):
        return None if self.fail_reads else dict(self.row)

    def record_attestation(self, user_id, version, country):
        if self.row.get("status") == "restricted":
            return dict(self.row)
        self.attested.append((user_id, version, country))
        self.row = {
            "user_id": user_id,
            "status": "eligible",
            "attested_at": "now",
            "attestation_version": version,
            "country": country,
        }
        return dict(self.row)

    def mark_restricted(self, user_id, country, reason):
        self.restricted.append((user_id, country, reason))
        self.row = {"user_id": user_id, "status": "restricted", "country": country}
        return True


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    for name in ("get_eligibility", "record_attestation", "mark_restricted"):
        monkeypatch.setattr(eligibility, name, getattr(fake, name))
    monkeypatch.setattr(eligibility, "record_audit", MagicMock())
    monkeypatch.setattr(
        eligibility.Config, "DELEGATION_RESTRICTED_COUNTRIES", frozenset({"US", "CA"})
    )
    monkeypatch.setattr(eligibility.Config, "DELEGATION_COUNTRY_HEADER", "")
    return fake


def _request(headers=None):
    return SimpleNamespace(headers=headers or {})


def _status(fn):
    with pytest.raises(HTTPException) as exc:
        fn()
    return exc.value.status_code, exc.value.detail["error"]["type"]


class TestRequireEligible:
    def test_attestation_missing_is_refused(self, store):
        assert _status(lambda: eligibility.require_eligible(USER_ID, _request(), False)) == (
            451,
            "attestation_required",
        )
        assert store.attested == []

    def test_attesting_records_it_and_passes(self, store):
        eligibility.require_eligible(USER_ID, _request(), True)
        assert store.attested == [(USER_ID, eligibility.ATTESTATION_VERSION, None)]
        assert eligibility.account_eligible(USER_ID)
        eligibility.record_audit.assert_called_once()

    def test_earlier_attestation_is_enough(self, store):
        store.row = dict(ATTESTED)
        eligibility.require_eligible(USER_ID, _request(), False)
        assert store.attested == []

    def test_restricted_country_header_is_refused_and_sticks(self, store, monkeypatch):
        monkeypatch.setattr(eligibility.Config, "DELEGATION_COUNTRY_HEADER", "cf-ipcountry")
        for country in ("US", "ca"):
            store.row = {}
            assert _status(
                lambda: eligibility.require_eligible(
                    USER_ID, _request({"cf-ipcountry": country}), True
                )
            ) == (451, "region_restricted")
        assert [c for _, c, _ in store.restricted] == ["US", "CA"]
        # Attesting later, from anywhere, never clears it.
        assert _status(
            lambda: eligibility.require_eligible(USER_ID, _request({"cf-ipcountry": "FR"}), True)
        ) == (451, "region_restricted")
        assert not eligibility.account_eligible(USER_ID)

    def test_header_ignored_unless_configured(self, store):
        eligibility.require_eligible(USER_ID, _request({"cf-ipcountry": "US"}), True)
        assert store.restricted == []

    def test_other_countries_pass_with_attestation(self, store, monkeypatch):
        monkeypatch.setattr(eligibility.Config, "DELEGATION_COUNTRY_HEADER", "cf-ipcountry")
        eligibility.require_eligible(USER_ID, _request({"cf-ipcountry": "FR"}), True)
        assert store.row["country"] == "FR"

    def test_unreadable_eligibility_is_503_not_a_pass(self, store):
        store.fail_reads = True
        with pytest.raises(HTTPException) as exc:
            eligibility.require_eligible(USER_ID, _request(), True)
        assert exc.value.status_code == 503


class TestAccountEligible:
    def test_no_row_failed_read_or_restricted_is_not_eligible(self, store):
        assert not eligibility.account_eligible(USER_ID)
        store.fail_reads = True
        assert not eligibility.account_eligible(USER_ID)
        store.fail_reads = False
        store.row = {"status": "restricted", "attested_at": "earlier"}
        assert not eligibility.account_eligible(USER_ID)

    def test_view(self, store):
        assert eligibility.eligibility_view(USER_ID)["status"] == "none"
        store.row = dict(ATTESTED)
        view = eligibility.eligibility_view(USER_ID)
        assert view["status"] == "eligible"
        assert view["attestation_text"] == eligibility.ATTESTATION_TEXT


class TestPayoutGate:
    """Rewards (v2 and v3 both go through _payee_verified) are granted only to
    an eligible account."""

    @pytest.fixture
    def linked(self, monkeypatch):
        monkeypatch.setattr(rewards, "get_wallet", lambda w: {"user_id": USER_ID})
        monkeypatch.setattr(rewards, "get_user_by_id", lambda u: {"id": u, "is_active": True})

    def test_not_attested_is_not_paid(self, linked, monkeypatch):
        monkeypatch.setattr(rewards, "account_eligible", lambda u: False)
        assert not rewards._payee_verified("0xabc", USER_ID)

    def test_attested_is_paid(self, linked, monkeypatch):
        monkeypatch.setattr(rewards, "account_eligible", lambda u: True)
        assert rewards._payee_verified("0xabc", USER_ID)
