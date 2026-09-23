"""Tests for src.services.chain.eth_payout_client -- native ETH provider
payouts on Base, priced off the Chainlink ETH/USD feed (2026-09-22)."""

from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from hexbytes import HexBytes

from src.services.chain.eth_payout_client import (
    EthPayoutClient,
    EthPayoutClientError,
    EthPriceReader,
    EthUsdPrice,
    SequencerStatus,
    StalePriceError,
    TransferBroadcastError,
    TransferNotSentError,
    gas_limit_with_headroom,
    usd_micros_to_wei,
    validate_price,
    validate_sequencer,
    wei_to_usd_micros,
)


@pytest.fixture
def sb():
    """No-op fixture whose presence bypasses the autouse DB-skip in
    tests/conftest.py -- pure unit tests, everything mocked."""
    return None


def _price(usd: int, updated_at: int = 1_000_000, decimals: int = 8) -> EthUsdPrice:
    return EthUsdPrice(answer=usd * 10**decimals, decimals=decimals, updated_at=updated_at)


# ---------------------------------------------------------------------------
# Pure conversion + price validation
# ---------------------------------------------------------------------------


def test_usd_micros_to_wei_at_3000_usd_per_eth(sb):
    # $3000 == 1 ETH
    assert usd_micros_to_wei(3000 * 10**6, _price(3000)) == 10**18
    # $30 == 0.01 ETH
    assert usd_micros_to_wei(30 * 10**6, _price(3000)) == 10**16


def test_usd_micros_to_wei_floors_never_overpays(sb):
    # $1 at $3000/ETH = 333333333333333.33.. wei -> floor
    assert usd_micros_to_wei(10**6, _price(3000)) == 333_333_333_333_333


def test_usd_micros_to_wei_handles_fractional_feed_answers(sb):
    # $2757.12345678 (8 decimals) -- a realistic Chainlink answer
    price = EthUsdPrice(answer=275_712_345_678, decimals=8, updated_at=0)
    wei = usd_micros_to_wei(2_757_123_456, price)  # $2757.123456
    assert abs(wei - 10**18) < 10**12  # ~1 ETH, within rounding of the 6-dp USD input


def test_usd_micros_to_wei_zero_and_negative_are_zero(sb):
    assert usd_micros_to_wei(0, _price(3000)) == 0
    assert usd_micros_to_wei(-5, _price(3000)) == 0


def test_wei_to_usd_micros_round_trips(sb):
    assert wei_to_usd_micros(10**18, _price(3000)) == 3000 * 10**6


def test_validate_price_accepts_a_fresh_positive_answer(sb):
    validate_price(_price(3000, updated_at=1000), max_age_seconds=3600, now=1000 + 60)


def test_validate_price_rejects_a_stale_answer(sb):
    with pytest.raises(StalePriceError, match="old"):
        validate_price(_price(3000, updated_at=1000), max_age_seconds=3600, now=1000 + 3601)


@pytest.mark.parametrize("answer", [0, -1])
def test_validate_price_rejects_non_positive_answers(sb, answer):
    price = EthUsdPrice(answer=answer, decimals=8, updated_at=1000)
    with pytest.raises(StalePriceError, match="non-positive"):
        validate_price(price, max_age_seconds=3600, now=1000)


def test_usd_per_eth_decimal(sb):
    assert str(_price(3000).usd_per_eth) == "3000"


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------


def _config(mock_config, **overrides):
    mock_config.PROVIDER_PAYOUT_ASSET = "ETH"
    mock_config.PROVIDER_PAYOUT_POOL_PRIVATE_KEY = "0x" + "1" * 64
    mock_config.BASE_ETH_USD_FEED_ADDRESS = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"
    mock_config.BASE_RPC_URL = "http://fake-rpc"
    mock_config.BASE_CHAIN_ID = 8453
    mock_config.BASE_SEQUENCER_UPTIME_FEED_ADDRESS = "0xBCF85224fc0756B9Fa45aA7892530B47e10b6433"
    for k, v in overrides.items():
        setattr(mock_config, k, v)


