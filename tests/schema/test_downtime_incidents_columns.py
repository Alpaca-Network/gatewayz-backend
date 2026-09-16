"""Guard: no query may reference a `downtime_incidents` column that does not exist.

Same defect class as test_users_table_columns.py, caught before it shipped
rather than after. The admin panel's downtime pages render
`incident.resolved_at`, but there is no `resolved_at` column -- the real one is
`ended_at`. A select naming `resolved_at` would get 42703 from PostgREST, which
fails the *entire* query, and the downtime module's read paths used to swallow
that into an empty list. The backend therefore maps `ended_at` -> `resolved_at`
in the response layer only (`_to_summary` in src/routes/admin_downtime.py), and
this guard makes sure the name never leaks back down into a query.

Column list captured from the live production schema of project
`ynleroehyrmaafkgjgmr` on 2026-09-16, via PostgREST's OpenAPI document
(`Accept: application/openapi+json` on /rest/v1/), not hand-written and not
read off the migration. It matches
supabase/migrations/20260212000000_create_downtime_incidents_table.sql exactly.

Deliberately a hardcoded set rather than a live schema pull: CI has no reliable
database, so a live pull would either fail the job or -- far worse -- skip green
and read as coverage it is not providing. A stale-but-executing list beats a
current-but-skipping one. Re-capture the set when a migration genuinely adds or
drops a column, and update the date above.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

TABLE = "downtime_incidents"

# public.downtime_incidents as it exists in production (see module docstring).
DOWNTIME_INCIDENTS_COLUMNS = frozenset(
    {
        "id",
        "started_at",
        "detected_at",
        "ended_at",
        "duration_seconds",
        "health_endpoint",
        "error_message",
        "http_status_code",
        "response_body",
        "status",
        "severity",
        "logs_captured",
        "logs_file_path",
        "log_count",
        "environment",
        "server_info",
        "metrics_snapshot",
        "notified_at",
        "resolved_by",
        "notes",
        "created_at",
        "updated_at",
    }
)

# Names the panel uses that are NOT columns. Mapping these in the response layer
# is fine; naming them in a query is the bug this file exists to prevent.
PANEL_ONLY_FIELDS = frozenset({"resolved_at"})

# Modules that query this table and nothing else. Filter helpers there take the
# query builder as a *parameter* (`_apply_filters(query, ...)`), so chain-root
# tracking cannot reach them -- every literal column argument in these files is
# checked instead. Keep this list to single-table modules or it will produce
# false positives from other tables' column names.
SINGLE_TABLE_MODULES = ("db/downtime_incidents.py",)

FILTER_METHODS = frozenset(
    {
        "eq",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "like",
        "ilike",
        "in_",
        "is_",
        "is_not",
        "contains",
        "order",
    }
)
WRITE_METHODS = frozenset({"insert", "update", "upsert"})


def _is_table_call(node: ast.AST) -> bool:
    """True for `<anything>.table("downtime_incidents")`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "table"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == TABLE
    )


def _chain_root(node: ast.AST) -> ast.AST | None:
    """Walk `a.b(1).c(2)` back to `a`, returning the innermost receiver."""
    for _ in range(50):
        if _is_table_call(node):
            return node
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return node
    return None


def _split_select_spec(spec: str) -> list[str]:
    """Column names out of a PostgREST select spec, ignoring embedded resources.

    Splits on top-level commas only, so `models!inner(a, b)` stays in one piece.
    """
    depth = 0
    current = ""
    parts: list[str] = []
    for ch in spec:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)

    columns = []
    for part in parts:
        name = part.strip()
        if not name or name == "*" or "(" in name:
            continue  # "*" or an embedded resource, not a column of this table
        name = name.split("::")[0].strip()
        if ":" in name:  # alias:column
            name = name.split(":", 1)[1].strip()
        columns.append(name)
    return columns


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level `NAME = "..."` assignments.

    Needed because a select list is often hoisted into a constant
    (`.select(_LIST_COLUMNS)`), which reaches the call site as an ast.Name. A
    scanner that only reads literal arguments sees nothing there and passes --
    a blind spot that silently covers the exact query most worth guarding.
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
        if isinstance(value, str):
            constants[target.id] = value
    return constants


def _columns_from_call(
    call: ast.Call, method: str, constants: dict[str, str] | None = None
) -> list[str]:
    constants = constants or {}
    if method == "select":
        columns: list[str] = []
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                columns.extend(_split_select_spec(arg.value))
            elif isinstance(arg, ast.Name) and arg.id in constants:
                columns.extend(_split_select_spec(constants[arg.id]))
        return columns
    if method in FILTER_METHODS:
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0].value
            if isinstance(value, str):
                return [value]
        return []
    if method in WRITE_METHODS:
        columns = []
        for arg in call.args:
            if isinstance(arg, ast.Dict):
                for key in arg.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        columns.append(key.value)
        return columns
    return []


def _walk_scope(node: ast.AST):
    """ast.walk, but it does not descend into nested function/class scopes, so a
    local named `query` in one function is not confused with another's."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_scope(child)


def _is_terminal(node: ast.AST) -> bool:
    """`...execute()` / `...execute().data` yields a result, not a builder."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    )


