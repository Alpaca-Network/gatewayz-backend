#!/usr/bin/env python3
"""
Migrate the old admin-panel `admin_users` table into `users.role` (Phase A6,
docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md §3).

Why: the admin panel used to own its own `admin_users` table (email, role,
status) as a second, disconnected identity system. Phase A makes `users.role`
the single RBAC source. This is the one-off script that reconciles the two:
for every `admin_users` row, find the matching `users` row by email
(case-insensitive) and set its role; for staff with no matching Gatewayz
account yet, report it (and optionally create an `admin_invites` row so they
can claim staff access on their first sign-in, per src/routes/admin_staff.py).

Role mapping:
    admin_users.role == 'superadmin' -> users.role = 'superadmin'
    admin_users.role == 'admin'      -> users.role = 'admin'
    admin_users.role == 'dev'        -> left at 'user' (developer access is a
                                         Gatewayz-side concept, not staff --
                                         printed, not silently dropped)
    anything else                    -> printed as unrecognized, skipped

`admin_users` is left untouched (read-only) -- the spec keeps it around for
30 days before it's dropped.

Usage:
    python3 scripts/migrate_admin_users.py                    # dry run (default)
    python3 scripts/migrate_admin_users.py --apply             # actually write users.role
    python3 scripts/migrate_admin_users.py --apply --create-invites  # + create admin_invites
                                                                       # for unmatched emails
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field

from src.config.supabase_config import get_supabase_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_admin_users")

# admin_users.role -> users.role. 'dev' has no staff equivalent: it stays
# 'user' but is reported explicitly rather than silently no-op'd.
_ROLE_MAP = {
    "superadmin": "superadmin",
    "admin": "admin",
}
_NO_STAFF_ROLE = "dev"


@dataclass
class MigrationReport:
    matched: list[dict] = field(default_factory=list)  # {email, admin_role, new_role, user_id}
    unmatched: list[dict] = field(default_factory=list)  # {email, admin_role}
    dev_role_skipped: list[dict] = field(default_factory=list)  # {email}
    unrecognized_role: list[dict] = field(default_factory=list)  # {email, admin_role}
    invites_created: list[dict] = field(default_factory=list)  # {email, role}


def _fetch_admin_users(client) -> list[dict]:
    result = client.table("admin_users").select("email, role, status").execute()
    return result.data or []


def _fetch_users_by_email(client) -> dict[str, dict]:
    """email (lowercased) -> users row, for every user. Loaded once so the
    match is a local dict lookup, not one query per admin_users row."""
    result = client.table("users").select("id, email, role").execute()
    by_email = {}
    for row in result.data or []:
        email = (row.get("email") or "").strip().lower()
        if email:
            by_email[email] = row
    return by_email


def build_report(client) -> MigrationReport:
    report = MigrationReport()
    admin_rows = _fetch_admin_users(client)
    users_by_email = _fetch_users_by_email(client)

    for row in admin_rows:
        email = (row.get("email") or "").strip().lower()
        admin_role = row.get("role")

        if not email:
            continue

        user = users_by_email.get(email)

        if admin_role == _NO_STAFF_ROLE:
            report.dev_role_skipped.append({"email": email})
            continue

        new_role = _ROLE_MAP.get(admin_role)
        if new_role is None:
            report.unrecognized_role.append({"email": email, "admin_role": admin_role})
            continue

        if user is None:
            report.unmatched.append({"email": email, "admin_role": admin_role})
        else:
            report.matched.append(
                {
                    "email": email,
                    "admin_role": admin_role,
                    "new_role": new_role,
                    "user_id": user["id"],
                    "current_role": user.get("role"),
                }
            )

    return report


def apply_matches(client, report: MigrationReport) -> int:
    """Set users.role for every matched row whose role actually needs to
    change. Returns the number of rows updated."""
    updated = 0
    for match in report.matched:
        if match["current_role"] == match["new_role"]:
            continue
        try:
            client.table("users").update({"role": match["new_role"]}).eq(
                "id", match["user_id"]
            ).execute()
            updated += 1
        except Exception as e:
            logger.error("Failed to set role for user_id=%s: %s", match["user_id"], e)
    return updated


def create_invites_for_unmatched(client, report: MigrationReport) -> None:
    """Create a pending admin_invites row for every unmatched admin_users
    email, so they can claim staff access via POST /auth/accept-invite on
    their first Gatewayz sign-in (src/db/staff.py::create_invite)."""
    # Imported lazily so a plain report-only run never needs src.db.staff's
    # dependency chain (and so a stray import error there doesn't break
    # `--dry-run`, which is the default and safest path).
    from src.db.staff import create_invite

    for entry in report.unmatched:
        row, _raw_token = create_invite(entry["email"], entry["admin_role"], invited_by=None)
        if row is not None:
            report.invites_created.append({"email": entry["email"], "role": entry["admin_role"]})
        else:
            logger.error("Failed to create invite for %s", entry["email"])


def _print_table(rows: list[dict], columns: list[str], title: str) -> None:
    print(f"\n{title} ({len(rows)})")
    if not rows:
        print("  (none)")
        return
    for row in rows:
        line = "  " + "  ".join(f"{col}={row.get(col)}" for col in columns)
        print(line)


def print_report(report: MigrationReport) -> None:
    _print_table(
        report.matched,
        ["email", "admin_role", "current_role", "new_role", "user_id"],
        "Matched (role will be set)",
    )
    _print_table(
        report.unmatched,
        ["email", "admin_role"],
        "Unmatched -- no Gatewayz account; invite needed",
    )
    _print_table(
        report.dev_role_skipped,
        ["email"],
        "Skipped ('dev' role has no staff equivalent, left as 'user')",
    )
    _print_table(
        report.unrecognized_role,
        ["email", "admin_role"],
        "Skipped (unrecognized admin_users.role)",
    )
    if report.invites_created:
        _print_table(report.invites_created, ["email", "role"], "Invites created")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write users.role changes (default is a dry run: report only)",
    )
    ap.add_argument(
        "--create-invites",
        action="store_true",
        help="create admin_invites rows for unmatched emails (requires --apply)",
    )
    args = ap.parse_args()

    if args.create_invites and not args.apply:
        ap.error("--create-invites requires --apply")

    client = get_supabase_client()

    try:
        report = build_report(client)
    except Exception as e:
        logger.error("Failed to read admin_users/users: %s", e)
        return 1

    if not args.apply:
        print("DRY RUN -- no changes written. Pass --apply to write users.role changes.")
        print_report(report)
        return 0

    updated = apply_matches(client, report)
    if args.create_invites:
        create_invites_for_unmatched(client, report)

    print_report(report)
    print(f"\nApplied: {updated} users.role update(s).")
    if args.create_invites:
        print(f"Created: {len(report.invites_created)} admin_invites row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
