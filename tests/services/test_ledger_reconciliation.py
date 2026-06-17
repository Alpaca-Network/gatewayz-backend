"""Unit tests for the pure credit-ledger reconciliation core (Phase 3, item 2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.services.billing.ledger_reconciliation import (
    ALLOWANCE,
    PURCHASED,
    REVENUE,
    UserDrift,
    reconcile,
    reconcile_rows,
    summarize_ledger,
    summarize_usage,
)


def _settled(ref, user_id, allowance, purchased):
    """Build a balanced settled triple (allowance+purchased debit, revenue credit)."""
    total = Decimal(str(allowance)) + Decimal(str(purchased))
    rows = []
    if Decimal(str(allowance)) > 0:
        rows.append({"ref": ref, "user_id": user_id, "account": ALLOWANCE,
                     "debit": str(allowance), "credit": "0", "state": "settled"})
    if Decimal(str(purchased)) > 0:
        rows.append({"ref": ref, "user_id": user_id, "account": PURCHASED,
                     "debit": str(purchased), "credit": "0", "state": "settled"})
    rows.append({"ref": ref, "user_id": user_id, "account": REVENUE,
                 "debit": "0", "credit": str(total), "state": "settled"})
    return rows


# --------------------------------------------------------------------------- #
# summarize_ledger
# --------------------------------------------------------------------------- #

def test_summarize_ledger_empty_is_clean():
    s = summarize_ledger([])
    assert s.total_revenue == Decimal("0")
    assert s.ref_count == 0
    assert s.entry_count == 0
    assert s.unbalanced_refs == []
    assert s.revenue_by_user == {}


def test_summarize_ledger_aggregates_revenue_per_user():
    rows = _settled("r1", 1, "0.02", "0.01") + _settled("r2", 1, "0.05", "0") \
        + _settled("r3", 2, "0", "0.10")
    s = summarize_ledger(rows)
    assert s.ref_count == 3
    assert s.revenue_by_user[1] == Decimal("0.08")  # 0.03 + 0.05
    assert s.revenue_by_user[2] == Decimal("0.10")
    assert s.total_revenue == Decimal("0.18")
    assert s.unbalanced_refs == []


def test_summarize_ledger_flags_unbalanced_ref():
    rows = _settled("good", 1, "0.02", "0")
    # A corrupt ref: debit 0.05 but revenue credit only 0.04
    rows += [
        {"ref": "bad", "user_id": 1, "account": ALLOWANCE, "debit": "0.05", "credit": "0"},
        {"ref": "bad", "user_id": 1, "account": REVENUE, "debit": "0", "credit": "0.04"},
    ]
    s = summarize_ledger(rows)
    assert s.unbalanced_refs == ["bad"]


def test_summarize_ledger_handles_none_and_string_decimals():
    rows = [
        {"ref": "r", "user_id": 1, "account": REVENUE, "debit": None, "credit": "0.030000"},
        {"ref": "r", "user_id": 1, "account": ALLOWANCE, "debit": "0.030000", "credit": None},
    ]
    s = summarize_ledger(rows)
    assert s.total_revenue == Decimal("0.030000")
    assert s.unbalanced_refs == []


# --------------------------------------------------------------------------- #
# summarize_usage
# --------------------------------------------------------------------------- #

def test_summarize_usage_aggregates_cost_per_user():
    rows = [
        {"user_id": 1, "cost": "0.03"},
        {"user_id": 1, "cost": "0.05"},
        {"user_id": 2, "cost": "0.10"},
    ]
    s = summarize_usage(rows)
    assert s.cost_by_user[1] == Decimal("0.08")
    assert s.cost_by_user[2] == Decimal("0.10")
    assert s.total_cost == Decimal("0.18")
    assert s.row_count == 3
    assert s.excluded_count == 0


def test_summarize_usage_excludes_below_min_cost():
    rows = [
        {"user_id": 1, "cost": "0.05"},
        {"user_id": 1, "cost": "0.0000001"},  # sub-threshold, like shadow skips
    ]
    s = summarize_usage(rows)
    assert s.total_cost == Decimal("0.05")
    assert s.row_count == 1
    assert s.excluded_count == 1


def test_summarize_usage_excludes_admin_user_ids():
    rows = [
        {"user_id": 1, "cost": "0.05"},
        {"user_id": 99, "cost": "0.20"},  # admin
    ]
    s = summarize_usage(rows, exclude_user_ids=frozenset({99}))
    assert 99 not in s.cost_by_user
    assert s.total_cost == Decimal("0.05")
    assert s.excluded_count == 1


# --------------------------------------------------------------------------- #
# reconcile
# --------------------------------------------------------------------------- #

def test_reconcile_perfect_match_is_ok():
    ledger = summarize_ledger(_settled("r1", 1, "0.03", "0") + _settled("r2", 2, "0.10", "0"))
    usage = summarize_usage([{"user_id": 1, "cost": "0.03"}, {"user_id": 2, "cost": "0.10"}])
    report = reconcile(ledger, usage)
    assert report.ok
    assert report.total_drift == Decimal("0")
    assert report.within_tolerance
    assert all(u.within_tolerance for u in report.per_user)


def test_reconcile_detects_drift_outside_tolerance():
    # Ledger recorded 0.10 for user 1 but live usage only billed 0.04 → drift 0.06.
    ledger = summarize_ledger(_settled("r1", 1, "0.10", "0"))
    usage = summarize_usage([{"user_id": 1, "cost": "0.04"}])
    report = reconcile(ledger, usage, tolerance=Decimal("0.01"))
    assert not report.ok
    assert not report.within_tolerance
    assert report.total_drift == Decimal("0.06")
    worst = report.per_user[0]
    assert worst.user_id == 1
    assert worst.drift == Decimal("0.06")
    assert not worst.within_tolerance


def test_reconcile_within_tolerance_passes():
    ledger = summarize_ledger(_settled("r1", 1, "0.100", "0"))
    usage = summarize_usage([{"user_id": 1, "cost": "0.095"}])  # 0.005 drift
    report = reconcile(ledger, usage, tolerance=Decimal("0.01"))
    assert report.within_tolerance
    assert report.ok


def test_reconcile_unbalanced_ref_makes_report_not_ok():
    ledger = summarize_ledger([
        {"ref": "bad", "user_id": 1, "account": ALLOWANCE, "debit": "0.05", "credit": "0"},
        {"ref": "bad", "user_id": 1, "account": REVENUE, "debit": "0", "credit": "0.05"},
        {"ref": "bad", "user_id": 1, "account": PURCHASED, "debit": "0.01", "credit": "0"},
    ])  # debits 0.06 != credit 0.05
    usage = summarize_usage([{"user_id": 1, "cost": "0.05"}])
    report = reconcile(ledger, usage)
    # revenue side matches, but the ref is internally unbalanced → not ok
    assert report.within_tolerance
    assert report.unbalanced_refs == ["bad"]
    assert not report.ok


def test_reconcile_user_in_ledger_but_not_usage_shows_full_drift():
    ledger = summarize_ledger(_settled("r1", 7, "0.09", "0"))
    usage = summarize_usage([])
    report = reconcile(ledger, usage)
    assert report.per_user[0].user_id == 7
    assert report.per_user[0].drift == Decimal("0.09")
    assert not report.ok


def test_reconcile_empty_both_sides_is_ok():
    report = reconcile(summarize_ledger([]), summarize_usage([]))
    assert report.ok
    assert report.total_drift == Decimal("0")
    assert report.per_user == []


def test_reconcile_per_user_sorted_worst_first():
    ledger = summarize_ledger(
        _settled("a", 1, "0.10", "0") + _settled("b", 2, "0.10", "0") + _settled("c", 3, "0.10", "0")
    )
    usage = summarize_usage([
        {"user_id": 1, "cost": "0.10"},   # drift 0
        {"user_id": 2, "cost": "0.02"},   # drift 0.08
        {"user_id": 3, "cost": "0.07"},   # drift 0.03
    ])
    report = reconcile(ledger, usage)
    drifts = [u.drift for u in report.per_user]
    assert drifts == [Decimal("0.08"), Decimal("0.03"), Decimal("0")]


def test_reconcile_rows_one_call_path():
    ledger_rows = _settled("r1", 1, "0.03", "0")
    usage_rows = [{"user_id": 1, "cost": "0.03"}, {"user_id": 99, "cost": "0.50"}]
    report = reconcile_rows(usage_rows=usage_rows, ledger_rows=ledger_rows,
                            exclude_user_ids=frozenset({99}))
    assert report.ok
    assert report.total_drift == Decimal("0")


# --------------------------------------------------------------------------- #
# reconcile_window — I/O runner with a mocked Supabase client
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table_name = table
        self.store = store

    def select(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def lt(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, start, end):
        self._start = start
        return self

    def execute(self):
        rows = self.store.get(self.table_name, [])
        # _fetch_all pages via range(); return all on first page, empty after.
        if getattr(self, "_start", 0) and self.table_name in ("credit_ledger", "usage_records"):
            return _Resp([])
        return _Resp(rows)


class _Client:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def test_reconcile_window_mocked(monkeypatch):
    import sys
    import types

    from src.services.billing import ledger_reconciliation as lr

    store = {
        "credit_ledger": _settled("r1", 1, "0.03", "0") + _settled("r2", 2, "0.10", "0"),
        "usage_records": [
            {"user_id": 1, "cost": "0.03"},
            {"user_id": 2, "cost": "0.10"},
            {"user_id": 99, "cost": "0.50"},  # admin → excluded
        ],
        "users": [{"id": 99}],  # tier == 'admin'
    }
    fake_mod = types.ModuleType("src.config.supabase_config")
    fake_mod.get_supabase_client = lambda: _Client(store)
    monkeypatch.setitem(sys.modules, "src.config.supabase_config", fake_mod)

    report, admin_count = lr.reconcile_window("2026-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00")
    assert admin_count == 1
    assert report.ok
    assert report.total_drift == Decimal("0")
    assert report.ledger_ref_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
