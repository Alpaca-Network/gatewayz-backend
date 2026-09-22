"""Response shaping for provider payout amounts (USD earnings paid in ETH on
Base since 2026-09-22), shared by GET /gpu/providers/me,
GET /gpu/providers/me/earnings and GET /admin/wayz/status."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

_USD_MICROS = Decimal(1_000_000)
_BASESCAN_TX_URL = "https://basescan.org/tx/{tx_hash}"
_SNOWTRACE_TX_URL = "https://testnet.snowtrace.io/tx/{tx_hash}"


def micros_to_usd(amount: int | str | None) -> str | None:
    """Integer USD micros -> decimal USD string (e.g. 1234567 -> '1.234567')."""
    if amount is None:
        return None
    return str(Decimal(int(amount)) / _USD_MICROS)


def usd_totals_view(
    totals: dict, statuses: tuple[str, ...] = ("accrued", "settled", "void")
) -> dict[str, Any]:
    """Flatten earnings_totals()/earnings_totals_all() into the response
    shape: `<status>_usd` + `<status>_usd_micros`, plus the legacy WAYZ
    `<status>_wei` keys (still returned so existing clients don't break)."""
    legacy = totals.get("wayz_wei") or {}
    view: dict[str, Any] = {"payout_asset": "ETH", "payout_chain": "base"}
    for status in statuses:
        micros = int(totals.get(status, 0) or 0)
        view[f"{status}_usd"] = micros_to_usd(micros)
        view[f"{status}_usd_micros"] = micros
        view[f"{status}_wei"] = str(int(legacy.get(status, 0) or 0))
    return view


def tx_url(asset: str | None, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    template = _SNOWTRACE_TX_URL if (asset or "ETH").upper() == "WAYZ" else _BASESCAN_TX_URL
    return template.format(tx_hash=tx_hash)
