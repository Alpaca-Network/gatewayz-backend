"""Credit-ledger reconciliation — shadow ledger vs. live billing (Gatewayz One, Phase 3).

Item 2 of the shadow→reconcile→cutover chain. Once the shadow dual-write
(:mod:`src.services.billing.credit_ledger_store`) is accruing settled entries in
``public.credit_ledger``, this module measures whether the ledger agrees with the
authoritative billing the gateway actually performed, so the cutover decision is
made on evidence rather than hope.

It is **pure** — it operates over plain lists of rows (the shape Supabase returns)
and returns dataclasses, performing no I/O. The CLI (``scripts/reconcile_credit_ledger.py``)
supplies the rows and renders the report.

Two independent checks:

  1. **Internal integrity** (ledger alone): every ``ref`` must balance
     (Σdebit == Σcredit) — the double-entry invariant. Unbalanced refs mean a
     partial/corrupt write and are reported by ref.
  2. **Ledger vs. live drift** (cross-check): the ledger's REVENUE credit total
     should equal what the live system recorded it charged (``usage_records.cost``),
     per user and overall, within a tolerance.

IMPORTANT — what can and cannot be compared:
  * ``usage_records`` has no request-id column, so the cross-check is an
    **aggregate per-user** comparison, not per-request.
  * The shadow path deliberately skips the exact cases ``deduct_credits`` bypasses
    (admin tier, sub-$0.000001 charges). To compare like-for-like, the live side
    must apply the *same* exclusions: pass ``min_cost`` and the set of admin
    ``user_id``s via ``exclude_user_ids`` (the CLI resolves admins from the users
    table). Drift outside tolerance after those exclusions is a real discrepancy.
  * The REVENUE total is the authoritative figure to compare. The allowance/
    purchased debit *split* can drift under concurrency (see the cutover TODO in
    ``credit_handler``); this module checks the REVENUE side, which stays correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from src.services.credit_ledger import ALLOWANCE, PURCHASED, REVENUE

# Mirror the shadow path's skip threshold (credit_handler / deduct_credits).
DEFAULT_MIN_COST = Decimal("0.000001")
# Default acceptable absolute drift (USD) for both per-user and overall checks.
DEFAULT_TOLERANCE = Decimal("0.01")

_ZERO = Decimal("0")


def _d(value) -> Decimal:
    """Coerce to Decimal via str (None/empty → 0); never raises on bad input."""
    if value is None or value == "":
        return _ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return _ZERO


@dataclass(frozen=True)
class LedgerSummary:
    """Aggregates derived from ``credit_ledger`` rows."""

    total_revenue: Decimal = _ZERO
    revenue_by_user: dict = field(default_factory=dict)
    ref_count: int = 0
    entry_count: int = 0
    unbalanced_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class UsageSummary:
    """Aggregates derived from ``usage_records`` rows (the comparison population)."""

    total_cost: Decimal = _ZERO
    cost_by_user: dict = field(default_factory=dict)
    row_count: int = 0
    excluded_count: int = 0


@dataclass(frozen=True)
class UserDrift:
    """Per-user reconciliation result. ``drift = ledger_revenue - usage_cost``."""

    user_id: object
    ledger_revenue: Decimal
    usage_cost: Decimal
    drift: Decimal
    within_tolerance: bool


@dataclass(frozen=True)
class ReconciliationReport:
    total_ledger_revenue: Decimal
    total_usage_cost: Decimal
    total_drift: Decimal
    within_tolerance: bool
    unbalanced_refs: list
    ledger_ref_count: int
    usage_row_count: int
    tolerance: Decimal
    per_user: list  # list[UserDrift], worst drift first

    @property
    def ok(self) -> bool:
        """True iff the ledger is internally balanced AND agrees with live billing."""
        return self.within_tolerance and not self.unbalanced_refs


def summarize_ledger(rows: list[dict]) -> LedgerSummary:
    """Summarize ``credit_ledger`` rows: REVENUE per user + per-ref balance check.

    Each row has ``ref``, ``user_id``, ``account``, ``debit``, ``credit``. A ref is
    *balanced* when its total debits equal its total credits across all its lines.
    """
    revenue_by_user: dict = {}
    total_revenue = _ZERO
    # ref -> [sum_debit, sum_credit]
    per_ref: dict = {}

    for row in rows:
        ref = row.get("ref")
        account = row.get("account")
        debit = _d(row.get("debit"))
        credit = _d(row.get("credit"))

        sums = per_ref.setdefault(ref, [_ZERO, _ZERO])
        sums[0] += debit
        sums[1] += credit

        if account == REVENUE:
            uid = row.get("user_id")
            revenue_by_user[uid] = revenue_by_user.get(uid, _ZERO) + credit
            total_revenue += credit

    unbalanced = sorted(
        str(ref) for ref, (d, c) in per_ref.items() if d != c
    )
    return LedgerSummary(
        total_revenue=total_revenue,
        revenue_by_user=revenue_by_user,
        ref_count=len(per_ref),
        entry_count=len(rows),
        unbalanced_refs=unbalanced,
    )


def summarize_usage(
    rows: list[dict],
    *,
    min_cost: Decimal = DEFAULT_MIN_COST,
    exclude_user_ids: frozenset = frozenset(),
) -> UsageSummary:
    """Summarize ``usage_records`` rows over the shadow-comparable population.

    Applies the *same* exclusions the shadow path applies, so the cross-check is
    like-for-like: drops rows with ``cost < min_cost`` and rows whose ``user_id``
    is in ``exclude_user_ids`` (admins).
    """
    cost_by_user: dict = {}
    total_cost = _ZERO
    included = 0
    excluded = 0

    for row in rows:
        cost = _d(row.get("cost"))
        uid = row.get("user_id")
        if cost < min_cost or uid in exclude_user_ids:
            excluded += 1
            continue
        cost_by_user[uid] = cost_by_user.get(uid, _ZERO) + cost
        total_cost += cost
        included += 1

    return UsageSummary(
        total_cost=total_cost,
        cost_by_user=cost_by_user,
        row_count=included,
        excluded_count=excluded,
    )


def reconcile(
    ledger: LedgerSummary,
    usage: UsageSummary,
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> ReconciliationReport:
    """Compare ledger REVENUE against live usage cost, per user and overall.

    A user (or the total) is *within tolerance* when ``abs(drift) <= tolerance``.
    The report is ``ok`` only when everything is within tolerance and no ref is
    unbalanced.
    """
    tolerance = _d(tolerance)
    all_users = set(ledger.revenue_by_user) | set(usage.cost_by_user)

    per_user: list = []
    for uid in all_users:
        rev = ledger.revenue_by_user.get(uid, _ZERO)
        cost = usage.cost_by_user.get(uid, _ZERO)
        drift = rev - cost
        per_user.append(
            UserDrift(
                user_id=uid,
                ledger_revenue=rev,
                usage_cost=cost,
                drift=drift,
                within_tolerance=abs(drift) <= tolerance,
            )
        )
    # Worst drift first; tie-break by a stable string form of user_id.
    per_user.sort(key=lambda u: (-abs(u.drift), str(u.user_id)))

    total_drift = ledger.total_revenue - usage.total_cost
    return ReconciliationReport(
        total_ledger_revenue=ledger.total_revenue,
        total_usage_cost=usage.total_cost,
        total_drift=total_drift,
        within_tolerance=abs(total_drift) <= tolerance,
        unbalanced_refs=list(ledger.unbalanced_refs),
        ledger_ref_count=ledger.ref_count,
        usage_row_count=usage.row_count,
        tolerance=tolerance,
        per_user=per_user,
    )


def reconcile_rows(
    ledger_rows: list[dict],
    usage_rows: list[dict],
    *,
    min_cost: Decimal = DEFAULT_MIN_COST,
    exclude_user_ids: frozenset = frozenset(),
    tolerance: Decimal = DEFAULT_TOLERANCE,
) -> ReconciliationReport:
    """Convenience: summarize both sides then reconcile (the CLI's one-call path)."""
    return reconcile(
        summarize_ledger(ledger_rows),
        summarize_usage(usage_rows, min_cost=min_cost, exclude_user_ids=exclude_user_ids),
        tolerance=tolerance,
    )


# --------------------------------------------------------------------------- #
# I/O runner — shared by the CLI (scripts/reconcile_credit_ledger.py) and the
# scheduled reconciliation job (src/services/scheduled_sync.py). The pure
# functions above carry the unit-tested logic; this only fetches + delegates.
# --------------------------------------------------------------------------- #

_PAGE = 1000


def _fetch_all(client, table: str, time_col: str, since: str, until: str) -> list[dict]:
    """Page through every row of ``table`` in [since, until) (Supabase caps at 1000)."""
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table(table)
            .select("*")
            .gte(time_col, since)
            .lt(time_col, until)
            .order(time_col)
            .range(start, start + _PAGE - 1)
            .execute()
        )
        batch = getattr(resp, "data", None) or []
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
        start += _PAGE


