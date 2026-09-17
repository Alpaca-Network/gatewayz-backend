"""Guard: the coupon redemption RPC keeps the properties that make it atomic.

``public.redeem_coupon()`` is the only thing standing between a coupon and a
double payout. Its safety is not in its Python caller -- it is in four specific
SQL constructs, each of which is one careless edit from disappearing while the
function still looks correct and every mocked Python test still passes:

  1. ``SELECT ... FOR UPDATE`` on the coupon row -- the serialization point.
     Delete two words and concurrent redemptions of a max_uses=1 coupon both
     succeed. Nothing else in the suite notices.
  2. ``times_used = times_used + 1`` computed IN the UPDATE. Rewriting it as a
     value computed beforehand reintroduces the read-modify-write that makes
     the times_used_within_limit CHECK unable to fire.
  3. A DETERMINISTIC request_id into atomic_add_credits. Swapping it for
     gen_random_uuid() silently turns the ledger's unique index from a
     redemption guard into a no-op.
  4. No EXECUTE grant to anon/authenticated. This function is SECURITY DEFINER
     and grants credits; a grant to a PostgREST-reachable role is a public
     "give me money" endpoint.

This lives in tests/schema/ rather than tests/migrations/ for a reason that is
itself an incident: the CI matrix in .github/workflows/ci.yml enumerates test
directories explicitly, and tests/migrations/ appears in NONE of them, exactly
as tests/schema/ did not until 2026-09-16. A guard nobody runs is worse than no
guard, because it reads as coverage.

Static analysis is the whole method here, deliberately: no Supabase is reachable
in CI, and tests/conftest.py's autouse skip_if_no_database would skip anything
that needed one -- turning this guard into a green skip.
"""

from __future__ import annotations

import pathlib
import re

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "20260917000000_add_redeem_coupon_rpc.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _body() -> str:
    """The function body only -- between the $$ delimiters.

    Every assertion below is about executable SQL, and this file's header
    comments name most of the same constructs in prose. Matching the whole file
    would let a guard pass on a comment that says "FOR UPDATE" while the code
    no longer does it.
    """
    parts = _sql().split("$$")
    assert len(parts) >= 3, "expected a $$-quoted function body"
    return parts[1]


def test_migration_file_exists():
    assert MIGRATION.is_file(), MIGRATION


