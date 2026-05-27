#!/usr/bin/env python3
"""
Rotate all gw_live_* API keys on prod.

Context: a Supabase RLS misconfiguration (fixed in migrations 20260527000000/1/2)
exposed the entire `users` table — including plaintext api_key — to anyone
with the publishable anon key from approximately project creation through
2026-05-27. Every existing gw_live_* key must be considered compromised.

This script:
  1) Lists every active user with an existing api_key
  2) Generates a new api_key for each
  3) Writes the new key back into users.api_key
  4) (optional) Updates the matching api_keys_new row
  5) Outputs a CSV: user_id,email,old_key_prefix,new_key with secrets redacted

NOT RUN AUTOMATICALLY. Run after deciding the customer notification plan.
Service role key must be supplied via SUPABASE_SERVICE_KEY env var.
"""

import os
import secrets
import sys
import time
from typing import Any

from supabase import create_client

PROD_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
KEY = os.environ.get("SUPABASE_SERVICE_KEY")
if not KEY:
    print("ERROR: set SUPABASE_SERVICE_KEY env var to the prod service_role key", file=sys.stderr)
    sys.exit(1)
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in {"1", "true", "yes"}

client = create_client(PROD_URL, KEY)


def gen_key() -> str:
    """Match existing format: gw_live_<43 url-safe base64 chars>."""
    return "gw_live_" + secrets.token_urlsafe(32)


def main() -> None:
    print(f"=== API key rotation (DRY_RUN={DRY_RUN}) ===")
    print()

    page = 0
    page_size = 500
    rotated = 0
    skipped = 0
    print("user_id,email,old_key_prefix,new_key_prefix,status")

    while True:
        res = (
            client.table("users")
            .select("id, email, api_key, is_active")
            .order("id")
            .range(page * page_size, page * page_size + page_size - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        for u in rows:
            uid = u["id"]
            email = u.get("email") or ""
            old = u.get("api_key") or ""
            if not old.startswith("gw_live_"):
                skipped += 1
                continue
            new = gen_key()
            old_pref = old[:15]
            new_pref = new[:15]
            status = "DRY_RUN"
            if not DRY_RUN:
                try:
                    client.table("users").update({"api_key": new}).eq("id", uid).execute()
                    # Best-effort: keep api_keys_new in sync if present
                    try:
                        client.table("api_keys_new").update({"api_key": new}).eq(
                            "user_id", uid
                        ).eq("api_key", old).execute()
                    except Exception:
                        pass
                    status = "rotated"
                    rotated += 1
                except Exception as e:
                    status = f"ERROR:{str(e)[:60]}"
            print(f"{uid},{email},{old_pref},{new_pref},{status}")
        if len(rows) < page_size:
            break
        page += 1
        time.sleep(0.05)

    print()
    print(f"=== Done. rotated={rotated} skipped={skipped} ===")
    if DRY_RUN:
        print("DRY_RUN=true — no rows were written. Set DRY_RUN=false to apply.")


if __name__ == "__main__":
    main()
