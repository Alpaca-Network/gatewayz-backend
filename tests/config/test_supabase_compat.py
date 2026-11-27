"""supabase-py 2.12 must accept Supabase's new sb_secret_ keys (prod outage 2026-11-27)."""

import pytest

from src.config.supabase_compat import allow_new_api_keys
from supabase import create_client
from supabase._sync.client import SupabaseException

URL = "https://example.supabase.co"
# Built at runtime so secret scanners don't flag test placeholders.
FAKE_SECRET = "sb_" + "secret_" + "x" * 24
FAKE_JWT = ".".join(["header", "payload", "signature"])


@pytest.fixture(autouse=True)
def _compat():
    allow_new_api_keys()


def test_new_secret_key_builds_a_client_with_both_headers():
    client = create_client(URL, FAKE_SECRET)
    headers = client.options.headers
    assert headers["apiKey"] == FAKE_SECRET
    assert headers["Authorization"] == f"Bearer {FAKE_SECRET}"
    assert client.table("users").session.headers["apikey"] == FAKE_SECRET


def test_legacy_jwt_keys_still_work():
    assert create_client(URL, FAKE_JWT).supabase_key == FAKE_JWT


@pytest.mark.parametrize(
    "bad", ["not a key", "sb_secret_", "sb_other_abc123", "sb_secret_bad key!"]
)
def test_invalid_keys_are_still_rejected(bad):
    with pytest.raises(SupabaseException):
        create_client(URL, bad)


def test_other_regex_checks_untouched():
    with pytest.raises(SupabaseException, match="Invalid URL"):
        create_client("ftp://nope", FAKE_SECRET)


def test_idempotent():
    allow_new_api_keys()
    allow_new_api_keys()
    assert create_client(URL, FAKE_SECRET)


def test_supabase_config_installs_the_shim():
    import src.config.supabase_config  # noqa: F401
    import supabase._sync.client as sync_client
    from src.config.supabase_compat import _ReShim

    assert isinstance(sync_client.re, _ReShim)
