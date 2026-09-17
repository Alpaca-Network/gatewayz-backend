"""Guard: the case-collision migration keeps the properties that make it safe.

`coupons.code` is UNIQUE case-SENSITIVELY while every lookup matches on
UPPER(code), so 'WELCOME' and 'welcome' are one code to a user and two
`coupon_id`s to `uq_coupon_user` -- which lets one user redeem the same typed
code twice. Measured on Postgres 16: a user was paid $50 on Monday and $5 on
Tuesday from one typed code, after nothing more exotic than an admin edit and a
`VACUUM FULL`.

Three properties of the fix are worth pinning, because each is a plausible edit
that looks like an improvement:

  1. **The pre-flight reports the rows, not just the key.** The raw 23505 from
     CREATE UNIQUE INDEX names the duplicated value and nothing else. Whoever
     hits this at deploy time needs the ids, what each is worth, and which has
     already paid out -- otherwise they cannot decide anything.
  2. **No DELETE or UPDATE repairs the collision.** Choosing which of two
     colliding coupons survives is a product decision: both are money, either
     may carry redemptions, and nothing records which spelling users were given.
     The obvious "fix" for a failing migration is to make it stop failing, and
     that is exactly the edit this test exists to catch.
  3. **No CREATE INDEX CONCURRENTLY.** Repo convention: `supabase db push`
     wraps the push in a transaction, which CONCURRENTLY cannot run inside.

Static analysis, like its siblings in this directory: no Supabase is reachable
in CI, and tests/conftest.py's autouse skip_if_no_database would turn anything
needing one into a green skip.
"""

from __future__ import annotations

import pathlib
import re

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
MIGRATION = MIGRATIONS / "20260917020000_unique_coupon_code_upper.sql"
REDEEM_MIGRATION = MIGRATIONS / "20260917010000_add_redeem_coupon_rpc.sql"
REAPPLY_MIGRATION = MIGRATIONS / "20260917015000_redeem_coupon_deterministic_lookup.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _executable() -> str:
    """The migration with `--` comment lines stripped.

    Needed because this file argues in prose against the very things it must
    not do -- it explains why it is not CONCURRENTLY and why it must not
    DELETE. Matching the raw text would fail on the explanation rather than on
    the code, which is the same "the comment says it, so it must be true"
    confusion the sibling guard in test_redeem_coupon_rpc.py avoids by reading
    only the function body.
    """
    return "\n".join(line for line in _sql().splitlines() if not line.strip().startswith("--"))


def test_migration_file_exists():
    assert MIGRATION.is_file(), MIGRATION


def test_creates_the_case_insensitive_unique_index():
    assert re.search(
        r"CREATE\s+UNIQUE\s+INDEX\s+(IF\s+NOT\s+EXISTS\s+)?uq_coupons_code_upper\s+"
        r"ON\s+public\.coupons\s*\(\s*UPPER\(code\)\s*\)",
        _sql(),
        re.IGNORECASE,
    ), "the index must be UNIQUE and on UPPER(code)"


def test_index_creation_is_idempotent():
    """The migration runner may re-apply."""
    assert re.search(
        r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS", _sql(), re.IGNORECASE
    ), "use IF NOT EXISTS so a re-apply is a no-op"


def test_no_concurrent_index_creation():
    """`supabase db push` wraps the push in a transaction; CONCURRENTLY cannot
    run inside one."""
    assert "CONCURRENTLY" not in _executable().upper()


class TestPreflight:
    """Property 1: fail with the rows, not just the key."""

    def test_detects_collisions_case_insensitively(self):
        sql = _sql()
        assert re.search(
            r"GROUP\s+BY\s+UPPER\(c\.code\)", sql, re.IGNORECASE
        ), "collisions must be grouped by UPPER(code)"
        assert re.search(r"HAVING\s+count\(\*\)\s*>\s*1", sql, re.IGNORECASE)

    def test_raises_rather_than_warning(self):
        """A NOTICE would scroll past in deploy logs and the index would then
        fail anyway, with the less useful message."""
        assert re.search(r"RAISE\s+EXCEPTION", _sql(), re.IGNORECASE)

    def test_report_names_the_columns_an_operator_needs_to_decide(self):
        """id, code, value_usd and the redemption count. Without the last one
        there is no way to tell which of the two has already paid out."""
        sql = _sql()
        for fragment in ("c.id", "c.code", "c.value_usd", "c.times_used", "c.is_active"):
            assert fragment in sql, f"pre-flight report omits {fragment}"
        assert re.search(
            r"FROM\s+public\.coupon_redemptions\s+r\s+WHERE\s+r\.coupon_id\s*=\s*c\.id",
            sql,
            re.IGNORECASE,
        ), "pre-flight must report how many redemptions each colliding row has"

    def test_tells_the_reader_not_to_repair_it_here(self):
        """The obvious response to a failing migration is to make it stop
        failing. The file has to argue against that at the point of temptation."""
        sql = _sql()
        assert "Do NOT" in sql and "DELETE" in sql