def test_from_config_raises_when_pool_key_unset(sb):
    with patch("src.services.chain.eth_payout_client.Config") as mock_config:
        _config(mock_config, PROVIDER_PAYOUT_POOL_PRIVATE_KEY=None)
        with pytest.raises(EthPayoutClientError, match="PROVIDER_PAYOUT_POOL_PRIVATE_KEY"):
            EthPayoutClient.from_config()


def test_from_config_raises_for_unsupported_asset(sb):
    with patch("src.services.chain.eth_payout_client.Config") as mock_config:
        _config(mock_config, PROVIDER_PAYOUT_ASSET="WAYZ")
        with pytest.raises(EthPayoutClientError, match="not supported"):
            EthPayoutClient.from_config()


def test_from_config_raises_when_feed_unset(sb):
    with patch("src.services.chain.eth_payout_client.Config") as mock_config:
        _config(mock_config, BASE_ETH_USD_FEED_ADDRESS=None)
        with pytest.raises(EthPayoutClientError, match="FEED"):
            EthPayoutClient.from_config()


# ---------------------------------------------------------------------------
# Web3 interactions (mocked RPC, REAL eth_account signing)
# ---------------------------------------------------------------------------


def _make_client(private_key: str):
    with patch("src.services.chain.eth_payout_client.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.to_checksum_address.side_effect = lambda a: a
        mock_feed = MagicMock()
        mock_w3.eth.contract.return_value = mock_feed
        client = EthPayoutClient("http://fake-rpc", private_key, "0xfeed", 8453)
        return client, mock_w3, mock_feed


def test_eth_usd_price_reads_the_chainlink_feed(sb):
    client, _, feed = _make_client(Account.create().key.hex())
    feed.functions.decimals.return_value.call.return_value = 8
    feed.functions.latestRoundData.return_value.call.return_value = (
        1,
        275_712_345_678,
        1_700_000_000,
        1_700_000_100,
        1,
    )
    price = client.eth_usd_price()
    assert price == EthUsdPrice(answer=275_712_345_678, decimals=8, updated_at=1_700_000_100)


def test_pool_balance_is_the_native_eth_balance(sb):
    account = Account.create()
    client, w3, _ = _make_client(account.key.hex())
    w3.eth.get_balance.return_value = 5 * 10**17
    assert client.pool_balance_wei() == 5 * 10**17
    w3.eth.get_balance.assert_called_once_with(account.address)


def _prime_for_transfer(w3, estimate=21_000):
    w3.eth.get_transaction_count.return_value = 7
    w3.eth.estimate_gas.return_value = estimate
    w3.eth.get_block.return_value = {"baseFeePerGas": 1_000_000}
    w3.eth.max_priority_fee = 100_000


def _decode(raw):
    from eth_account.typed_transactions import TypedTransaction

    return TypedTransaction.from_bytes(raw).as_dict()


@pytest.mark.asyncio
async def test_transfer_sends_a_signed_eip1559_native_transfer(sb):
    # Throwaway key -- proves the build/sign/send chain against REAL
    # eth_account signing (a mock signer would accept anything).
    account = Account.create()
    client, w3, _ = _make_client(account.key.hex())
    _prime_for_transfer(w3)
    recorded = []

    signed = await client.transfer(
        "0x000000000000000000000000000000000000dEaD", 10**16, lambda st: recorded.append(st) or True
    )

    raw = w3.eth.send_raw_transaction.call_args.args[0]
    assert Account.recover_transaction(raw) == account.address
    tx = _decode(raw)
    assert tx["value"] == 10**16
    assert tx["chainId"] == 8453
    assert tx["nonce"] == 7
    assert tx["maxPriorityFeePerGas"] == 100_000
    assert tx["maxFeePerGas"] == 2 * 1_000_000 + 100_000
    assert tx["data"] in (b"", HexBytes(b""))
    # The returned/recorded hash IS the hash of the bytes broadcast.
    from eth_utils import keccak

    assert signed.tx_hash == "0x" + keccak(raw).hex()
    assert recorded == [signed]


@pytest.mark.asyncio
async def test_transfer_gas_is_estimated_with_headroom_for_smart_wallets(sb):
    """PR #2364 review #1: a smart-contract payout wallet (e.g. Coinbase
    Smart Wallet) needs more than 21000 gas; the limit must come from
    eth_estimateGas + headroom, never a hard-coded 21000."""
    client, w3, _ = _make_client(Account.create().key.hex())
    _prime_for_transfer(w3, estimate=40_000)

    signed = await client.transfer(
        "0x000000000000000000000000000000000000dEaD", 10**16, lambda st: True
    )

    tx = _decode(w3.eth.send_raw_transaction.call_args.args[0])
    assert tx["gas"] == 50_000  # 40000 * 1.25
    assert signed.gas == 50_000
    estimate_args = w3.eth.estimate_gas.call_args.args[0]
    assert estimate_args["value"] == 10**16


def test_gas_limit_never_below_a_plain_transfer(sb):
    assert gas_limit_with_headroom(1) == 21_000
    assert gas_limit_with_headroom(21_000) == 26_250
    assert gas_limit_with_headroom(40_001) == 50_002  # ceil


@pytest.mark.asyncio
async def test_transfer_records_the_hash_before_broadcasting(sb):
    """PR #2364 review #2: the hash must be durably recorded BEFORE the
    node ever sees the tx."""
    client, w3, _ = _make_client(Account.create().key.hex())
    _prime_for_transfer(w3)
    order = []
    w3.eth.send_raw_transaction.side_effect = lambda raw: order.append("send")

    await client.transfer(
        "0x000000000000000000000000000000000000dEaD", 1, lambda st: order.append("record") or True
    )

    assert order == ["record", "send"]


@pytest.mark.asyncio
async def test_transfer_does_not_broadcast_when_recording_fails(sb):
    client, w3, _ = _make_client(Account.create().key.hex())
    _prime_for_transfer(w3)

    with pytest.raises(TransferNotSentError):
        await client.transfer("0x000000000000000000000000000000000000dEaD", 1, lambda st: False)
    w3.eth.send_raw_transaction.assert_not_called()

    def _boom(st):
        raise RuntimeError("db down")

    with pytest.raises(TransferNotSentError):
        await client.transfer("0x000000000000000000000000000000000000dEaD", 1, _boom)
    w3.eth.send_raw_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_transfer_gas_estimation_failure_is_not_sent(sb):
    client, w3, _ = _make_client(Account.create().key.hex())
    _prime_for_transfer(w3)
    w3.eth.estimate_gas.side_effect = RuntimeError("execution reverted")

    with pytest.raises(TransferNotSentError):
        await client.transfer("0x000000000000000000000000000000000000dEaD", 1, lambda st: True)
    w3.eth.send_raw_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_send_failure_raises_broadcast_error_carrying_the_recorded_hash(sb):
    """PR #2364 review #2: send_raw_transaction raising (e.g. read timeout
    after the node accepted it) must surface as 'outcome unknown' with the
    same hash that was recorded -- never as 'not sent'."""
    client, w3, _ = _make_client(Account.create().key.hex())
    _prime_for_transfer(w3)
    w3.eth.send_raw_transaction.side_effect = TimeoutError("read timeout")
    recorded = []

    with pytest.raises(TransferBroadcastError) as exc_info:
        await client.transfer(
            "0x000000000000000000000000000000000000dEaD", 1, lambda st: recorded.append(st) or True
        )

    assert exc_info.value.tx_hash == recorded[0].tx_hash
    assert exc_info.value.nonce == 7


def test_confirmed_nonce_uses_the_latest_mined_count(sb):
    account = Account.create()
    client, w3, _ = _make_client(account.key.hex())
    w3.eth.get_transaction_count.return_value = 42
    assert client.confirmed_nonce() == 42
    w3.eth.get_transaction_count.assert_called_once_with(account.address, "latest")


def test_wait_for_receipt_returns_none_on_timeout(sb):
    from web3.exceptions import TimeExhausted

    client, w3, _ = _make_client(Account.create().key.hex())
    w3.eth.wait_for_transaction_receipt.side_effect = TimeExhausted("slow")
    assert client.wait_for_receipt("0xabc", 1) is None


# ---------------------------------------------------------------------------
# L2 sequencer-uptime gate (PR #2364 review #4)
# ---------------------------------------------------------------------------


def test_validate_sequencer_accepts_up_and_past_grace(sb):
    validate_sequencer(SequencerStatus(answer=0, started_at=1000), 3600, now=1000 + 3601)


def test_validate_sequencer_rejects_down(sb):
    with pytest.raises(StalePriceError, match="down"):
        validate_sequencer(SequencerStatus(answer=1, started_at=0), 3600, now=10**9)


def test_validate_sequencer_rejects_inside_grace_period(sb):
    with pytest.raises(StalePriceError, match="grace"):
        validate_sequencer(SequencerStatus(answer=0, started_at=1000), 3600, now=1000 + 60)


def _reader_with_feeds(price_round, seq_round):
    with patch("src.services.chain.eth_payout_client.Web3") as mock_web3_cls:
        mock_w3 = MagicMock()
        mock_web3_cls.return_value = mock_w3
        mock_web3_cls.to_checksum_address.side_effect = lambda a: a
        price_feed, seq_feed = MagicMock(), MagicMock()
        mock_w3.eth.contract.side_effect = [price_feed, seq_feed]
        reader = EthPriceReader("http://fake-rpc", "0xfeed", "0xseq")
    price_feed.functions.decimals.return_value.call.return_value = 8
    price_feed.functions.latestRoundData.return_value.call.return_value = price_round
    seq_feed.functions.latestRoundData.return_value.call.return_value = seq_round
    return reader


def test_trusted_price_refuses_while_the_sequencer_is_down(sb):
    now = 1_800_000_000
    reader = _reader_with_feeds((1, 3000 * 10**8, now, now, 1), (1, 1, now - 10, now - 10, 1))
    with patch("src.services.chain.eth_payout_client.Config") as mock_config:
        mock_config.BASE_SEQUENCER_GRACE_PERIOD_SECONDS = 3600
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 1800
        with pytest.raises(StalePriceError, match="sequencer"):
            reader.trusted_eth_usd_price(now=now)


def test_trusted_price_returns_a_fresh_price_when_sequencer_is_healthy(sb):
    now = 1_800_000_000
    reader = _reader_with_feeds(
        (1, 3000 * 10**8, now - 100, now - 100, 1), (1, 0, now - 86400, now - 86400, 1)
    )
    with patch("src.services.chain.eth_payout_client.Config") as mock_config:
        mock_config.BASE_SEQUENCER_GRACE_PERIOD_SECONDS = 3600
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 1800
        price = reader.trusted_eth_usd_price(now=now)
    assert price.usd_per_eth == 3000


def test_trusted_price_rejects_answers_older_than_the_default_max_age(sb):
    """Default max age is ~1.5x Base's 1200s heartbeat."""
    from src.config.config import Config

    assert Config.ETH_USD_PRICE_MAX_AGE_SECONDS == 1800
    now = 1_800_000_000
    reader = _reader_with_feeds(
        (1, 3000 * 10**8, now - 1801, now - 1801, 1), (1, 0, now - 86400, now - 86400, 1)
    )
    with patch("src.services.chain.eth_payout_client.Config") as mock_config:
        mock_config.BASE_SEQUENCER_GRACE_PERIOD_SECONDS = 3600
        mock_config.ETH_USD_PRICE_MAX_AGE_SECONDS = 1800
        with pytest.raises(StalePriceError, match="old"):
            reader.trusted_eth_usd_price(now=now)


def test_get_receipt_returns_none_when_not_found(sb):
    from web3.exceptions import TransactionNotFound

    client, w3, _ = _make_client(Account.create().key.hex())
    w3.eth.get_transaction_receipt.side_effect = TransactionNotFound("nope")
    assert client.get_receipt("0xabc") is None
