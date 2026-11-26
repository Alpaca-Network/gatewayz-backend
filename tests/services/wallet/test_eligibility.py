"""Tests for src/services/wallet/eligibility.py -- the US / Canada block.
Counsel cleared the program everywhere except the US and Canada; an account
must attest before it links a Cardano wallet, and a restricted country is
sticky. The DB and the audit log are mocked; IP country databases are small
files written by tests/services/wallet/mmdb_writer.py."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.services.wallet import eligibility, ip_country
from tests.services.wallet.mmdb_writer import write_mmdb

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
        eligibility.Config, "WALLET_LINK_RESTRICTED_COUNTRIES", frozenset({"US", "CA"})
    )
    monkeypatch.setattr(eligibility.Config, "WALLET_LINK_COUNTRY_HEADER", "")
    monkeypatch.setattr(eligibility.Config, "WALLET_LINK_IP_COUNTRY_DB_PATH", "")
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
        monkeypatch.setattr(eligibility.Config, "WALLET_LINK_COUNTRY_HEADER", "cf-ipcountry")
        for country in ("US", "ca"):
            store.row = {}
            status = _status(
                lambda: eligibility.require_eligible(
                    USER_ID, _request({"cf-ipcountry": country}), True
                )
            )
            assert status == (451, "region_restricted")
        assert [c for _, c, _ in store.restricted] == ["US", "CA"]
        # Attesting later, from anywhere, never clears it.
        status = _status(
            lambda: eligibility.require_eligible(USER_ID, _request({"cf-ipcountry": "FR"}), True)
        )
        assert status == (451, "region_restricted")
        assert not eligibility.account_eligible(USER_ID)

    def test_header_ignored_unless_configured(self, store):
        eligibility.require_eligible(USER_ID, _request({"cf-ipcountry": "US"}), True)
        assert store.restricted == []

    def test_other_countries_pass_with_attestation(self, store, monkeypatch):
        monkeypatch.setattr(eligibility.Config, "WALLET_LINK_COUNTRY_HEADER", "cf-ipcountry")
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


US_IP, PT_IP, UNKNOWN_IP = "8.8.8.8", "85.240.0.1", "1.1.1.1"


@pytest.fixture
def geo(store, tmp_path, monkeypatch):
    """A configured IP country database and the production client-IP header."""
    path = write_mmdb(
        tmp_path / "country.mmdb",
        {
            "8.8.8.0/24": {"country": {"iso_code": "US"}},
            "24.48.0.0/16": {"registered_country": {"iso_code": "CA"}},
            "85.240.0.0/13": {"country": {"iso_code": "PT"}},
        },
    )
    # Other test modules may reload src.config.config, leaving more than one
    # Config class alive: patch every copy the code under test can read.
    configs = {eligibility.Config, importlib.import_module("src.config.config").Config}
    configs.add(getattr(ip_country, "Config", eligibility.Config))
    for config in configs:
        monkeypatch.setattr(config, "WALLET_LINK_IP_COUNTRY_DB_PATH", str(path), raising=False)
        monkeypatch.setattr(config, "TRUSTED_CLIENT_IP_HEADER", "x-real-ip", raising=False)
    monkeypatch.setattr(ip_country, "_state", (None, None, None))
    return path


def _ip_request(ip, headers=None):
    return SimpleNamespace(headers={"X-Real-IP": ip, **(headers or {})}, client=None)


def _audit_metadata(action):
    calls = [c for c in eligibility.record_audit.call_args_list if c.kwargs["action"] == action]
    assert len(calls) == 1
    return calls[0].kwargs["metadata"]


class TestIpCountry:
    def test_restricted_ip_country_is_refused_and_sticks(self, store, geo):
        for ip, country in ((US_IP, "US"), ("24.48.1.1", "CA")):
            store.row = {}
            status = _status(lambda: eligibility.require_eligible(USER_ID, _ip_request(ip), True))
            assert status == (451, "region_restricted")
            assert store.restricted[-1] == (USER_ID, country, "ip_country")
        assert store.attested == []
        metadata = eligibility.record_audit.call_args.kwargs["metadata"]
        assert metadata["country_source"] == "ip" and metadata["country"] == "CA"
        # Sticky: a later request from an allowed country is still refused.
        status = _status(lambda: eligibility.require_eligible(USER_ID, _ip_request(PT_IP), True))
        assert status == (451, "region_restricted")
        assert not eligibility.account_eligible(USER_ID)

    def test_allowed_ip_country_takes_the_attestation_path(self, store, geo):
        status = _status(lambda: eligibility.require_eligible(USER_ID, _ip_request(PT_IP), False))
        assert status == (451, "attestation_required")
        eligibility.require_eligible(USER_ID, _ip_request(PT_IP), True)
        assert store.restricted == []
        assert store.attested == [(USER_ID, eligibility.ATTESTATION_VERSION, "PT")]
        metadata = _audit_metadata("wallet.eligibility.attested")
        assert (metadata["country"], metadata["country_source"]) == ("PT", "ip")

    def test_header_wins_over_ip(self, store, geo, monkeypatch):
        monkeypatch.setattr(eligibility.Config, "WALLET_LINK_COUNTRY_HEADER", "cf-ipcountry")
        # Header says allowed, IP says US: the header decides.
        eligibility.require_eligible(USER_ID, _ip_request(US_IP, {"cf-ipcountry": "FR"}), True)
        assert store.restricted == []
        assert _audit_metadata("wallet.eligibility.attested")["country_source"] == "header"
        # Header says US, IP says PT: refused, recorded as the header.
        store.row = {}
        status = _status(
            lambda: eligibility.require_eligible(
                USER_ID, _ip_request(PT_IP, {"cf-ipcountry": "US"}), True
            )
        )
        assert status == (451, "region_restricted")
        assert store.restricted == [(USER_ID, "US", "request_country")]
        metadata = _audit_metadata("wallet.eligibility.restricted")
        assert metadata["country_source"] == "header"

    def test_header_configured_but_absent_falls_back_to_ip(self, store, geo, monkeypatch):
        monkeypatch.setattr(eligibility.Config, "WALLET_LINK_COUNTRY_HEADER", "cf-ipcountry")
        status = _status(lambda: eligibility.require_eligible(USER_ID, _ip_request(US_IP), True))
        assert status == (451, "region_restricted")
        assert store.restricted == [(USER_ID, "US", "ip_country")]

    @pytest.mark.parametrize("ip", [UNKNOWN_IP, "10.0.0.1", "not-an-ip"])
    def test_unknown_ip_is_attestation_only(self, store, geo, ip):
        eligibility.require_eligible(USER_ID, _ip_request(ip), True)
        assert store.restricted == []
        assert store.attested == [(USER_ID, eligibility.ATTESTATION_VERSION, None)]
        assert _audit_metadata("wallet.eligibility.attested")["country_source"] == "none"

    @pytest.mark.timeout(10)
    @pytest.mark.parametrize("content", [None, b"", b"corrupt" * 100])
    def test_missing_or_unreadable_db_is_attestation_only(self, store, geo, content):
        if content is None:
            geo.unlink()
        else:
            geo.write_bytes(content)
        status = _status(lambda: eligibility.require_eligible(USER_ID, _ip_request(US_IP), False))
        assert status == (451, "attestation_required")
        eligibility.require_eligible(USER_ID, _ip_request(US_IP), True)
        assert store.restricted == []
        assert store.attested == [(USER_ID, eligibility.ATTESTATION_VERSION, None)]
        assert _audit_metadata("wallet.eligibility.attested")["country_source"] == "none"

    def test_db_off_ignores_the_ip(self, store):
        eligibility.require_eligible(USER_ID, _ip_request(US_IP), True)
        assert store.restricted == []

    def test_request_without_client_ip_is_unknown(self, store, geo):
        no_ip = SimpleNamespace(headers={})
        assert eligibility.request_country_and_source(no_ip) == (None, "none")
        assert eligibility.request_country_and_source(None) == (None, "none")