def admin_user_ids(client) -> frozenset:
    """User ids the shadow path skips (tier == 'admin'). Empty set on any failure."""
    try:
        resp = client.table("users").select("id").eq("tier", "admin").execute()
        return frozenset(
            r["id"] for r in (getattr(resp, "data", None) or []) if r.get("id") is not None
        )
    except Exception:
        return frozenset()


def reconcile_window(
    since: str,
    until: str,
    *,
    tolerance: Decimal = DEFAULT_TOLERANCE,
    min_cost: Decimal = DEFAULT_MIN_COST,
) -> tuple[ReconciliationReport, int]:
    """Fetch ledger + usage in [since, until) and reconcile. Returns (report, admin_count).

    Reads from the project the gateway is configured against (service-role key
    required — the ledger table is RLS-locked).
    """
    from src.config.supabase_config import get_supabase_client

    client = get_supabase_client()
    ledger_rows = _fetch_all(client, "credit_ledger", "created_at", since, until)
    usage_rows = _fetch_all(client, "usage_records", "timestamp", since, until)
    admins = admin_user_ids(client)
    report = reconcile_rows(
        ledger_rows,
        usage_rows,
        min_cost=min_cost,
        exclude_user_ids=admins,
        tolerance=tolerance,
    )
    return report, len(admins)


# Account-name re-exports so callers/tests need only this module.
__all__ = [
    "ALLOWANCE",
    "PURCHASED",
    "REVENUE",
    "DEFAULT_MIN_COST",
    "DEFAULT_TOLERANCE",
    "LedgerSummary",
    "UsageSummary",
    "UserDrift",
    "ReconciliationReport",
    "summarize_ledger",
    "summarize_usage",
    "reconcile",
    "reconcile_rows",
]
