"""Guard: no query may reference a `users` column that does not exist in prod.

Three production incidents came from exactly this defect -- a select or filter
naming a column that had been dropped (`users.credits`,
`users.role_metadata`), never existed on this table (`users.last_login`, which
lives on `admin_users`), or never existed at all (`users.api_usage_count`).
PostgREST answers 42703 and fails the *entire* query, so one stale column name
turns a whole endpoint into a 500 or -- worse, where the caller swallows the
error -- into a silently empty result.

Mocking a 500 would only pin the three call sites we already know about. This
test instead reads the source and checks every `users` query statically, so the
next stale column name fails in CI rather than in prod.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

# public.users as it exists in production. Keep in sync with
# supabase/migrations/ when a column is genuinely added or dropped.
USERS_COLUMNS = frozenset(
    {
        "id",
        "username",
        "email",
        "api_key",
        "is_active",
        "registration_date",
        "auth_method",
        "subscription_status",
        "trial_expires_at",
        "welcome_email_sent",
        "privy_user_id",
        "created_at",
        "updated_at",
        "referral_code",
        "referred_by_code",
        "has_made_first_purchase",
        "role",
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_product_id",
        "tier",
        "subscription_end_date",
        "email_updates_enabled",
        "preferences",
        "subscription_allowance",
        "purchased_credits",
        "allowance_reset_date",
        "settings",
        "partner_code",
        "partner_trial_id",
        "partner_signup_timestamp",
        "partner_metadata",
        "privy_app_id",
    }
)

# Known references to columns that do not exist, deliberately left in place.
# Each entry needs a reason; removing the code is preferable to growing this.
KNOWN_EXCEPTIONS = {
    # _migrate_legacy_credits(): backfill for the dropped users.credits column.
    # Inert in prod -- the row never carries `credits`, so the function returns
    # before issuing the update -- but the literals are still in the source.
    ("db/users.py", "credits"),
    # AnalyticsService.get_trial_analytics(): the trial system was removed in
    # Apr 2026 and users.api_usage_count never existed in any migration. The
    # funnel needs a real data source (usage_records) or deletion; neither is
    # a mechanical fix, so it is tracked rather than guessed at.
    ("services/analytics.py", "api_usage_count"),
}

# PostgREST builder methods whose first string argument is a column name.
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


def _is_users_table_call(node: ast.AST) -> bool:
    """True for `<anything>.table("users")`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "table"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "users"
    )


def _chain_root(node: ast.AST) -> ast.AST | None:
    """Walk `a.b(1).c(2)` back to `a`, returning the innermost receiver."""
    seen = 0
    while seen < 50:
        seen += 1
        if _is_users_table_call(node):
            return node
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return node
    return None


# Column lists are not always literals at the call site. A module that hoists its
# spec into a constant -- `_LIST_COLUMNS = "id, email"` then `.select(_LIST_COLUMNS)`
# -- reaches this scanner as an ast.Name and used to sail straight past, which
# meant the queries most worth guarding (the long, shared, carefully-maintained
# ones) were exactly the queries not guarded. Mutation-tested: planting a phantom
# column behind a constant went undetected before this.
_STRING_CONSTANTS: dict[str, str] = {}


def _collect_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module- and class-level `NAME = "..."` assignments."""
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


def _spec_of(arg: ast.expr) -> str | None:
    """The literal select spec behind an argument, resolving constants."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Name):
        return _STRING_CONSTANTS.get(arg.id)
    return None


def _columns_from_select(call: ast.Call) -> list[str]:
    """Column names out of `.select("a, b", "c", count="exact")`."""
    columns: list[str] = []
    for arg in call.args:
        spec = _spec_of(arg)
        if spec is None:
            continue
        depth = 0
        current = ""
        parts: list[str] = []
        # Split on top-level commas so embedded resources such as
        # "api_keys_new!inner(api_key)" stay in one piece.
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

        for part in parts:
            name = part.strip()
            if not name or name == "*" or "(" in name:
                continue  # "*" or an embedded resource, not a users column
            name = name.split("::")[0].strip()
            if ":" in name:  # alias:column
                name = name.split(":", 1)[1].strip()
            columns.append(name)
    return columns


