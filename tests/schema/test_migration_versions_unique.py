"""Supabase keys applied migrations by the numeric version prefix only.

Two files sharing a version means `supabase db push` treats one as already
applied and fails with a schema_migrations_pkey duplicate (the ETH payouts
migration collided with the GLM alias one on 20260922120000 and never
reached prod).
"""

from collections import defaultdict
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def test_migration_versions_are_unique():
    by_version = defaultdict(list)
    for f in MIGRATIONS.glob("*.sql"):
        by_version[f.name.split("_", 1)[0]].append(f.name)
    dupes = {v: sorted(names) for v, names in by_version.items() if len(names) > 1}
    assert not dupes, f"duplicate migration versions: {dupes}"