def test_locks_the_coupon_row_for_update():
    """Property 1: the serialization point exists, on the coupons select."""
    body = _body()
    match = re.search(
        r"SELECT\s+\*\s+INTO\s+v_coupon\s+FROM\s+public\.coupons\s+WHERE\s+.*?FOR\s+UPDATE",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "the coupon lookup must take a row lock with SELECT ... FOR UPDATE"


def test_coupon_lookup_is_case_insensitive():
    """Agrees with idx_coupons_code_upper and is_coupon_redeemable(), so a
    lower-cased code cannot silently miss a stored coupon."""
    assert re.search(
        r"WHERE\s+UPPER\(code\)\s*=\s*UPPER\(", _body(), re.IGNORECASE
    ), "coupon lookup must match on UPPER(code)"


def test_times_used_is_incremented_in_the_update_statement():
    """Property 2: the counter is computed by Postgres, not handed to it."""
    assert re.search(
        r"UPDATE\s+public\.coupons\s+SET\s+times_used\s*=\s*times_used\s*\+\s*1",
        _body(),
        re.IGNORECASE,
    ), "times_used must be incremented as `times_used = times_used + 1` in the UPDATE"


def test_request_id_is_deterministic_in_coupon_and_user():
    """Property 3: the ledger's unique index actually guards the redemption.

    A random uuid would make atomic_add_credits idempotent only against a
    retry of the same in-flight call, which is not the guarantee wanted here.
    """
    body = _body()
    assert re.search(
        r"v_request_id\s*:=\s*md5\(.*coupon_redemption.*v_coupon\.id.*p_user_id.*\)\s*::\s*UUID",
        body,
        re.IGNORECASE | re.DOTALL,
    ), "request_id must be derived deterministically from (coupon_id, user_id)"
    assert "gen_random_uuid" not in body.lower(), "a random request_id defeats ledger idempotency"


def test_grant_and_both_ledger_writes_are_in_one_function_body():
    """No COMMIT splits the grant from the rows that record it.

    PostgREST runs one RPC call in one transaction, so 'same body' means 'same
    transaction' -- which is what makes a credited user without a redemption
    row (or the reverse) unrepresentable.
    """
    body = _body()
    assert "atomic_add_credits" in body
    assert re.search(r"INSERT\s+INTO\s+public\.coupon_redemptions", body, re.IGNORECASE)
    assert re.search(r"UPDATE\s+public\.coupons", body, re.IGNORECASE)
    assert not re.search(r"(?<![_.\w])COMMIT\s*;", body, re.IGNORECASE), "no COMMIT inside the body"


def test_credits_go_to_purchased_not_allowance():
    """Coupon credits must survive a subscription cancellation.

    subscription_allowance is forfeited on cancel by design; crediting there
    would quietly expire money a user was given.
    """
    assert re.search(
        r"p_target\s*:=\s*'purchased'", _body()
    ), "coupon credits must target purchased_credits"


def test_enforces_every_eligibility_invariant():
    """Each invariant is checked, and each has its own reason code."""
    body = _body()
    checks = {
        "is_active": r"v_coupon\.is_active\s*=\s*false",
        "valid_from": r"v_now\s*<\s*v_coupon\.valid_from",
        "valid_until": r"v_now\s*>\s*v_coupon\.valid_until",
        "user_specific assignment": (
            r"v_coupon\.coupon_scope\s*=\s*'user_specific'\s*"
            r"AND\s+v_coupon\.assigned_to_user_id\s+IS\s+DISTINCT\s+FROM\s+p_user_id"
        ),
        "max uses": r"v_coupon\.times_used\s*>=\s*v_coupon\.max_uses",
        "already redeemed": r"FROM\s+public\.coupon_redemptions\s+WHERE\s+coupon_id",
    }
    for name, pattern in checks.items():
        assert re.search(pattern, body, re.IGNORECASE | re.DOTALL), f"missing check: {name}"


def test_max_uses_is_enforced_for_every_scope():
    """The 2025 is_coupon_redeemable() gates its max-uses check on
    `coupon_scope = 'global'`. This function must not copy that: a ceiling
    enforced for only some scopes is one schema change from no ceiling."""
    body = _body()
    # The condition is everything between the nearest `IF` and the comparison;
    # `THEN` and a nested `IF` are excluded so the match cannot run backwards
    # into the preceding (legitimately scope-gated) assignment check.
    match = re.search(
        r"\bIF\s+((?:(?!\bTHEN\b|\bIF\b).)*?)v_coupon\.times_used\s*>=\s*v_coupon\.max_uses",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "max-uses check not found"
    assert "coupon_scope" not in match.group(1), "the max-uses check must not be scope-gated"


def test_every_failure_reason_is_distinct():
    """Expired, exhausted, wrong account and already-redeemed must not collapse
    into one 'invalid coupon'."""
    body = _body()
    for code in (
        "COUPON_NOT_FOUND",
        "COUPON_INACTIVE",
        "COUPON_NOT_YET_ACTIVE",
        "COUPON_EXPIRED",
        "COUPON_NOT_ASSIGNED",
        "MAX_USES_EXCEEDED",
        "ALREADY_REDEEMED",
        "USER_NOT_FOUND",
        "REDEMPTION_FAILED",
    ):
        assert f"'{code}'" in body, f"missing error_code {code}"


def test_constraint_backstops_are_mapped_by_constraint_name():
    """A bare `WHEN check_violation` would report balance_change_matches_value
    as 'coupon exhausted' -- a confident wrong reason."""
    body = _body()
    assert "GET STACKED DIAGNOSTICS" in body.upper()
    assert "times_used_within_limit" in body
    assert "uq_coupon_user" in body
    assert "idx_credit_transactions_request_id" in body


def test_raises_rather_than_returns_when_the_grant_fails():
    """A RETURN after a failed grant would COMMIT the partial redemption."""
    assert re.search(
        r"RAISE\s+EXCEPTION\s+'coupon_grant_failed", _body(), re.IGNORECASE
    ), "a failed credit grant must raise so the transaction rolls back"


def test_is_not_executable_by_postgrest_reachable_roles():
    """Property 4. SECURITY DEFINER + a grant to `anon` would be an
    unauthenticated credit faucet."""
    sql = _sql()
    for role in ("PUBLIC", "anon", "authenticated"):
        assert re.search(
            rf"REVOKE\s+ALL\s+ON\s+FUNCTION\s+public\.redeem_coupon\b.*?FROM\s+{role}\b",
            sql,
            re.IGNORECASE | re.DOTALL,
        ), f"missing REVOKE from {role}"

    granted = set(
        re.findall(
            r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.redeem_coupon\b[^;]*?\bTO\s+(\w+)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
    )
    assert granted == {"service_role"}, f"unexpected EXECUTE grantees: {sorted(granted)}"


def test_security_definer_pins_its_search_path():
    """An unpinned search_path in a SECURITY DEFINER body lets a caller shadow
    `coupons` or `atomic_add_credits` with objects of their own."""
    sql = _sql()
    assert re.search(r"SECURITY\s+DEFINER", sql, re.IGNORECASE)
    assert re.search(
        r"SET\s+search_path\s*=\s*public,\s*pg_temp", sql, re.IGNORECASE
    ), "SECURITY DEFINER function must pin search_path"


def test_migration_is_idempotent():
    """The migration runner may re-apply; a bare CREATE FUNCTION with a changed
    signature would leave two overloads and make RPC dispatch ambiguous."""
    sql = _sql()
    assert re.search(
        r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+public\.redeem_coupon", sql, re.IGNORECASE
    ), "migration must drop the prior signature before creating"
    assert re.search(r"CREATE\s+OR\s+REPLACE\s+FUNCTION", sql, re.IGNORECASE)


def test_no_concurrent_index_creation():
    """Repo convention: `supabase db push` wraps the push in a transaction, so
    CREATE INDEX CONCURRENTLY cannot run inside a migration file."""
    assert "CONCURRENTLY" not in _sql().upper()
