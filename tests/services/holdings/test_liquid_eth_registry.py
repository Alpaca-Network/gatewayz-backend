"""Static guard on the liquid ETH registry migration
(supabase/migrations/20261008120000_holdings_tokens_liquid_eth.sql).

The registry is the trust root -- whatever it lists gets valued and paid --
so a typo in an address or a price id here is a payout bug, not a cosmetic
one. Nothing here touches a network or a database."""

import re
from pathlib import Path

from src.services.holdings.chains import SUPPORTED_CHAIN_IDS

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase/migrations/20261008120000_holdings_tokens_liquid_eth.sql"
)

ROW_RE = re.compile(r"\((\d+),\s*'(0x[0-9a-fA-F]+)',\s*'(\w+)',\s*(\d+),\s*'([a-z0-9-]+)'\)")

# Each symbol's own CoinGecko id, verified against /simple/price on 2026-10-08.
# Never ETH's: wstETH/rETH/cbETH trade at a premium to ETH.
EXPECTED_PRICE_IDS = {
    "stETH": "staked-ether",
    "wstETH": "wrapped-steth",
    "rETH": "rocket-pool-eth",
    "cbETH": "coinbase-wrapped-staked-eth",
}

EXPECTED_ROWS = {
    (1, "stETH"),
    (1, "wstETH"),
    (1, "rETH"),
    (1, "cbETH"),
    (8453, "wstETH"),
    (8453, "rETH"),
    (8453, "cbETH"),
    (42161, "wstETH"),
    (42161, "rETH"),
    (42161, "cbETH"),
}


def _rows():
    return [
        (int(chain), addr, symbol, int(decimals), price_id)
        for chain, addr, symbol, decimals, price_id in ROW_RE.findall(MIGRATION.read_text())
    ]


def test_exactly_the_expected_tokens():
    rows = _rows()
    assert {(chain, symbol) for chain, _a, symbol, _d, _p in rows} == EXPECTED_ROWS
    assert len(rows) == len(EXPECTED_ROWS)


def test_rows_are_well_formed():
    for chain, addr, symbol, decimals, price_id in _rows():
        assert chain in SUPPORTED_CHAIN_IDS
        assert re.fullmatch(r"0x[0-9a-f]{40}", addr), f"{symbol}@{chain}: lowercase 40-hex"
        assert decimals == 18
        assert price_id == EXPECTED_PRICE_IDS[symbol]
        assert price_id != "ethereum"


def test_addresses_are_unique_per_chain():
    keys = [(chain, addr) for chain, addr, *_ in _rows()]
    assert len(keys) == len(set(keys))


def test_reapply_is_a_noop():
    sql = MIGRATION.read_text().lower()
    assert "on conflict do nothing" in sql
    assert "where not exists" in sql
    assert "lower(t.contract_address)" in sql  # an ops row in checksum case still matches
    assert "update " not in sql and "delete " not in sql