class TestNoAutomaticRemediation:
    """Property 2. Picking a surviving coupon is a product decision, and doing
    it here would do it silently, at deploy time, with no human in the loop."""

    def test_migration_never_deletes_or_rewrites_a_coupon(self):
        # Comments legitimately discuss DELETE; executable statements must not.
        executable = _executable()
        assert not re.search(
            r"\bDELETE\s+FROM\s+public\.coupons", executable, re.IGNORECASE
        ), "must not delete a colliding coupon"
        assert not re.search(
            r"\bUPDATE\s+public\.coupons\s+SET\s+code", executable, re.IGNORECASE
        ), "must not rename a colliding coupon"
        assert not re.search(
            r"\bTRUNCATE\b", executable, re.IGNORECASE
        ), "must not truncate anything"

    def test_migration_does_not_deactivate_a_colliding_coupon(self):
        """Flipping is_active is a quieter way of picking a winner, and it
        still decides which coupon a user can no longer redeem."""
        executable = _executable()
        assert not re.search(
            r"UPDATE\s+public\.coupons\s+SET\s+is_active", executable, re.IGNORECASE
        )


class TestRedeemCouponLookupIsDeterministic:
    """Property 3: the RPC must not be independently fragile.

    With the unique index in place at most one row can match, so the ordering is
    a no-op. It matters if the index is ever dropped, or -- the case that
    actually happens -- if a collision already exists when the migration runs,
    because then the index CANNOT be created and the ordering is the only thing
    left between one typed code and two grants to the same user.
    """

    def _redeem_body(self, path):
        return path.read_text(encoding="utf-8").split("$$")[1]

    def test_lookup_orders_and_limits(self):
        assert re.search(
            r"SELECT\s+\*\s+INTO\s+v_coupon\s+FROM\s+public\.coupons\s+"
            r"WHERE\s+UPPER\(code\)\s*=\s*UPPER\(v_code\)\s+"
            r"ORDER\s+BY\s+id\s+LIMIT\s+1\s+FOR\s+UPDATE",
            self._redeem_body(REDEEM_MIGRATION),
            re.IGNORECASE | re.DOTALL,
        ), "the coupon lookup must be ORDER BY id LIMIT 1 ... FOR UPDATE"

    def test_still_takes_the_row_lock(self):
        """Adding ORDER BY must not have cost us the lock."""
        assert re.search(r"FOR\s+UPDATE", self._redeem_body(REDEEM_MIGRATION), re.IGNORECASE)


class TestCorrectionIsActuallyApplied:
    """20260917010000 was applied to production by #2348 before its lookup was
    corrected. Supabase records applied migrations by version, so editing that
    file changes nothing on any database that already ran it -- verified against
    the CLI: the push reports "Remote database is up to date" and the function
    keeps the old body. The correction therefore has to arrive as its own
    migration, and these tests pin the two things that make it work.
    """

    def test_a_reapply_migration_exists(self):
        assert REAPPLY_MIGRATION.is_file(), REAPPLY_MIGRATION

    def test_it_carries_the_corrected_lookup(self):
        assert re.search(
            r"ORDER\s+BY\s+id\s+LIMIT\s+1\s+FOR\s+UPDATE",
            REAPPLY_MIGRATION.read_text(encoding="utf-8"),
            re.IGNORECASE | re.DOTALL,
        )

    def test_it_replaces_rather_than_creates(self):
        """CREATE OR REPLACE so it is a no-op on a fresh database whose
        20260917010000 already carried the fix."""
        sql = REAPPLY_MIGRATION.read_text(encoding="utf-8")
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.redeem_coupon", sql, re.IGNORECASE
        )

    def test_its_body_is_identical_to_the_original(self):
        """The reviewed definition and the applied one must not drift.

        A copy that is edited independently is two functions with one name, and
        the one people read would stop being the one that runs.
        """
        original = REDEEM_MIGRATION.read_text(encoding="utf-8").split("$$")[1]
        reapplied = REAPPLY_MIGRATION.read_text(encoding="utf-8").split("$$")[1]
        assert reapplied == original, "20260917015000's body has drifted from 20260917010000's"

    def test_it_keeps_the_permissions(self):
        """SECURITY DEFINER credit-granting function: CREATE OR REPLACE does not
        reset grants, but the file must not hand it to a PostgREST-reachable
        role either."""
        sql = REAPPLY_MIGRATION.read_text(encoding="utf-8")
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

    def test_it_sorts_before_the_index_migration(self):
        """The ordering invariant, and the reason this is not one migration.

        20260917020000 can fail -- its pre-flight aborts when a collision
        already exists -- and `supabase db push` commits one transaction PER
        migration. So the correction must land in an EARLIER file, or it rolls
        back alongside the index in exactly the case that needs it. Measured on
        that database state: correction first -> $50 paid once; correction
        inside the failing migration -> $55 across two redemption rows.
        """
        assert (
            REAPPLY_MIGRATION.name < MIGRATION.name
        ), f"{REAPPLY_MIGRATION.name} must sort before {MIGRATION.name}"

    def test_the_index_migration_does_not_redefine_the_function(self):
        """The regression guard for the above: folding the CREATE OR REPLACE
        back into the index migration is the tidy-looking edit that reopens the
        double grant."""
        executable = _executable()
        assert not re.search(
            r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION\s+public\.redeem_coupon",
            executable,
            re.IGNORECASE,
        ), (
            "20260917020000 must not (re)define redeem_coupon -- it can fail, and "
            "the correction would roll back with it"
        )
