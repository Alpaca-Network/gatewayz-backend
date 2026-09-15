"""The holdings-rewards observation sweep.

Runs `Config.HOLDINGS_SNAPSHOTS_PER_DAY` times a day. Each run reads every
eligible wallet's balance of every enabled registry token and prices it in
USD. A wallet that was fully measured gets:

* exactly one `wallet_holdings_sweeps` row -- the sweep's total USD value,
  **written even when that total is zero**; and
* one `wallet_holdings_snapshots` row per token it actually holds, the
  per-token detail behind that total.

The sweep row is the unit the daily accrual reads, and the zero case is why
it exists. Per-token rows do not exist for an empty wallet, so if the sweep
were inferred from them, a wallet emptied between sweeps would leave no
trace at all rather than being valued at zero -- and the day's minimum would
be taken across only the sweeps in which it happened to be funded. Fund a
wallet just before two of four daily sweeps, empty it the rest of the day,
and it would be paid as though it held that balance all day. Recording the
zero is what makes "paid on the lowest value seen that day" true.

Two rules are about not recording wrong data, not about saving money. Both
suppress the sweep row as well as the detail, because they are cases where
we do not KNOW the total -- and "we could not measure it" must never be
written down as "they held zero":

1. **An incomplete chain read records nothing for that wallet.**
   `BalanceReadResult.is_complete` is false whenever any chain failed, and a
   failed chain contributes zero readings -- so a partial set looks like a
   smaller wallet. Writing it would drag the day's minimum down and silently
   underpay the holder.
2. **A held token with no fresh price records nothing for that wallet.**
   `src/services/holdings/prices.py` fails closed: an id it cannot vouch for
   is simply absent from the returned dict. Valuing the rest of the wallet
   and dropping that token has exactly the same effect as (1).

A token the wallet holds **zero** of is irrelevant to both rules -- it
contributes nothing to the total either way, so a missing price for it must
not block the sweep.

Every row written by one sweep shares a single `taken_at`, which is what
joins the detail to its sweep row. Every skip is logged with its reason and
counted in the run summary, so an operator can see coverage gaps on the ops
page rather than inferring them from missing credits.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.config.config import Config
from src.db.holdings import list_enabled_tokens, record_snapshot, record_sweep
from src.db.user_wallets import list_all_wallets
from src.services.holdings.chains import BalanceReading, TokenRef, read_balances
from src.services.holdings.prices import get_usd_prices

logger = logging.getLogger(__name__)

# usd_value is numeric(38,18); quantize to that scale so a Decimal never
# reaches the DB with more precision than the column can keep.
_USD_DP = Decimal("0.000000000000000001")


def token_refs_from_rows(rows: list[dict[str, Any]]) -> list[TokenRef]:
    """Map `holdings_tokens` rows to the `TokenRef`s the balance reader
    takes. A row missing a required field is dropped with a warning rather
    than aborting the sweep -- a single bad registry row must not stop every
    wallet from being observed."""
    refs: list[TokenRef] = []
    for row in rows:
        try:
            refs.append(
                TokenRef(
                    chain_id=int(row["chain_id"]),
                    contract_address=row["contract_address"],
                    decimals=int(row["decimals"]),
                    symbol=str(row["symbol"]),
                    price_id=str(row["price_id"]),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("holdings_snapshots: dropping malformed token row %s: %s", row, e)
    return refs


def _token_key(chain_id: int, contract_address: str | None) -> tuple[int, str]:
    """The registry's own uniqueness key -- (chain_id, contract or
    'native'), matching idx_holdings_tokens_chain_contract."""
    return chain_id, (contract_address or "native").lower()


def _token_id_map(rows: list[dict[str, Any]]) -> dict[tuple[int, str], int]:
    mapping: dict[tuple[int, str], int] = {}
    for row in rows:
        try:
            mapping[_token_key(int(row["chain_id"]), row.get("contract_address"))] = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
    return mapping


def _wallet_age_ok(created_at: Any, now: datetime, min_age_days: int) -> bool | None:
    """True/False for a wallet old enough / too new, or None when
    `created_at` cannot be parsed. An unparseable age is not treated as old
    enough: we can only pay for holdings in a wallet we can prove has been
    linked long enough."""
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created <= now - timedelta(days=min_age_days)


def _usd_value(reading: BalanceReading, price: Decimal) -> Decimal:
    scale = Decimal(10) ** int(reading.token.decimals)
    return ((Decimal(reading.raw_amount) / scale) * price).quantize(_USD_DP)


def run_holdings_snapshots_once(now: datetime | None = None) -> dict[str, Any]:
    """Run one observation sweep.

    Returns a summary dict for the job-run record. No-ops with
    ``{"skipped": "disabled"}`` when ``HOLDINGS_REWARDS_ENABLED`` is off and
    with ``{"skipped": "no_tokens"}`` when the registry is empty (its normal
    state until ops seeds it). Never raises: a failure on one wallet is
    counted and the sweep continues.
    """
    if not Config.HOLDINGS_REWARDS_ENABLED:
        return {"skipped": "disabled"}

    started = now or datetime.now(UTC)

    token_rows = list_enabled_tokens()
    if not token_rows:
        logger.info("holdings_snapshots: token registry is empty, nothing to observe")
        return {"skipped": "no_tokens"}

    tokens = token_refs_from_rows(token_rows)
    if not tokens:
        return {"skipped": "no_tokens"}
    token_ids = _token_id_map(token_rows)

    # One price fetch for the whole sweep: the same tokens are valued for
    # every wallet, and a per-wallet fetch would hammer the price API and
    # value early wallets at a different moment than later ones.
    prices = get_usd_prices([t.price_id for t in tokens])

    # One taken_at for the whole sweep -- see the module docstring.
    taken_at = started

    min_age_days = Config.HOLDINGS_MIN_WALLET_AGE_DAYS
    skipped = {
        "too_new": 0,
        "unknown_age": 0,
        "incomplete_read": 0,
        "missing_price": 0,
        "sweep_write_failed": 0,
        "error": 0,
    }
    considered = 0
    sweeps_recorded = 0
    wallets_recorded = 0
    wallets_empty = 0
    rows_recorded = 0
    rows_failed = 0

    for wallet_row in list_all_wallets():
        address = str(wallet_row.get("wallet_address") or "")
        if not address:
            continue

        age_ok = _wallet_age_ok(wallet_row.get("created_at"), started, min_age_days)
        if age_ok is None:
            skipped["unknown_age"] += 1
            logger.info(
                "holdings_snapshots: skipping %s -- created_at %r is unusable",
                address,
                wallet_row.get("created_at"),
            )
            continue
        if not age_ok:
            skipped["too_new"] += 1
            continue

        considered += 1

        try:
            result = read_balances(address, tokens)
        except Exception as e:  # noqa: BLE001 - one bad wallet must not sink the sweep
            skipped["error"] += 1
            logger.warning("holdings_snapshots: balance read failed for %s: %s", address, e)
            continue

        if not result.is_complete:
            skipped["incomplete_read"] += 1
            logger.warning(
                "holdings_snapshots: skipping %s -- chains %s could not be read, "
                "a partial total would underpay",
                address,
                result.failed_chain_ids,
            )
            continue

        held = [r for r in result.readings if r.raw_amount > 0]

        unpriced = sorted(
            {
                r.token.price_id
                for r in held
                if r.token.price_id not in prices or prices[r.token.price_id].price <= 0
            }
        )
        if unpriced:
            skipped["missing_price"] += 1
            logger.warning(
                "holdings_snapshots: skipping %s -- no fresh price for held token(s) %s, "
                "a partial total would underpay",
                address,
                unpriced,
            )
            continue

        # Everything below this line is a fully measured sweep: every chain
        # was read and every held token has a fresh price. Its total is
        # therefore authoritative -- including a total of zero.
        valued = [(r, _usd_value(r, prices[r.token.price_id].price)) for r in held]
        sweep_total = sum((value for _, value in valued), Decimal(0))

        # The sweep row goes first and gates the per-token detail. It is the
        # row the payout reads, and an empty wallet has no per-token rows at
        # all -- so without it, a wallet emptied between sweeps would leave
        # no trace and never drag the day's minimum down. Detail rows are
        # skipped when it fails, so detail never exists without the sweep it
        # belongs to.
        if record_sweep(address, taken_at, sweep_total) is None:
            skipped["sweep_write_failed"] += 1
            logger.warning(
                "holdings_snapshots: sweep row write failed for %s, recording no detail either",
                address,
            )
            continue

        sweeps_recorded += 1
        if not held:
            wallets_empty += 1
            continue

        wrote_any = False
        for reading, usd_value in valued:
            key = _token_key(reading.token.chain_id, reading.token.contract_address)
            token_id = token_ids.get(key)
            if token_id is None:
                logger.warning(
                    "holdings_snapshots: no registry id for %s on chain %s",
                    reading.token.symbol,
                    reading.token.chain_id,
                )
                rows_failed += 1
                continue
            created = record_snapshot(
                wallet_address=address,
                token_id=token_id,
                raw_amount=reading.raw_amount,
                usd_value=usd_value,
                taken_at=taken_at,
            )
            if created is None:
                rows_failed += 1
            else:
                rows_recorded += 1
                wrote_any = True

        if wrote_any:
            wallets_recorded += 1

    duration_seconds = (datetime.now(UTC) - started).total_seconds()

    return {
        "taken_at": taken_at.isoformat(),
        "tokens": len(tokens),
        "wallets_considered": considered,
        "sweeps_recorded": sweeps_recorded,
        "wallets_recorded": wallets_recorded,
        "wallets_empty": wallets_empty,
        "rows_recorded": rows_recorded,
        "rows_failed": rows_failed,
        "skipped": skipped,
        "duration": duration_seconds,
    }
