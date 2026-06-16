"""Unit tests for the Phase 3 credit-ledger core (pure Decimal math, not wired)."""

from decimal import Decimal

import pytest

from src.services.credit_ledger import (
    ALLOWANCE,
    PURCHASED,
    REVENUE,
    ChargeSplit,
    EntryState,
    InsufficientBalance,
    LedgerEntry,
    Reconciliation,
    is_balanced,
    reconcile,
    reserve,
    reserved_amount,
    split_charge,
)

D = Decimal


# --------------------------------------------------------------------------- #
# split_charge — allowance spent before purchased
# --------------------------------------------------------------------------- #
def test_split_uses_allowance_first():
    s = split_charge(3, allowance=10, purchased=10)
    assert s.from_allowance == D("3")
    assert s.from_purchased == D("0")
    assert s.total == D("3")


def test_split_spills_to_purchased_when_allowance_short():
    s = split_charge(8, allowance=5, purchased=10)
    assert s.from_allowance == D("5")
    assert s.from_purchased == D("3")
    assert s.total == D("8")


def test_split_exact_allowance_boundary():
    s = split_charge(5, allowance=5, purchased=10)
    assert (s.from_allowance, s.from_purchased) == (D("5"), D("0"))


def test_split_zero_charge():
    s = split_charge(0, allowance=5, purchased=5)
    assert s.total == D("0")


def test_split_negative_amount_raises():
    with pytest.raises(ValueError):
        split_charge(-1, allowance=5, purchased=5)


def test_split_insufficient_balance_raises():
    with pytest.raises(InsufficientBalance):
        split_charge(20, allowance=5, purchased=10)


def test_split_uses_decimal_not_float():
    s = split_charge(0.1, allowance=0.3, purchased=0)
    # 0.1 via str() -> exact Decimal('0.1'), no binary float drift
    assert s.from_allowance == D("0.1")
    assert isinstance(s.from_allowance, Decimal)


# --------------------------------------------------------------------------- #
# reserve — balanced double-entry, correct accounts/state
# --------------------------------------------------------------------------- #
def test_reserve_is_balanced():
    entries = reserve("req1", estimated_cost=8, allowance=5, purchased=10)
    assert is_balanced(entries)


def test_reserve_debits_user_accounts_credits_revenue():
    entries = reserve("req1", estimated_cost=8, allowance=5, purchased=10)
    by_account = {e.account: e for e in entries}
    assert by_account[ALLOWANCE].debit == D("5")
    assert by_account[PURCHASED].debit == D("3")
    assert by_account[REVENUE].credit == D("8")
    assert all(e.state == EntryState.RESERVED for e in entries)


def test_reserve_only_allowance_when_sufficient():
    entries = reserve("req1", estimated_cost=4, allowance=10, purchased=10)
    accounts = {e.account for e in entries}
    assert accounts == {ALLOWANCE, REVENUE}  # no purchased entry
    assert is_balanced(entries)


def test_reserve_zero_cost_produces_no_entries():
    assert reserve("req1", estimated_cost=0, allowance=5, purchased=5) == []


def test_reserve_insufficient_raises():
    with pytest.raises(InsufficientBalance):
        reserve("req1", estimated_cost=50, allowance=5, purchased=10)


def test_reserved_amount_counts_user_holds_only():
    entries = reserve("req1", estimated_cost=8, allowance=5, purchased=10)
    assert reserved_amount(entries) == D("8")  # excludes the revenue credit


# --------------------------------------------------------------------------- #
# reconcile — estimate vs actual
# --------------------------------------------------------------------------- #
def test_reconcile_exact():
    r = reconcile(reserved=10, actual=10)
    assert r == Reconciliation(settled=D("10"), released=D("0"), extra=D("0"), drift=D("0"))


def test_reconcile_overestimate_releases_difference():
    # held 10, actual 6 -> settle 6, release 4, no extra
    r = reconcile(reserved=10, actual=6)
    assert r.settled == D("6")
    assert r.released == D("4")
    assert r.extra == D("0")
    assert r.drift == D("-4")


def test_reconcile_underestimate_charges_extra():
    # held 10, actual 13 -> settle 10, extra 3, no release
    r = reconcile(reserved=10, actual=13)
    assert r.settled == D("10")
    assert r.released == D("0")
    assert r.extra == D("3")
    assert r.drift == D("3")


def test_reconcile_zero_actual_full_release():
    r = reconcile(reserved=5, actual=0)
    assert r.settled == D("0")
    assert r.released == D("5")
    assert r.drift == D("-5")


def test_reconcile_negative_raises():
    with pytest.raises(ValueError):
        reconcile(reserved=-1, actual=1)
    with pytest.raises(ValueError):
        reconcile(reserved=1, actual=-1)


def test_reconcile_settled_plus_released_equals_reserved_on_overestimate():
    r = reconcile(reserved=10, actual=4)
    assert r.settled + r.released == D("10")  # nothing lost


def test_reconcile_settled_plus_extra_equals_actual_on_underestimate():
    r = reconcile(reserved=10, actual=15)
    assert r.settled + r.extra == D("15")  # full actual accounted for


# --------------------------------------------------------------------------- #
# is_balanced
# --------------------------------------------------------------------------- #
def test_is_balanced_true_and_false():
    balanced = [
        LedgerEntry("r", ALLOWANCE, D("5"), D("0"), EntryState.SETTLED),
        LedgerEntry("r", REVENUE, D("0"), D("5"), EntryState.SETTLED),
    ]
    assert is_balanced(balanced)
    unbalanced = [LedgerEntry("r", ALLOWANCE, D("5"), D("0"), EntryState.SETTLED)]
    assert not is_balanced(unbalanced)


def test_ledger_entry_is_immutable():
    e = LedgerEntry("r", ALLOWANCE, D("5"), D("0"), EntryState.RESERVED)
    with pytest.raises(Exception):
        e.debit = D("9")  # frozen dataclass


def test_charge_split_is_immutable():
    s = ChargeSplit(D("1"), D("2"))
    with pytest.raises(Exception):
        s.from_allowance = D("9")
