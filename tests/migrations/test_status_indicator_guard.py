"""A latched breaker must not outrank the measurement beside it.

#2366. `model_status_current.status_indicator` tested the circuit breaker
FIRST and unconditionally, short-circuiting the uptime ladder underneath. One
trip published `offline` forever. Measured in production 2026-09-23:

    openai/gpt-4o   status_indicator = offline   circuit_breaker_state = open
                    uptime_24h = 100.0           uptime_7d = 100.0

Twenty-five advertised models were in that state.

WHY THIS TESTS THE STAGED FILE, NOT AN AUTO-APPLIED MIGRATION
-------------------------------------------------------------
I first shipped this as an ordinary migration using CREATE OR REPLACE VIEW.
It failed on push:

    ERROR: cannot change name of view column "active_incidents"
           to "active_incidents_count"  (SQLSTATE 42P16)

CREATE OR REPLACE VIEW can only APPEND columns; it fails outright on a rename,
retype or reorder, and leaves the OLD definition in place. The deployed view
has drifted from every migration file in the repo -- which is precisely why
`20260915000000_rebuild_model_status_views.sql` exists in staged-migrations,
using DROP + CREATE, the only form that converges. The fix belongs there.

And the first version of this test compared the new migration's columns
against the ORIGINAL 2025-11-28 file. It passed, and proved nothing: the
original file is not what is deployed. A baseline that is not production is
not a baseline.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGED = ROOT / "supabase" / "staged-migrations" / "20260915000000_rebuild_model_status_views.sql"
ROUTE = ROOT / "src" / "routes" / "status_page.py"


def _model_status_view_sql() -> str:
    text = STAGED.read_text()
    start = text.index("CREATE VIEW model_status_current")
    return text[start : text.index("FROM model_health_tracking", start)]


def _breaker_branch() -> str:
    m = re.search(
        r"WHEN\s+mht\.circuit_breaker_state\s*=\s*'open'(.*?)THEN",
        _model_status_view_sql(),
        re.S | re.I,
    )
    assert m, "the breaker branch disappeared entirely"
    return m.group(1)


def test_the_breaker_branch_is_guarded_by_a_measurement():
    assert "uptime_percentage_24h" in _breaker_branch(), (
        "the breaker decides the health verdict unconditionally again — one trip "
        "will publish 'offline' forever, whatever the measurement says"
    )


def test_absent_measurement_plus_open_breaker_is_still_offline():
    # No evidence must not read as healthy evidence, so the guard has to
    # COALESCE rather than let a NULL slip past the comparison.
    assert "COALESCE" in _breaker_branch().upper()


def test_the_uptime_ladder_is_still_reachable():
    sql = _model_status_view_sql()
    for threshold in ("99.9", "95.0", "50.0"):
        assert threshold in sql, f"the {threshold} rung of the ladder went missing"


def test_the_rebuild_uses_drop_and_create_not_replace():
    # CREATE OR REPLACE cannot rename, retype or reorder an existing column --
    # it fails and silently leaves the old definition in place, which is how
    # the deployed view drifted from every file in the repo.
    text = STAGED.read_text()
    assert "DROP VIEW IF EXISTS model_status_current CASCADE" in text
    assert "CREATE OR REPLACE VIEW model_status_current" not in text


def test_no_auto_applied_migration_tries_to_replace_this_view_again():
    # The trap, pinned so the next person does not re-lay it: an ordinary
    # migration touching this view WILL fail on push and look like a no-op.
    auto = ROOT / "supabase" / "migrations"
    offenders = [
        p.name
        for p in auto.glob("*.sql")
        if "CREATE OR REPLACE VIEW model_status_current" in p.read_text()
        and p.name > "20260915000000"
    ]
    assert not offenders, (
        f"{offenders} use CREATE OR REPLACE on a view whose deployed shape has drifted; "
        "put the change in supabase/staged-migrations/ instead"
    )


def test_the_view_column_matches_what_the_route_reads():
    # The route is the closest thing to production we can read offline: its own
    # comment records that the live view exposes `active_incidents`. If the
    # staged rebuild ever disagrees with what status_page.py reads, the endpoint
    # degrades to nulls.
    view = _model_status_view_sql()
    route = ROUTE.read_text()
    if 'model.get("active_incidents_count")' in route:
        assert "AS active_incidents_count" in view
    else:
        assert 'model.get("active_incidents")' in route, "the route stopped reading this field"
