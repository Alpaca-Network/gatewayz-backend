"""Credit ledger — double-entry billing core (Gatewayz One, Phase 3).

STAGED / NOT WIRED. Pure, ``Decimal``-based logic for the optimistic
reserve → settle → reconcile flow over ``subscription_allowance`` +
``purchased_credits`` (spec §6.D). This module performs **no I/O** and is **not
called by the request path** — it exists so the money math can be reviewed and
unit-tested *before* any cutover to live billing. Wiring it in (and the cutover)
is a deliberate later step.

Invariants:
  * Append-only entries — :class:`LedgerEntry` is frozen; operations return new
    entries, never mutate.
  * Every transaction is balanced — Σdebit == Σcredit (see :func:`is_balanced`).
  * Spend order — ``subscription_allowance`` is consumed before
    ``purchased_credits``; on a refund the release order is reversed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

# Account names (string keys; the real ledger maps these to columns/rows).
ALLOWANCE = "user:subscription_allowance"
PURCHASED = "user:purchased_credits"
REVENUE = "revenue"

_ZERO = Decimal("0")


class EntryState(str, Enum):  # noqa: UP042
    RESERVED = "reserved"  # optimistic hold, not yet final
    SETTLED = "settled"  # finalized
    RELEASED = "released"  # hold returned (refund of an over-estimate)


class InsufficientBalance(Exception):
    """Raised when balances cannot cover a charge."""


def _d(value) -> Decimal:
    """Coerce to Decimal via str (avoids binary float artifacts)."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class LedgerEntry:
    """A single append-only double-entry line (one of debit/credit is non-zero)."""

    ref: str  # idempotency key (e.g. request id)
    account: str
    debit: Decimal
    credit: Decimal
    state: EntryState


def is_balanced(entries: list[LedgerEntry]) -> bool:
    """True if total debits equal total credits across ``entries``."""
    return sum((e.debit for e in entries), _ZERO) == sum((e.credit for e in entries), _ZERO)


@dataclass(frozen=True)
class ChargeSplit:
    """How a charge is apportioned across the two balances (allowance first)."""

    from_allowance: Decimal
    from_purchased: Decimal

    @property
    def total(self) -> Decimal:
        return self.from_allowance + self.from_purchased


def split_charge(amount, allowance, purchased) -> ChargeSplit:
    """Apportion ``amount`` over the balances, spending allowance before purchased.

    Raises:
        ValueError: if ``amount`` is negative.
        InsufficientBalance: if ``amount`` exceeds ``allowance + purchased``.
    """
    amount, allowance, purchased = _d(amount), _d(allowance), _d(purchased)
    if amount < 0:
        raise ValueError("amount must be non-negative")
    from_allowance = min(amount, allowance)
    from_purchased = amount - from_allowance
    if from_purchased > purchased:
        raise InsufficientBalance(f"charge {amount} exceeds available {allowance + purchased}")
    return ChargeSplit(from_allowance, from_purchased)


def reserve(ref: str, estimated_cost, allowance, purchased) -> list[LedgerEntry]:
    """Optimistically hold ``estimated_cost`` against the balances.

    Returns balanced RESERVED entries (debits on the user accounts, a matching
    credit to revenue). Raises InsufficientBalance if the estimate can't be held.
    """
    split = split_charge(estimated_cost, allowance, purchased)
    entries: list[LedgerEntry] = []
    if split.from_allowance > 0:
        entries.append(
            LedgerEntry(ref, ALLOWANCE, split.from_allowance, _ZERO, EntryState.RESERVED)
        )
    if split.from_purchased > 0:
        entries.append(
            LedgerEntry(ref, PURCHASED, split.from_purchased, _ZERO, EntryState.RESERVED)
        )
    if split.total > 0:
        entries.append(LedgerEntry(ref, REVENUE, _ZERO, split.total, EntryState.RESERVED))
    return entries


def reserved_amount(entries: list[LedgerEntry]) -> Decimal:
    """Total currently held against user accounts in RESERVED state."""
    return sum(
        (
            e.debit
            for e in entries
            if e.state == EntryState.RESERVED and e.account in (ALLOWANCE, PURCHASED)
        ),
        _ZERO,
    )


@dataclass(frozen=True)
class Reconciliation:
    """Outcome of reconciling a reservation against the actual cost."""

    settled: Decimal  # amount finalized (min(actual, reserved))
    released: Decimal  # amount returned to the user (over-estimate refund)
    extra: Decimal  # additional charge needed (under-estimate)
    drift: Decimal  # actual - reserved (negative = refund, positive = extra)


def reconcile(reserved, actual) -> Reconciliation:
    """Compute the settlement deltas between a reserved hold and the actual cost.

    Does not require balances — it only computes the numbers the settlement
    step applies. ``extra`` (an under-estimate) must then be charged from the
    user's remaining balance by the caller, allowance-first via :func:`split_charge`.
    """
    reserved, actual = _d(reserved), _d(actual)
    if reserved < 0 or actual < 0:
        raise ValueError("reserved and actual must be non-negative")
    drift = actual - reserved
    return Reconciliation(
        settled=min(actual, reserved),
        released=max(_ZERO, reserved - actual),
        extra=max(_ZERO, actual - reserved),
        drift=drift,
    )
