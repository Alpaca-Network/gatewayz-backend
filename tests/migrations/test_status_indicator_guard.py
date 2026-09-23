"""A latched breaker must not outrank the measurement beside it.

#2366. `model_status_current.status_indicator` tested the circuit breaker
FIRST and unconditionally, so it short-circuited the uptime ladder underneath
it. One trip published `offline` forever. Measured in production:

    openai/gpt-4o   status_indicator = offline   circuit_breaker_state = open
                    uptime_24h = 100.0           uptime_7d = 100.0

Twenty-five advertised models were in that state -- published as down while
serving every check for a week.

The verdict itself lives in SQL, so the end-to-end proof is the daily ops
check (`status_contradicts_uptime`), which reads the live surface and should
fall to zero once this deploys. What is pinned HERE is the pair of things a
unit test can actually catch:

1. the breaker branch is guarded by a measurement, not unconditional
2. the replacement view exposes exactly the original columns, in order --
   `CREATE OR REPLACE VIEW` refuses otherwise, and `status_page.py` turns a
   missing column into a 500 (it has done so before).
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
ORIGINAL = MIGRATIONS / "20251128000000_enhance_model_health_tracking.sql"
REPLACEMENT = MIGRATIONS / "20260923000000_status_indicator_measurement_beats_latched_breaker.sql"


def _view_sql(path: Path) -> str:
    text = path.read_text()
    start = text.index("CREATE OR REPLACE VIEW model_status_current AS")
    return text[start : text.index(";", start)]


def _columns(view_sql: str) -> list[str]:
    """Output column names, in order, as the view exposes them."""
    body = view_sql[
        view_sql.index("SELECT") + len("SELECT") : view_sql.index("FROM model_health_tracking")
    ]
    cols, depth, current = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(current)
            current = ""
        else:
            current += ch
    cols.append(current)

    names = []
    for c in cols:
        c = " ".join(c.split())
        if not c:
            continue
        m = re.search(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", c, re.IGNORECASE)
        names.append(m.group(1) if m else c.split(".")[-1])
    return names


def test_the_breaker_branch_is_guarded_by_a_measurement():
    sql = _view_sql(REPLACEMENT)
    m = re.search(r"WHEN\s+mht\.circuit_breaker_state\s*=\s*'open'(.*?)THEN", sql, re.S | re.I)
    assert m, "the breaker branch disappeared entirely"
    guard = m.group(1)
    assert "uptime_percentage_24h" in guard, (
        "the breaker decides the health verdict unconditionally again — one trip "
        "will publish 'offline' forever, whatever the measurement says"
    )


def test_absent_measurement_plus_open_breaker_is_still_offline():
    # The carve-out. No evidence must not read as healthy evidence, so the
    # guard has to COALESCE rather than let NULL fall through the comparison.
    guard = re.search(
        r"WHEN\s+mht\.circuit_breaker_state\s*=\s*'open'(.*?)THEN",
        _view_sql(REPLACEMENT),
        re.S | re.I,
    ).group(1)
    assert (
        "COALESCE" in guard.upper()
    ), "a NULL uptime would slip past the guard and read as healthy"


def test_the_uptime_ladder_is_still_reachable():
    sql = _view_sql(REPLACEMENT)
    for threshold in ("99.9", "95.0", "50.0"):
        assert threshold in sql, f"the {threshold} rung of the ladder went missing"


def test_the_replacement_exposes_exactly_the_original_columns():
    # CREATE OR REPLACE VIEW refuses a changed column list, and status_page.py
    # turns a missing column into a blanket 500 — it already did that once.
    assert _columns(_view_sql(REPLACEMENT)) == _columns(_view_sql(ORIGINAL))


def test_the_original_really_was_unconditional():
    # Proves this test can tell the two apart, rather than passing on anything
    # that mentions a breaker.
    guard = re.search(
        r"WHEN\s+mht\.circuit_breaker_state\s*=\s*'open'(.*?)THEN", _view_sql(ORIGINAL), re.S | re.I
    ).group(1)
    assert "uptime_percentage_24h" not in guard