def _scan_scope(
    body: list[ast.stmt], scan_all_literals: bool, constants: dict[str, str] | None = None
) -> list[tuple[int, str]]:
    """Columns referenced by downtime_incidents queries inside one scope."""
    query_vars: set[str] = set()
    for _ in range(4):  # fixpoint; chains in this codebase are 2-3 deep
        before = set(query_vars)
        for stmt in body:
            for node in _walk_scope(stmt):
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name) or _is_terminal(node.value):
                    continue
                root = _chain_root(node.value)
                if _is_table_call(root) or (isinstance(root, ast.Name) and root.id in query_vars):
                    query_vars.add(target.id)
        if query_vars == before:
            break

    found: list[tuple[int, str]] = []
    for stmt in body:
        for node in _walk_scope(stmt):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in FILTER_METHODS | WRITE_METHODS | {"select"}:
                continue
            if not scan_all_literals:
                root = _chain_root(node.func.value)
                if not (
                    _is_table_call(root) or (isinstance(root, ast.Name) and root.id in query_vars)
                ):
                    continue
            for column in _columns_from_call(node, method, constants):
                if "." in column:
                    continue  # embedded resource filter
                found.append((node.lineno, column))
    return found


def _offenders() -> list[str]:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()
        scan_all_literals = rel in SINGLE_TABLE_MODULES
        constants = _module_string_constants(tree)

        scopes: list[list[ast.stmt]] = [tree.body]
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                scopes.append(node.body)

        for scope in scopes:
            for lineno, column in _scan_scope(scope, scan_all_literals, constants):
                if column in DOWNTIME_INCIDENTS_COLUMNS:
                    continue
                problems.append(f"{rel}:{lineno} references {TABLE}.{column}")
    return sorted(set(problems))


def _list_columns_constant() -> list[str]:
    """Read `_LIST_COLUMNS` out of src/db/downtime_incidents.py by parsing it.

    Read, not imported: this guard must run with no database and no environment
    configured, and importing src.db pulls in the Supabase config at module
    scope.
    """
    tree = ast.parse((SRC / "db" / "downtime_incidents.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "_LIST_COLUMNS":
            value = ast.literal_eval(node.value)
            return _split_select_spec(value)
    raise AssertionError("_LIST_COLUMNS not found in src/db/downtime_incidents.py")


def test_no_query_references_a_nonexistent_downtime_incidents_column():
    offenders = _offenders()
    assert not offenders, (
        f"These queries name a `{TABLE}` column that does not exist in production. "
        "PostgREST fails the whole query with 42703:\n  " + "\n  ".join(offenders)
    )


def test_list_select_names_only_real_columns():
    """The paginated list's select list, pinned against the prod column set."""
    unknown = sorted(set(_list_columns_constant()) - DOWNTIME_INCIDENTS_COLUMNS)
    assert not unknown, f"_LIST_COLUMNS names columns that do not exist: {unknown}"


def test_list_select_excludes_the_bulk_payload_columns():
    """logs_captured and response_body are fetched per-incident, never for a
    50-row page -- that payload blows the admin panel's 15s proxy timeout."""
    columns = set(_list_columns_constant())
    assert "logs_captured" not in columns
    assert "response_body" not in columns


def test_resolved_at_is_never_used_as_a_column():
    """`resolved_at` is the panel's field name for the `ended_at` column. The
    rename belongs in the response layer; a query naming it returns 42703."""
    offenders = [o for o in _offenders() if any(f".{f}" in o for f in PANEL_ONLY_FIELDS)]
    assert not offenders, (
        "A query names a panel-only field as a column. Map it in the response "
        "layer instead:\n  " + "\n  ".join(offenders)
    )


def test_scanner_detects_a_planted_phantom_column(tmp_path, monkeypatch):
    """The guard is only useful if it actually catches a bad column."""
    import tests.schema.test_downtime_incidents_columns as module

    fake_src = tmp_path / "src"
    fake_src.mkdir()
    (fake_src / "bad.py").write_text(
        'client.table("downtime_incidents").select("id, resolved_at").eq("closed_at", 1).execute()\n'
    )
    monkeypatch.setattr(module, "SRC", fake_src)

    offenders = module._offenders()
    assert any("downtime_incidents.resolved_at" in o for o in offenders)
    assert any("downtime_incidents.closed_at" in o for o in offenders)


def test_scanner_accepts_valid_columns_and_split_builders(tmp_path, monkeypatch):
    import tests.schema.test_downtime_incidents_columns as module

    fake_src = tmp_path / "src"
    fake_src.mkdir()
    (fake_src / "good.py").write_text(
        "query = client.table('downtime_incidents').select('id, started_at, ended_at')\n"
        "query = query.eq('status', 'ongoing')\n"
        "query = query.order('started_at', desc=True)\n"
        "result = query.execute()\n"
    )
    monkeypatch.setattr(module, "SRC", fake_src)

    assert module._offenders() == []


def test_scanner_ignores_other_tables(tmp_path, monkeypatch):
    """A column that is fine on another table must not be reported here."""
    import tests.schema.test_downtime_incidents_columns as module

    fake_src = tmp_path / "src"
    fake_src.mkdir()
    (fake_src / "other.py").write_text(
        "client.table('users').select('id, email, last_login').execute()\n"
    )
    monkeypatch.setattr(module, "SRC", fake_src)

    assert module._offenders() == []


def test_scanner_resolves_a_select_list_held_in_a_constant(tmp_path, monkeypatch):
    """Regression: the first version of this scanner only read literal select
    arguments, so `.select(_LIST_COLUMNS)` -- the one query in this codebase
    that actually uses a hoisted column list -- slipped straight past it."""
    import tests.schema.test_downtime_incidents_columns as module

    fake_src = tmp_path / "src"
    fake_src.mkdir()
    (fake_src / "hoisted.py").write_text(
        '_COLS = "id,started_at,resolved_at"\n'
        'client.table("downtime_incidents").select(_COLS).execute()\n'
    )
    monkeypatch.setattr(module, "SRC", fake_src)

    assert any("downtime_incidents.resolved_at" in o for o in module._offenders())
