"""Live RLS lockdown check against the Supabase anon (PostgREST) endpoint.

The May-2026 emergency migrations enabled RLS + revoked anon/authenticated
grants on sensitive tables after the anon key was found to leak plaintext API
keys, emails and Stripe IDs. This test re-verifies that lockdown end-to-end so a
future migration cannot silently regress it.

It is SKIPPED unless both SUPABASE_URL and SUPABASE_ANON_KEY are set, so it never
breaks local/CI runs that don't have those configured. Wire it into a CI job
that provides the anon key to get continuous protection.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_PUBLIC_KEY")

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and SUPABASE_ANON_KEY),
    reason="Set SUPABASE_URL + SUPABASE_ANON_KEY to verify the RLS lockdown live",
)

SENSITIVE_TABLES = ["users", "payments", "api_keys_new", "credit_transactions", "rate_limit_usage"]


@pytest.mark.parametrize("table", SENSITIVE_TABLES)
def test_anon_key_cannot_read_sensitive_table(table):
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}?select=*&limit=1"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode() or "[]")
    except urllib.error.HTTPError as exc:
        # Denied outright (401/403) or hidden (404) are all acceptable outcomes.
        assert exc.code in (401, 403, 404), f"unexpected status {exc.code} reading {table}"
        return

    # A 200 is only acceptable if RLS default-deny returns zero rows.
    assert data == [], f"anon key leaked rows from {table}: {str(data)[:200]}"
