"""Live RLS lockdown check against the Supabase anon (PostgREST) endpoint.

The May-2026 emergency migrations enabled RLS + revoked anon/authenticated
grants on sensitive tables after the anon key was found to leak plaintext API
keys, emails and Stripe IDs. This test re-verifies that lockdown end-to-end so a
future migration cannot silently regress it.

It is SKIPPED unless both SUPABASE_URL and SUPABASE_ANON_KEY are set, so it never
breaks local/CI runs that don't have those configured. Wire it into a CI job
that provides the anon key to get continuous protection.

SUPABASE_ANON_KEY must be the project's publishable key (sb_publishable_...);
the legacy JWT anon key was disabled on 2026-11-27. A dead or wrong key gets
401 from the API gateway on every table, which the per-table checks below
would otherwise count as "denied". So the module first proves the key is live
against an endpoint any valid anon key can read, and SKIPS loudly (never
passes) when it is not.
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


def _anon_headers() -> dict:
    return {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"}


@pytest.fixture(scope="module", autouse=True)
def anon_key_is_live():
    """Prove the anon key is accepted before trusting any 401 below.

    GoTrue's /auth/v1/settings is readable with any valid anon/publishable key
    and does not depend on table grants, so a 200 here means the gateway knows
    the key. If it is rejected, every table check would "pass" on a 401 from
    the gateway, so skip the whole module with an explicit reason instead.
    """
    url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/settings"
    req = urllib.request.Request(url, headers=_anon_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        pytest.skip(
            f"SUPABASE_ANON_KEY was rejected (HTTP {exc.code} from /auth/v1/settings): "
            "the key is invalid or disabled. Set it to the project's sb_publishable_ "
            "key. RLS lockdown NOT verified."
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        pytest.skip(f"Could not reach Supabase to validate the anon key ({exc}); not verified.")
    assert status == 200, f"unexpected status {status} validating the anon key"


SENSITIVE_TABLES = [
    "users",
    "payments",
    "api_keys_new",
    "credit_transactions",
    "rate_limit_usage",
    "usage_records",
    "activity_log",
]


@pytest.mark.parametrize("table", SENSITIVE_TABLES)
def test_anon_key_cannot_read_sensitive_table(table):
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}?select=*&limit=1"
    req = urllib.request.Request(url, headers=_anon_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode() or "[]")
    except urllib.error.HTTPError as exc:
        # The key was proven live above, so 401/403 here is a real permission
        # denial (PostgREST answers 401 for anon), and 404 means hidden.
        assert exc.code in (401, 403, 404), f"unexpected status {exc.code} reading {table}"
        return

    # A 200 is only acceptable if RLS default-deny returns zero rows.
    assert data == [], f"anon key leaked rows from {table}: {str(data)[:200]}"
