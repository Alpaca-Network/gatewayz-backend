"""Guard: no query may name a `provider_budget_events` column the table lacks.

Third member of the family in this directory, and the one with the most to
lose. `provider_budget_events` backs the only operator-facing signal for
provider credit exhaustion — the condition that on 2026-09-16 took 57 of ~65
catalog models offline in production while the user-facing error said
"temporarily unavailable ... try again shortly" and nobody noticed for hours.

A stale column name here fails the read with PostgREST 42703, and
`_build_provider_budget_block` degrades through `_safe_block` to
`{"error": "..."}` under an HTTP 200. The panel renders that as "status
unavailable" rather than "all funded" — correct, but it means the alarm for a
silent outage would itself have failed silently. That is the specific loop this
test exists to break.

The column list was captured from the LIVE database (`ynleroehyrmaafkgjgmr`,
2026-09-16, via PostgREST's OpenAPI document) after migration
`20260916210000_provider_budget_events.sql` was CI-applied — not transcribed
from the migration file. Repo and database disagree more often than anyone
expects: seven production failures in Sep 2026 came from exactly that gap, and
#2341 added a duplicate index because it trusted the migration history over the
live schema.

Deliberately a hardcoded set rather than a live pull: CI has no guaranteed
database, and `conftest.py`'s autouse `skip_if_no_database` skips anything
whose path contains "db" — a guard that skips is worse than no guard, because
it reads as coverage. A stale-but-executing list beats a current-but-skipping
one. Re-capture when the table changes.
"""

from __future__ import annotations

import ast
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
TABLE = "provider_budget_events"

# public.provider_budget_events, captured live 2026-09-16.
PROVIDER_BUDGET_EVENTS_COLUMNS = frozenset(
    {
        "id",
        "provider",
        "reason",
        "first_seen_at",
        "last_seen_at",
        "occurrences",
        "sample_model",
    }
)


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "..."` assignments.

    Needed because the select list is hoisted into `_EVENT_COLUMNS` and reaches
    `.select()` as an ast.Name. Mutation-testing the sibling users guard on
    2026-09-16 found it blind to exactly this shape — a column list gets
    hoisted precisely when it is long and shared, i.e. when it matters most.
    """
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _columns(spec: str) -> list[str]:
    out = []
    for raw in spec.split(","):
        col = raw.strip().strip("\"'")
        if ":" in col:  # PostgREST rename alias
            col = col.split(":", 1)[1].strip()
        if col and col != "*" and "(" not in col:
            out.append(col)
    return out


def _offenders() -> list[str]:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if TABLE not in text:
            continue
        constants = _string_constants(ast.parse(text))
        for match in re.finditer(r"\.select\(\s*([A-Za-z_][A-Za-z0-9_]*|\"[^\"]*\"|'[^']*')", text):
            token = match.group(1)
            spec = constants.get(token) if token.isidentifier() else token.strip("\"'")
            if not spec:
                continue
            line = text.count("\n", 0, match.start()) + 1
            for col in _columns(spec):
                if col not in PROVIDER_BUDGET_EVENTS_COLUMNS:
                    offenders.append(
                        f"{path.relative_to(SRC.parent)}:{line} selects "
                        f"{TABLE}.{col}, which does not exist"
                    )
    return offenders


def test_no_query_names_a_missing_provider_budget_events_column():
    offenders = _offenders()
    assert not offenders, (
        "These queries name a column public.provider_budget_events does not have. "
        "PostgREST fails the whole query with 42703, and the operator alert for a "
        "provider outage would itself go silent:\n    " + "\n    ".join(offenders)
    )


def test_the_select_list_is_resolved_from_its_constant():
    """The scanner must follow `_EVENT_COLUMNS`, not skip it."""
    source = (
        '_EVENT_COLUMNS = "provider,reason,phantom_col"\n'
        'r = c.table("provider_budget_events").select(_EVENT_COLUMNS).execute()\n'
    )
    constants = _string_constants(ast.parse(source))
    assert constants.get("_EVENT_COLUMNS") == "provider,reason,phantom_col"
    bad = [
        c for c in _columns(constants["_EVENT_COLUMNS"]) if c not in PROVIDER_BUDGET_EVENTS_COLUMNS
    ]
    assert bad == ["phantom_col"], f"constant-held select list was not scanned: {bad}"


def test_the_real_columns_are_accepted():
    """Guards against a list so strict it flags valid code."""
    spec = "id,provider,reason,first_seen_at,last_seen_at,occurrences,sample_model"
    assert [c for c in _columns(spec) if c not in PROVIDER_BUDGET_EVENTS_COLUMNS] == []
