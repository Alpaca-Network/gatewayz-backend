"""The server keeps the IP country database file up to date
(src/services/wallet/ip_country_fetch.py). No network: httpx is mocked."""

from __future__ import annotations

import gzip
import os
import time
from datetime import UTC, datetime

import httpx
import pytest

from src.services.wallet import ip_country_fetch as fetch
from tests.services.wallet.mmdb_writer import write_mmdb

NETWORKS = {
    "8.8.8.0/24": {"country": {"iso_code": "US"}},
    "85.240.0.0/13": {"country": {"iso_code": "PT"}},
}
TEMPLATE = "https://example.test/country-{month}.mmdb.gz"


@pytest.fixture
def db_bytes(tmp_path):
    return write_mmdb(tmp_path / "source.mmdb", NETWORKS).read_bytes()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    target = tmp_path / "data" / "ip-country.mmdb"
    monkeypatch.setattr(fetch.Config, "WALLET_LINK_IP_COUNTRY_DB_URL", TEMPLATE, raising=False)
    monkeypatch.setattr(fetch.Config, "WALLET_LINK_IP_COUNTRY_DB_PATH", str(target))
    return target


def _client(responses: dict[str, httpx.Response]) -> httpx.Client:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return responses.get(str(request.url), httpx.Response(404))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client


def _month_urls():
    return fetch.candidate_urls(TEMPLATE)


def test_candidate_urls_try_this_month_then_last_month():
    urls = fetch.candidate_urls(TEMPLATE, datetime(2026, 1, 5, tzinfo=UTC))
    assert urls == [
        "https://example.test/country-2026-01.mmdb.gz",
        "https://example.test/country-2025-12.mmdb.gz",
    ]
    assert fetch.candidate_urls("https://example.test/fixed.mmdb") == [
        "https://example.test/fixed.mmdb"
    ]


def test_off_unless_both_settings_are_set(monkeypatch):
    monkeypatch.setattr(fetch.Config, "WALLET_LINK_IP_COUNTRY_DB_URL", "", raising=False)
    monkeypatch.setattr(fetch.Config, "WALLET_LINK_IP_COUNTRY_DB_PATH", "/tmp/x.mmdb")
    assert fetch.refresh(client=_client({})) == "off"


def test_a_missing_file_is_downloaded_unpacked_and_installed(configured, db_bytes):
    this_month = _month_urls()[0]
    client = _client({this_month: httpx.Response(200, content=gzip.compress(db_bytes))})
    assert fetch.refresh(client=client) == "updated"
    assert configured.read_bytes() == db_bytes


def test_last_month_is_used_when_this_month_is_not_published(configured, db_bytes):
    last_month = _month_urls()[1]
    client = _client({last_month: httpx.Response(200, content=gzip.compress(db_bytes))})
    assert fetch.refresh(client=client) == "updated"
    assert client.seen == _month_urls()


def test_a_fresh_file_is_left_alone(configured, db_bytes):
    configured.parent.mkdir(parents=True)
    configured.write_bytes(db_bytes)
    client = _client({})
    assert fetch.refresh(client=client) == "fresh"
    assert client.seen == []


def test_a_stale_file_is_replaced(configured, db_bytes):
    configured.parent.mkdir(parents=True)
    configured.write_bytes(b"old")
    old = time.time() - (fetch.MAX_AGE_DAYS + 1) * 86400
    os.utime(configured, (old, old))
    client = _client({_month_urls()[0]: httpx.Response(200, content=db_bytes)})
    assert fetch.refresh(client=client) == "updated"
    assert configured.read_bytes() == db_bytes


@pytest.mark.parametrize(
    "body",
    [b"not a database", gzip.compress(b"not a database"), b"\x1f\x8b broken gzip"],
)
def test_a_bad_download_never_replaces_a_working_file(configured, db_bytes, body):
    configured.parent.mkdir(parents=True)
    configured.write_bytes(db_bytes)
    client = _client({url: httpx.Response(200, content=body) for url in _month_urls()})
    assert fetch.refresh(force=True, client=client) == "failed"
    assert configured.read_bytes() == db_bytes
    assert [p.name for p in configured.parent.iterdir()] == [configured.name]


def test_a_database_that_does_not_answer_the_check_address_is_refused(configured, tmp_path):
    other = write_mmdb(tmp_path / "other.mmdb", {"85.240.0.0/13": {"country": {"iso_code": "PT"}}})
    body = other.read_bytes()
    client = _client({url: httpx.Response(200, content=body) for url in _month_urls()})
    assert fetch.refresh(client=client) == "failed"
    assert not configured.exists()


def test_http_errors_are_reported_not_raised(configured):
    client = _client({url: httpx.Response(500) for url in _month_urls()})
    assert fetch.refresh(client=client) == "failed"


def test_only_https_is_accepted(configured, monkeypatch, db_bytes):
    monkeypatch.setattr(
        fetch.Config, "WALLET_LINK_IP_COUNTRY_DB_URL", "http://example.test/c.mmdb", raising=False
    )
    client = _client({"http://example.test/c.mmdb": httpx.Response(200, content=db_bytes)})
    assert fetch.refresh(client=client) == "failed"
    assert client.seen == []


def test_an_oversized_download_is_refused(configured, monkeypatch, db_bytes):
    monkeypatch.setattr(fetch, "MAX_DOWNLOAD_BYTES", 10)
    client = _client({url: httpx.Response(200, content=db_bytes) for url in _month_urls()})
    assert fetch.refresh(client=client) == "failed"
    assert not configured.exists()
