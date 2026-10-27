"""The whole migration chain applies to an empty database (CI-only)."""

from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def test_every_migration_is_recorded(db):
    """`supabase db start` applied each file; a silently skipped one would be missing."""
    with db.cursor() as cur:
        cur.execute("SELECT version FROM supabase_migrations.schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
    on_disk = {p.name.split("_", 1)[0] for p in MIGRATIONS.glob("*.sql")}
    assert on_disk - applied == set()


def test_rls_is_on_for_every_public_table(db):
    """A table without RLS is readable by anon through PostgREST."""
    with db.cursor() as cur:
        cur.execute("""
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
            ORDER BY 1
            """)
        missing = [r[0] for r in cur.fetchall()]
    # Report, don't fail, on tables that predate this test: the list is the
    # baseline. New tables must not join it.
    baseline_path = Path(__file__).parent / "rls_baseline.txt"
    baseline = set(baseline_path.read_text().split()) if baseline_path.exists() else set()
    assert sorted(set(missing) - baseline) == []
