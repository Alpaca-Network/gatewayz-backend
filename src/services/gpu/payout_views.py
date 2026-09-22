"""Response shaping for provider payout amounts (USD earnings paid in ETH on
Base since 2026-09-22), shared by GET /gpu/providers/me,
GET /gpu/providers/me/earnings, GET /admin/wayz/status and the emission view.

**Back-compat (PR #2364 review):** existing clients read `<status>_wei`,
`settlements[].amount_wei` and `emission.allocation_wayz`. These stay
populated with meaningful ETH values rather than zeros:

- `settled_wei` -- ETH actually paid on Base (sum of CONFIRMED settlements).
- `accrued_wei` / `settling_wei` / `void_wei` -- the USD balance converted
  at the current trusted Chainlink ETH/USD price (the same gate settlement
  uses), i.e. "what this would pay in ETH right now". `null` only while no
  trusted price is available.
- `allocation_wayz` -- deprecated alias: the allocation's ETH equivalent.

USD fields (`<status>_usd`, `_usd_micros`) are the source of truth; legacy
pre-switch WAYZ history is under `legacy_wayz_<status>_wei`.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

from src.services.chain.eth_payout_client import EthUsdPrice, usd_micros_to_wei

logger = logging.getLogger(__name__)

_USD_MICROS = Decimal(1_000_000)
_WEI_PER_ETH = Decimal(10) ** 18
_BASESCAN_TX_URL = "https://basescan.org/tx/{tx_hash}"
_SNOWTRACE_TX_URL = "https://testnet.snowtrace.io/tx/{tx_hash}"

# Display-only price cache: endpoints must not hit the RPC per request.
_PRICE_TTL_SECONDS = 300
_PRICE_FAILURE_TTL_SECONDS = 60
_price_cache: tuple[float, EthUsdPrice | None] | None = None


def _fetch_price() -> EthUsdPrice:
    from src.services.chain.eth_payout_client import EthPriceReader

    return EthPriceReader.from_config().trusted_eth_usd_price()


def get_display_eth_usd_price() -> EthUsdPrice | None:
    """Trusted (sequencer-checked, fresh) ETH/USD price for display, cached.
    None when it can't be read or isn't trustworthy right now."""
    global _price_cache
    now = time.monotonic()
    if _price_cache is not None and now < _price_cache[0]:
        return _price_cache[1]
    try:
        price = _fetch_price()
        _price_cache = (now + _PRICE_TTL_SECONDS, price)
    except Exception as e:
        logger.info(f"display ETH/USD price unavailable: {e}")
        price = None
        _price_cache = (now + _PRICE_FAILURE_TTL_SECONDS, None)
    return price


def micros_to_usd(amount: int | str | None) -> str | None:
    """Integer USD micros -> decimal USD string (e.g. 1234567 -> '1.234567')."""
    if amount is None:
        return None
    return str(Decimal(int(amount)) / _USD_MICROS)


def wei_to_eth(amount_wei: int | str | None) -> str | None:
    if amount_wei is None:
        return None
    return str(Decimal(int(amount_wei)) / _WEI_PER_ETH)


def usd_totals_view(
    totals: dict,
    statuses: tuple[str, ...] = ("accrued", "settled", "void"),
    *,
    price: EthUsdPrice | None,
    paid_wei: int,
) -> dict[str, Any]:
    """Flatten earnings_totals()/earnings_totals_all() into the response
    shape -- see the module docstring for what each `_wei` key means."""
    legacy = totals.get("wayz_wei") or {}
    view: dict[str, Any] = {
        "payout_asset": "ETH",
        "payout_chain": "base",
        "eth_usd_price": str(price.usd_per_eth) if price is not None else None,
    }
    for status in statuses:
        micros = int(totals.get(status, 0) or 0)
        view[f"{status}_usd"] = micros_to_usd(micros)
        view[f"{status}_usd_micros"] = micros
        if status == "settled":
            wei: int | None = paid_wei
        elif price is not None:
            wei = usd_micros_to_wei(micros, price)
        else:
            wei = None
        view[f"{status}_wei"] = str(wei) if wei is not None else None
        view[f"{status}_eth"] = wei_to_eth(wei)
        view[f"legacy_wayz_{status}_wei"] = str(int(legacy.get(status, 0) or 0))
    return view


def allocation_eth_equivalent(allocation_usd_micros: int, price: EthUsdPrice | None) -> str | None:
    if price is None:
        return None
    return wei_to_eth(usd_micros_to_wei(allocation_usd_micros, price))


def tx_url(asset: str | None, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    template = _SNOWTRACE_TX_URL if (asset or "ETH").upper() == "WAYZ" else _BASESCAN_TX_URL
    return template.format(tx_hash=tx_hash)
