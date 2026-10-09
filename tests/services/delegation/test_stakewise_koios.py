"""Tests for the read-only chain clients: the StakeWise V3 vault reader and
the Koios client. Both must fail over / fail closed without ever putting an
RPC URL or API key into an error message."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.services.delegation import koios, stakewise
from src.services.holdings.chains import RpcEndpoint, reset_endpoint_health

KEY = "sekretAlchemyKey123"
VAULT = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _vault(monkeypatch):
    monkeypatch.setattr(stakewise.Config, "STAKEWISE_VAULT_ADDRESS", VAULT)
    monkeypatch.setattr(stakewise.Config, "STAKEWISE_VAULT_CHAIN_ID", 1)
    reset_endpoint_health()
    stakewise.reset_fee_cache()
    yield
    reset_endpoint_health()


def _client(behaviour):
    """A fake Web3 whose contract functions run `behaviour(name, args)`."""

    class Fn:
        def __init__(self, name, args):
            self.name, self.args = name, args

        def call(self):
            return behaviour(self.name, self.args)

    class Functions:
        def __getattr__(self, name):
            return lambda *args: Fn(name, args)

    contract = SimpleNamespace(functions=Functions())
    return SimpleNamespace(eth=SimpleNamespace(contract=lambda address, abi: contract))


def test_abi_carries_the_verified_v3_names():
    names = {entry["name"] for entry in stakewise.VAULT_ABI}
    assert names == {"getShares", "convertToAssets", "totalAssets", "feePercent", "feeRecipient"}


def test_unset_or_malformed_vault_is_unconfigured(monkeypatch):
    monkeypatch.setattr(stakewise.Config, "STAKEWISE_VAULT_ADDRESS", None)
    assert stakewise.vault_address() is None
    monkeypatch.setattr(stakewise.Config, "STAKEWISE_VAULT_ADDRESS", "not-an-address")
    assert stakewise.vault_address() is None


def test_transport_failure_fails_over_to_the_next_endpoint(monkeypatch):
    endpoints = [
        RpcEndpoint(url=f"https://eth-mainnet.g.alchemy.com/v2/{KEY}", source="alchemy"),
        RpcEndpoint(url="https://ethereum-rpc.publicnode.com", source="public"),
    ]
    monkeypatch.setattr(stakewise, "rpc_endpoints_for_chain", lambda chain_id: endpoints)

    def make(url):
        if KEY in url:
            return _client(
                lambda n, a: (_ for _ in ()).throw(
                    requests.ConnectionError(f"Max retries exceeded with url: /v2/{KEY}")
                )
            )
        return _client(lambda n, a: 7 if n == "getShares" else 0)

    monkeypatch.setattr(stakewise, "_make_client", make)
    assert stakewise.read_shares("0x" + "1" * 40) == 7


def test_error_message_never_carries_the_url_or_key(monkeypatch):
    endpoints = [RpcEndpoint(url=f"https://eth-mainnet.g.alchemy.com/v2/{KEY}", source="alchemy")]
    monkeypatch.setattr(stakewise, "rpc_endpoints_for_chain", lambda chain_id: endpoints)
    error = requests.HTTPError(f"401 for url: https://eth-mainnet.g.alchemy.com/v2/{KEY}")
    error.response = SimpleNamespace(status_code=401)
    monkeypatch.setattr(
        stakewise, "_make_client", lambda url: _client(lambda n, a: (_ for _ in ()).throw(error))
    )
    with pytest.raises(stakewise.VaultReadError) as exc:
        stakewise.read_shares("0x" + "1" * 40)
    assert KEY not in str(exc.value) and "alchemy" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_fee_percent_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(
        stakewise,
        "rpc_endpoints_for_chain",
        lambda chain_id: [RpcEndpoint(url="https://x.example", source="public")],
    )
    monkeypatch.setattr(
        stakewise, "_make_client", lambda url: _client(lambda n, a: calls.append(n) or 9900)
    )
    assert stakewise.cached_fee_percent_bps() == 9900
    assert stakewise.cached_fee_percent_bps() == 9900
    assert calls == ["feePercent"]


class TestKoios:
    def _response(self, payload, status=200):
        response = MagicMock()
        response.status_code = status
        response.json.return_value = payload
        if status >= 400:
            err = requests.HTTPError(f"{status} for url: https://api.koios.rest/x")
            err.response = response
            response.raise_for_status.side_effect = err
        return response

    def test_paginates_until_a_short_page(self, monkeypatch):
        monkeypatch.setattr(koios, "PAGE_SIZE", 2)
        pages = [[{"stake_address": "a"}, {"stake_address": "b"}], [{"stake_address": "c"}]]
        with patch.object(
            koios.requests, "get", side_effect=[self._response(p) for p in pages]
        ) as get:
            rows = koios.list_pool_delegators("pool1abc")
        assert [r["stake_address"] for r in rows] == ["a", "b", "c"]
        assert get.call_args_list[1].kwargs["params"]["offset"] == 2

    def test_api_key_goes_in_the_header_and_never_in_errors(self, monkeypatch):
        monkeypatch.setattr(koios.Config, "KOIOS_API_KEY", KEY)
        with patch.object(koios.requests, "get", return_value=self._response([], 503)) as get:
            with pytest.raises(koios.KoiosError) as exc:
                koios.get_pool_history("pool1abc")
        assert get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {KEY}"
        assert KEY not in get.call_args.args[0]
        assert KEY not in str(exc.value) and "koios.rest" not in str(exc.value)
        assert "HTTP 503" in str(exc.value)

    def test_a_failed_page_fails_the_whole_read(self, monkeypatch):
        monkeypatch.setattr(koios, "PAGE_SIZE", 1)
        responses = [self._response([{"stake_address": "a"}]), self._response([], 500)]
        with patch.object(koios.requests, "get", side_effect=responses):
            with pytest.raises(koios.KoiosError):
                koios.list_pool_delegators("pool1abc")

    def test_tip_epoch(self):
        with patch.object(koios.requests, "get", return_value=self._response([{"epoch_no": 660}])):
            assert koios.get_tip_epoch() == 660