def _columns_from_call(call: ast.Call, method: str) -> list[str]:
    if method == "select":
        return _columns_from_select(call)
    if method in FILTER_METHODS:
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0].value
            if isinstance(value, str):
                return [value]
        return []
    if method == "or_":
        # "is_active.is.null,is_active.eq.true"
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0].value
            if isinstance(value, str):
                return [clause.split(".")[0].strip() for clause in value.split(",")]
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
    """ast.walk, but it does not descend into nested function/class scopes.

    Without this, a local named `query` or `result` in one function would be
    confused with a same-named users query builder in another.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
        return  # its own body is scanned as a separate scope
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


def _scan_scope(body: list[ast.stmt]) -> list[tuple[int, str]]:
    """Columns referenced by users-table queries inside one scope.

    Tracks locals assigned from a users query (`q = client.table("users")...`,
    then `q = q.ilike(...)`) so split query builders are covered too.
    """
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
                if _is_users_table_call(root) or (
                    isinstance(root, ast.Name) and root.id in query_vars
                ):
                    query_vars.add(target.id)
        if query_vars == before:
            break

    found: list[tuple[int, str]] = []
    for stmt in body:
        for node in _walk_scope(stmt):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in FILTER_METHODS | WRITE_METHODS | {"select", "or_"}:
                continue
            root = _chain_root(node.func.value)
            if not (
                _is_users_table_call(root) or (isinstance(root, ast.Name) and root.id in query_vars)
            ):
                continue
            for column in _columns_from_call(node, method):
                if "." in column:
                    continue  # embedded resource filter, e.g. api_keys_new.api_key
                found.append((node.lineno, column))
    return found


def _offenders() -> list[str]:
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        global _STRING_CONSTANTS
        _STRING_CONSTANTS = _collect_string_constants(tree)
        rel = path.relative_to(SRC).as_posix()

        scopes: list[list[ast.stmt]] = [tree.body]
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                scopes.append(node.body)

        for scope in scopes:
            for lineno, column in _scan_scope(scope):
                if column in USERS_COLUMNS or (rel, column) in KNOWN_EXCEPTIONS:
                    continue
                problems.append(f"{rel}:{lineno} references users.{column}")
    return sorted(set(problems))


def test_no_query_references_a_nonexistent_users_column():
    offenders = _offenders()
    assert not offenders, (
        "These queries name a `users` column that does not exist in production. "
        "PostgREST fails the whole query with 42703:\n  " + "\n  ".join(offenders)
    )


def test_scanner_detects_a_planted_phantom_column(tmp_path, monkeypatch):
    """The guard is only useful if it actually catches a bad column."""
    import tests.schema.test_users_table_columns as module

    fake_src = tmp_path / "src"
    fake_src.mkdir()
    (fake_src / "bad.py").write_text(
        'client.table("users").select("id, credits").eq("last_login", 1).execute()\n'
    )
    monkeypatch.setattr(module, "SRC", fake_src)

    offenders = module._offenders()
    assert any("users.credits" in o for o in offenders)
    assert any("users.last_login" in o for o in offenders)


def test_scanner_accepts_embedded_resources_and_valid_columns(tmp_path, monkeypatch):
    import tests.schema.test_users_table_columns as module

    fake_src = tmp_path / "src"
    fake_src.mkdir()
    (fake_src / "good.py").write_text(
        "query = client.table('users').select("
        "'id, username, subscription_allowance, purchased_credits, "
        "api_keys_new!inner(api_key)')\n"
        "query = query.ilike('api_keys_new.api_key', pattern)\n"
        "query = query.eq('is_active', True)\n"
        "result = query.execute()\n"
    )
    monkeypatch.setattr(module, "SRC", fake_src)

    assert module._offenders() == []


def test_scanner_resolves_a_select_list_held_in_a_constant():
    """A hoisted column list must be scanned, not skipped.

    Found by mutation-testing this guard on 2026-09-16: planting a phantom
    column in a literal `.select("...")` was caught, but planting the same
    column behind `_COLS = "..."` / `.select(_COLS)` was not. That is the
    worst possible shape to miss -- a column list gets hoisted into a
    constant precisely when it is long, shared and carefully maintained,
    which is to say when it is most worth guarding.
    """
    global _STRING_CONSTANTS

    # The call must sit at module scope: _walk_scope deliberately does not
    # descend into nested function scopes, and _scan_scope is driven per-scope
    # by the real scanner.
    source = (
        '_COLS = "id, email, phantom_column"\n'
        'result = client.table("users").select(_COLS).execute()\n'
    )
    tree = ast.parse(source)
    _STRING_CONSTANTS = _collect_string_constants(tree)

    assert _STRING_CONSTANTS == {"_COLS": "id, email, phantom_column"}

    found = [col for _line, col in _scan_scope(tree.body)]
    assert "phantom_column" in found, f"constant-held select list was not scanned: {found}"
    assert "email" in found, "resolving the constant must not lose the valid columns"

    _STRING_CONSTANTS = {}
