"""The Alchemy key must never leave the RPC layer.

The key lives in the URL path (https://{net}.g.alchemy.com/v2/{key}), and
requests puts the request URL in its exception messages -- "401 Client
Error: Unauthorized for url: <url>", "Max retries exceeded with url:
/v2/<key>". These tests drive a real sweep end to end through a real
read_balances (only the web3 client is faked), raise exactly those errors,
and check that the key is in no log record, no sweep summary, no
ChainReadFailure and no ops alert payload. Nothing touches a network.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

import src.services.holdings.alerts as alerts
import src.services.holdings.chains as chains
import src.services.holdings.snapshots as snapshots
from src.config.config import Config
from src.services.holdings.chains import (
    CHAIN_ID_ETHEREUM,
    CHAIN_ID_POLYGON,
    TokenRef,
    describe_rpc_error,
    read_balances,
)
from src.services.holdings.prices import PricePoint

KEY = "FAKEalchemyKEY0123456789abcdef"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
WALLET = "0x742d35cc6634c0532925a3b844bc454e4438f44e"

ETH_NATIVE = TokenRef(CHAIN_ID_ETHEREUM, None, 18, "ETH", "ethereum")
POL_NATIVE = TokenRef(CHAIN_ID_POLYGON, None, 18, "POL", "matic-network")


def _http_error(url: str, status: int = 401) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.url = url
    return requests.HTTPError(
        f"{status} Client Error: Unauthorized for url: {url}", response=response
    )


def _connection_error() -> requests.ConnectionError:
    return requests.ConnectionError(
        "HTTPSConnectionPool(host='polygon-mainnet.g.alchemy.com', port=443): "
        f"Max retries exceeded with url: /v2/{KEY} (Caused by NewConnectionError('refused'))"
    )


def _failing_client(error: Exception) -> MagicMock:
    client = MagicMock()
    client.eth.get_balance.side_effect = error
    client.eth.contract.side_effect = error
    return client


@pytest.fixture(autouse=True)
def _alchemy_everywhere(monkeypatch):
    """Alchemy primary on every chain; public fallbacks behind it."""
    monkeypatch.setattr(Config, "ALCHEMY_API_KEY", KEY)
    for attr in chains._RPC_CONFIG_ATTR.values():
        monkeypatch.setattr(Config, attr, None)
    chains.reset_endpoint_health()
    alerts._reset_for_tests()
    yield
    chains.reset_endpoint_health()
    alerts._reset_for_tests()


@pytest.fixture
def every_endpoint_fails():
    """Each endpoint fails with the error that carries its own URL: an
    HTTPError naming the full URL on HTTP-style failures, and the urllib3
    path-only form on connection failures."""

    def _make(chain_id, rpc_url):
        if chain_id == CHAIN_ID_POLYGON:
            return _failing_client(_connection_error())
        return _failing_client(_http_error(rpc_url))

    with patch("src.services.holdings.chains._make_web3", side_effect=_make):
        yield


def _assert_clean(*blobs):
    for blob in blobs:
        text = blob if isinstance(blob, str) else json.dumps(blob, default=str)
        assert KEY not in text
        assert "/v2/" not in text or "/v2/***" in text


def test_the_errors_under_test_really_carry_the_key():
    """Guard against a vacuous test: the raw messages DO contain it."""
    url = f"https://eth-mainnet.g.alchemy.com/v2/{KEY}"
    assert KEY in str(_http_error(url))
    assert KEY in str(_connection_error())


def test_describe_uses_class_status_and_code_only():
    url = f"https://eth-mainnet.g.alchemy.com/v2/{KEY}"
    assert describe_rpc_error(_http_error(url, 429)) == "HTTPError HTTP 429"
    assert describe_rpc_error(_connection_error()) == "ConnectionError"


def test_read_balances_failures_and_logs_are_clean(every_endpoint_fails, caplog):
    with caplog.at_level(logging.DEBUG):
        result = read_balances(WALLET, [ETH_NATIVE, POL_NATIVE])

    assert sorted(result.failed_chain_ids) == [CHAIN_ID_ETHEREUM, CHAIN_ID_POLYGON]
    reasons = [f.reason for f in result.failures]
    assert "HTTPError HTTP 401" in reasons and "ConnectionError" in reasons
    _assert_clean(reasons, repr(result))
    # Every attempt was logged (primary + fallbacks) and none leaked.
    assert any("retrying on fallback (public)" in r.getMessage() for r in caplog.records)
    _assert_clean(*[r.getMessage() for r in caplog.records])


def test_a_full_sweep_and_its_alert_are_clean(every_endpoint_fails, monkeypatch, caplog):
    rows = [
        {
            "id": 1,
            "chain_id": 1,
            "contract_address": None,
            "decimals": 18,
            "symbol": "ETH",
            "price_id": "ethereum",
        },
        {
            "id": 2,
            "chain_id": 137,
            "contract_address": None,
            "decimals": 18,
            "symbol": "POL",
            "price_id": "matic-network",
        },
    ]
    monkeypatch.setattr(Config, "HOLDINGS_REWARDS_ENABLED", True)
    monkeypatch.setattr(Config, "HOLDINGS_MIN_WALLET_AGE_DAYS", 3)
    monkeypatch.setattr(snapshots, "list_enabled_tokens", lambda: rows)
    monkeypatch.setattr(
        snapshots,
        "list_all_wallets",
        lambda: [{"wallet_address": WALLET, "created_at": (NOW - timedelta(days=30)).isoformat()}],
    )
    monkeypatch.setattr(
        snapshots,
        "get_usd_prices",
        lambda ids: {i: PricePoint(price=1, as_of=NOW) for i in ids},
    )
    monkeypatch.setattr(snapshots, "record_sweep", MagicMock(return_value=None))
    monkeypatch.setattr(snapshots, "record_snapshot", MagicMock(return_value=None))

    sent: list[dict] = []

    def _send_email(to, subject, html, text=None, tags=None):  # noqa: ARG001
        sent.append({"to": to, "subject": subject, "html": html, "text": text})
        return MagicMock(sent=True)

    with (
        caplog.at_level(logging.DEBUG),
        patch("src.services.holdings.alerts.get_redis_client", return_value=None),
        patch(
            "src.services.provider_alerting.resolve_ops_recipients",
            return_value=(["ops@example.com"], "env"),
        ),
        patch("src.services.email.send_email", side_effect=_send_email),
    ):
        summary = snapshots.run_holdings_snapshots_once(now=NOW)
        assert alerts.alert_if_sweep_recorded_nothing(summary) is True

    assert summary["sweeps_recorded"] == 0
    assert summary["failed_chains"] == {"1": 1, "137": 1}
    assert len(sent) == 1
    _assert_clean(summary, sent, *[r.getMessage() for r in caplog.records])
    # The wallet address stays out of the alert too.
    assert WALLET not in json.dumps(sent)


@pytest.mark.parametrize(
    "logger_name",
    [
        "urllib3.connectionpool",
        "web3.providers.HTTPProvider",
        "web3._utils.http_session_manager.HTTPSessionManager",
    ],
)
def test_http_library_debug_lines_are_scrubbed(logger_name, caplog):
    """web3 and urllib3 print the endpoint URI at DEBUG -- with the key in
    it. A LOG_LEVEL=DEBUG deploy must not ship it."""
    lib_logger = logging.getLogger(logger_name)
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        lib_logger.debug(
            '%s "POST /v2/%s HTTP/1.1" 401 0', "https://eth-mainnet.g.alchemy.com:443", KEY
        )
        lib_logger.debug("Making request HTTP. URI: https://eth-mainnet.g.alchemy.com/v2/%s", KEY)

    messages = [r.getMessage() for r in caplog.records if r.name == logger_name]
    assert len(messages) == 2
    _assert_clean(*messages)


def test_a_non_alchemy_key_in_an_explicit_url_is_also_kept_out(monkeypatch, caplog):
    """An explicit <CHAIN>_RPC_URL can carry its own key (Infura /v3/<id>,
    a second Alchemy key) that the ALCHEMY_API_KEY mask knows nothing about."""
    other = "OTHERinfuraPROJECTid0123456789"
    monkeypatch.setattr(Config, "ETHEREUM_RPC_URL", f"https://mainnet.infura.io/v3/{other}")

    def _make(chain_id, rpc_url):  # noqa: ARG001
        return _failing_client(_http_error(rpc_url, 403))

    with (
        patch("src.services.holdings.chains._make_web3", side_effect=_make),
        caplog.at_level(logging.DEBUG),
    ):
        result = read_balances(WALLET, [ETH_NATIVE])

    text = " ".join([r.getMessage() for r in caplog.records] + [f.reason for f in result.failures])
    assert other not in text and KEY not in text
