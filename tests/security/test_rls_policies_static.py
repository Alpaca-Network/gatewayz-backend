"""Static RLS-footgun check over the migration history (gatewayz-backend#2258,
threat model docs/security/ANONYMITY_THREAT_MODEL.md §5, L9/L10).

The 2026-05-27 incident (20260527000000_emergency_rls_lockdown.sql) happened
because several tables had default Supabase grants (anon/authenticated can
read+write) and either no RLS or an always-true policy. This test scans
supabase/migrations/*.sql (+ supabase/staged-migrations/*.sql) in execution
order -- no SQL parser, just regex over statements -- and asserts, for every
RLS-sensitive table named in the threat model's §5 inventory:

  1. Any CREATE POLICY that hands `anon`/`authenticated`/`public` an
     always-true USING or WITH CHECK clause is followed, later in migration
     order, by a matching DROP POLICY -- either a literal
     `DROP POLICY ... ON <table>` for that exact policy name, or the
     dynamic pg_policies-driven sweep in
     20260527000002_final_security_hardening.sql (loops over
     `tablename IN (...)` where `qual = 'true' OR qual IS NULL OR
     with_check = 'true'` and DROPs each match).
  2. The table has an explicit `REVOKE ALL ... FROM anon, authenticated`
     somewhere in the history -- literal, or via the dynamic table-array
     REVOKE loop in 20260527000001_full_security_hardening.sql -- OR it was
     never granted an anon/authenticated policy at all ("RLS-enabled,
     no-policy" -- default-deny by omission, the same posture usage_records
     had before this PR and user_wallets/wallet_stakes/faucet_claims have
     today).
  3. Its owned `<table>_id_seq` sequence, if ever GRANTed to anon/
     authenticated, has a later `REVOKE ALL ON SEQUENCE ... FROM anon,
     authenticated` -- literal, or via the dynamic sequence-array REVOKE
     loop added in 20260903100000_usage_records_hardening.sql. Table-level
     REVOKE does not touch sequence-level grants, so this is a separate
     check (found in fix round 1 of PR review: usage_records/activity_log/
     api_keys_new/credit_transactions/users/payments' owned sequences still
     carried their base-schema-dump GRANT ALL to anon/authenticated even
     after rule 2 passed -- users/payments' *tables* were already locked
     down in 20260527000000, but not their sequences).

This intentionally does not try to be a general policy analyzer: it exists
to catch the *shape* of the 2026-05-27 incident recurring on these specific
tables, so a future migration can't silently reopen one of them.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "supabase", "migrations")
STAGED_DIR = os.path.join(REPO_ROOT, "supabase", "staged-migrations")

# The threat model's §5 RLS-sensitive table inventory.
TARGET_TABLES = frozenset(
    {
        "usage_records",
        "chat_completion_requests",
        "credit_transactions",
        "activity_log",
        "users",
        "api_keys_new",
        "payments",
        "user_wallets",
        "wallet_stakes",
        "faucet_claims",
    }
)

PUBLIC_ROLES = frozenset({"anon", "authenticated", "public"})


def _migration_files() -> list[str]:
    """All migration SQL files in execution order: supabase/migrations/ (chronological
    by timestamp-prefixed filename) then supabase/staged-migrations/ (human-gated,
    applied after -- see that folder's README)."""
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
    files += sorted(glob.glob(os.path.join(STAGED_DIR, "*.sql")))
    assert files, f"no migration files found under {MIGRATIONS_DIR}"
    return files


@dataclass
class PolicyCreate:
    file: str
    order: tuple  # (file_index, char_offset) -- sortable, defines migration order
    name: str
    table: str
    roles: frozenset
    always_true: bool


@dataclass
class PolicyDrop:
    file: str
    order: tuple
    name: str
    table: str


@dataclass
class DynamicBlock:
    """A DO $...$ block that loops over a hardcoded table list to REVOKE and/or
    DROP POLICY dynamically (the 20260527000001/000002 pattern)."""

    file: str
    order: tuple
    tables: frozenset
    drops_policies: bool
    revokes: bool


@dataclass
class SequenceGrant:
    file: str
    order: tuple
    sequence: str
    role: str


@dataclass
class SequenceRevoke:
    order: tuple
    sequence: str


@dataclass
class DynamicSequenceRevokeBlock:
    order: tuple
    sequences: frozenset


@dataclass
class Timeline:
    creates: list = field(default_factory=list)
    drops: list = field(default_factory=list)
    revokes: list = field(default_factory=list)  # (order, table)
    dynamic_blocks: list = field(default_factory=list)
    sequence_grants: list = field(default_factory=list)
    sequence_revokes: list = field(default_factory=list)
    dynamic_sequence_revoke_blocks: list = field(default_factory=list)


# Matches both quoted ("public"."table") and unquoted (public.table) forms.
_TABLE_REF = r'"?public"?\.\s*"?(?P<table>\w+)"?'

_CREATE_POLICY_RE = re.compile(
    r'CREATE\s+POLICY\s+"?(?P<name>[^"\n]+?)"?\s+' r"ON\s+" + _TABLE_REF + r"\s+" r"(?P<body>.*?);",
    re.IGNORECASE | re.DOTALL,
)

_DROP_POLICY_RE = re.compile(
    r'DROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?"?(?P<name>[^"\n]+?)"?\s+' r"ON\s+" + _TABLE_REF + r"\s*;",
    re.IGNORECASE,
)

_REVOKE_RE = re.compile(
    r"REVOKE\s+ALL\s+ON\s+" + _TABLE_REF + r"\s+"
    r"FROM\s+(?:.*?anon.*?authenticated|.*?authenticated.*?anon)",
    re.IGNORECASE,
)

# `DO $tag$ ... $tag$;` blocks -- the vehicle for both dynamic-table-list
# REVOKE loops (000001) and the pg_policies-driven DROP POLICY sweep (000002).
_DO_BLOCK_RE = re.compile(r"DO\s+\$(?P<tag>\w*)\$(?P<body>.*?)\$(?P=tag)\$\s*;", re.DOTALL)

# A hardcoded table-name list, either `tables text[] := ARRAY[...]` or a
# `tablename IN (...)` predicate -- both list single-quoted table names.
_TABLE_LIST_RE = re.compile(
    r"(?:ARRAY\s*\[|tablename\s+IN\s*\()(?P<list>[^\])]*)[\])]", re.IGNORECASE | re.DOTALL
)
_QUOTED_NAME_RE = re.compile(r"'(\w+)'")

# Sequence grant/revoke -- separate from table grants: REVOKE ALL ON a table
# does not touch grants on its owned sequence (fix round 1, PR review).
_SEQ_REF = r'"?public"?\.\s*"?(?P<sequence>\w+)"?'

_GRANT_SEQUENCE_RE = re.compile(
    r"GRANT\s+[\w,\s]+?\s+ON\s+SEQUENCE\s+" + _SEQ_REF + r'\s+TO\s+"?(?P<role>\w+)"?',
    re.IGNORECASE,
)

_REVOKE_SEQUENCE_RE = re.compile(
    r"REVOKE\s+ALL\s+ON\s+SEQUENCE\s+" + _SEQ_REF + r"\s+"
    r"FROM\s+(?:.*?anon.*?authenticated|.*?authenticated.*?anon)",
    re.IGNORECASE,
)

# The dynamic sequence-array REVOKE loop pattern (20260903100000, section 5):
# `EXECUTE format('REVOKE ALL ON SEQUENCE public.%I FROM anon, authenticated', s)`.
_DYNAMIC_SEQUENCE_REVOKE_RE = re.compile(
    r"REVOKE\s+ALL\s+ON\s+SEQUENCE\s+public\.%I", re.IGNORECASE
)


def _roles_from_body(body: str) -> frozenset:
    m = re.search(
        r"\bTO\s+(?P<roles>[\w\s,\"]+?)(?:\s+USING|\s+WITH\s+CHECK|$)", body, re.IGNORECASE
    )
    if not m:
        # No TO clause = policy applies to PUBLIC (Postgres default).
        return frozenset({"public"})
    roles = re.findall(r"\w+", m.group("roles"))
    return frozenset(r.lower() for r in roles)


def _is_always_true(body: str) -> bool:
    using_true = re.search(r"USING\s*\(\s*true\s*\)", body, re.IGNORECASE)
    check_true = re.search(r"WITH\s+CHECK\s*\(\s*true\s*\)", body, re.IGNORECASE)
    return bool(using_true or check_true)


def _parse_timeline() -> Timeline:
    tl = Timeline()
    for file_idx, path in enumerate(_migration_files()):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        fname = os.path.basename(path)

        for m in _CREATE_POLICY_RE.finditer(text):
            body = m.group("body")
            tl.creates.append(
                PolicyCreate(
                    file=fname,
                    order=(file_idx, m.start()),
                    name=m.group("name").strip(),
                    table=m.group("table"),
                    roles=_roles_from_body(body),
                    always_true=_is_always_true(body),
                )
            )

        for m in _DROP_POLICY_RE.finditer(text):
            tl.drops.append(
                PolicyDrop(
                    file=fname,
                    order=(file_idx, m.start()),
                    name=m.group("name").strip(),
                    table=m.group("table"),
                )
            )

        for m in _REVOKE_RE.finditer(text):
            tl.revokes.append(((file_idx, m.start()), m.group("table")))

        for m in _GRANT_SEQUENCE_RE.finditer(text):
            role = m.group("role").lower()
            if role in PUBLIC_ROLES:
                tl.sequence_grants.append(
                    SequenceGrant(
                        file=fname,
                        order=(file_idx, m.start()),
                        sequence=m.group("sequence"),
                        role=role,
                    )
                )

        for m in _REVOKE_SEQUENCE_RE.finditer(text):
            tl.sequence_revokes.append(
                SequenceRevoke(order=(file_idx, m.start()), sequence=m.group("sequence"))
            )

        for m in _DO_BLOCK_RE.finditer(text):
            block_body = m.group("body")
            list_match = _TABLE_LIST_RE.search(block_body)
            if not list_match:
                continue
            names = frozenset(_QUOTED_NAME_RE.findall(list_match.group("list")))
            if not names:
                continue
            drops_policies = bool(re.search(r"DROP\s+POLICY", block_body, re.IGNORECASE))
            revokes = bool(re.search(r"REVOKE\s+ALL\s+ON\s+public\.%I", block_body, re.IGNORECASE))
            if drops_policies or revokes:
                tl.dynamic_blocks.append(
                    DynamicBlock(
                        file=fname,
                        order=(file_idx, m.start()),
                        tables=names,
                        drops_policies=drops_policies,
                        revokes=revokes,
                    )
                )

            if _DYNAMIC_SEQUENCE_REVOKE_RE.search(block_body):
                tl.dynamic_sequence_revoke_blocks.append(
                    DynamicSequenceRevokeBlock(order=(file_idx, m.start()), sequences=names)
                )

    return tl


TIMELINE = _parse_timeline()


def _always_true_public_creates_for(table: str) -> list[PolicyCreate]:
    return [
        c
        for c in TIMELINE.creates
        if c.table == table and c.always_true and (c.roles & PUBLIC_ROLES)
    ]


def _later_drop_exists(create: PolicyCreate) -> bool:
    for drop in TIMELINE.drops:
        if drop.table == create.table and drop.name == create.name and drop.order > create.order:
            return True
    for block in TIMELINE.dynamic_blocks:
        if block.drops_policies and create.table in block.tables and block.order > create.order:
            return True
    return False


def _table_is_revoked(table: str) -> bool:
    if any(t == table for _order, t in TIMELINE.revokes):
        return True
    return any(b.revokes and table in b.tables for b in TIMELINE.dynamic_blocks)


def _later_sequence_revoke_exists(grant: SequenceGrant) -> bool:
    for revoke in TIMELINE.sequence_revokes:
        if revoke.sequence == grant.sequence and revoke.order > grant.order:
            return True
    for block in TIMELINE.dynamic_sequence_revoke_blocks:
        if grant.sequence in block.sequences and block.order > grant.order:
            return True
    return False


def test_migration_files_found():
    """Sanity check the scan actually parsed something -- an empty result would
    make every assertion below vacuously pass."""
    assert TIMELINE.creates, "parsed zero CREATE POLICY statements; regex likely broken"
    assert TIMELINE.revokes or TIMELINE.dynamic_blocks
    assert (
        TIMELINE.sequence_grants
    ), "parsed zero GRANT ... ON SEQUENCE statements; regex likely broken"


class TestNoLiveAlwaysTruePolicy:
    """Rule 1 (L10): every always-true policy ever granted to anon/authenticated/
    public on a target table must have a later DROP (literal or dynamic sweep)."""

    def test_every_always_true_target_table_policy_is_dropped(self):
        offenders = []
        for table in sorted(TARGET_TABLES):
            for create in _always_true_public_creates_for(table):
                if not _later_drop_exists(create):
                    offenders.append(
                        f"{create.table}.{create.name!r} (roles={sorted(create.roles)}, "
                        f"{create.file}) has no later DROP POLICY"
                    )
        assert not offenders, "live always-true policy with no later drop:\n" + "\n".join(offenders)


class TestGrantsLockedDown:
    """Rule 2 (L9): every target table either has an explicit REVOKE from
    anon/authenticated, or was never granted an anon/authenticated policy
    at all (RLS-enabled-no-policy default-deny)."""

    def test_every_target_table_is_revoked_or_never_granted_a_policy(self):
        offenders = []
        for table in sorted(TARGET_TABLES):
            revoked = _table_is_revoked(table)
            ever_granted_policy = bool(_always_true_public_creates_for(table)) or any(
                c.table == table and (c.roles & PUBLIC_ROLES) for c in TIMELINE.creates
            )
            if not revoked and ever_granted_policy:
                offenders.append(table)
        assert not offenders, (
            "tables with an anon/authenticated policy but no REVOKE ALL: " f"{offenders}"
        )


class TestSequenceGrantsLockedDown:
    """Rule 3 (fix round 1): a target table's owned <table>_id_seq sequence,
    if ever GRANTed to anon/authenticated, must have a later REVOKE ALL ON
    SEQUENCE. Table-level REVOKE does not imply sequence-level REVOKE."""

    def test_every_granted_sequence_has_a_later_revoke(self):
        offenders = []
        target_seqs = {f"{table}_id_seq" for table in TARGET_TABLES}
        for grant in TIMELINE.sequence_grants:
            if grant.sequence not in target_seqs:
                continue
            if not _later_sequence_revoke_exists(grant):
                offenders.append(
                    f"{grant.sequence} granted to {grant.role} ({grant.file}) has no later "
                    "REVOKE ALL ON SEQUENCE"
                )
        assert not offenders, "granted sequence with no later revoke:\n" + "\n".join(offenders)


class TestKnownIncidentFixturesStillParse:
    """Pins the scanner to the two incidents this test exists for, so a change
    to the regex logic itself can't silently stop detecting them."""

    def test_usage_records_hardening_migration_present(self):
        assert _table_is_revoked("usage_records"), (
            "usage_records must have a REVOKE ALL ... FROM anon, authenticated "
            "(supabase/migrations/20260903100000_usage_records_hardening.sql)"
        )

    def test_chat_completion_requests_stub_policy_was_dropped(self):
        stub_creates = [
            c
            for c in TIMELINE.creates
            if c.table == "chat_completion_requests"
            and c.name == "Allow users to read their own chat completion requests"
        ]
        assert stub_creates, "expected the known stub policy to still be parseable"
        assert _later_drop_exists(stub_creates[0])

    def test_usage_records_sequence_grant_was_revoked(self):
        grants = [g for g in TIMELINE.sequence_grants if g.sequence == "usage_records_id_seq"]
        assert grants, "expected the known base-schema sequence grant to still be parseable"
        assert all(_later_sequence_revoke_exists(g) for g in grants)


class TestUsageRecordsHardeningMigrationContent:
    """Content checks for the migration this ticket adds (gatewayz-backend#2258)."""

    MIGRATION_PATH = os.path.join(MIGRATIONS_DIR, "20260903100000_usage_records_hardening.sql")

    @property
    def _sql(self) -> str:
        with open(self.MIGRATION_PATH, encoding="utf-8") as fh:
            return fh.read()

    def test_migration_file_exists_and_parses_as_statements(self):
        sql = self._sql
        statements = [
            s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")
        ]
        assert len(statements) >= 5, "expected multiple distinct statements in the migration"

    def test_contains_revoke_grant_deny_policy_and_drop_policy(self):
        sql = self._sql
        assert re.search(
            r"REVOKE\s+ALL\s+ON\s+public\.usage_records\s+FROM\s+anon,\s*authenticated",
            sql,
            re.IGNORECASE,
        )
        assert re.search(
            r"GRANT\s+ALL\s+ON\s+public\.usage_records\s+TO\s+service_role", sql, re.IGNORECASE
        )
        assert re.search(
            r"CREATE\s+POLICY\s+usage_records_service_only.*?USING\s*\(\s*false\s*\).*?WITH\s+CHECK\s*\(\s*false\s*\)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        assert re.search(
            r"DROP\s+POLICY\s+IF\s+EXISTS\s+usage_records_service_only", sql, re.IGNORECASE
        )

    def test_contains_sequence_revokes_for_all_four_owned_sequences(self):
        sql = self._sql
        for seq in (
            "usage_records_id_seq",
            "activity_log_id_seq",
            "api_keys_new_id_seq",
            "credit_transactions_id_seq",
        ):
            assert f"'{seq}'" in sql, f"expected {seq} in the dynamic sequence-revoke array"
        assert re.search(r"REVOKE\s+ALL\s+ON\s+SEQUENCE\s+public\.%I", sql, re.IGNORECASE)
        assert re.search(r"to_regclass\('public\.'\s*\|\|\s*s\)", sql, re.IGNORECASE)

    def test_references_exact_stub_policy_name(self):
        sql = self._sql
        assert '"Allow users to read their own chat completion requests"' in sql
        assert re.search(
            r'DROP\s+POLICY\s+IF\s+EXISTS\s+"Allow users to read their own chat completion requests"\s+'
            r"ON\s+public\.chat_completion_requests",
            sql,
            re.IGNORECASE,
        )
