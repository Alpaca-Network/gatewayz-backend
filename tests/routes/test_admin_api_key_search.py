"""Admin user search by API key after plaintext keys were removed.

New keys have only key_hash, encrypted_key and last4, so the old
``ilike("api_keys_new.api_key", ...)`` cannot find them. A 4-character query
now also matches ``last4``; a full key also matches ``key_hash``. The plaintext
ilike stays as a fallback for legacy rows.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.routes.admin import _api_key_search_filter
from src.security.deps import require_admin
from src.utils.crypto import sha256_key_hash

client = TestClient(app)

ADMIN = {"id": 1, "email": "admin@example.com", "role": "admin"}
FULL_KEY = "gw_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0-_x"
SALT = "test-salt-0123456789abcdef"


@pytest.fixture(autouse=True)
def _salt(monkeypatch):
    monkeypatch.setenv("KEY_HASH_SALT", SALT)


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[require_admin] = lambda: ADMIN
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


class TestSearchFilter:
    def test_four_characters_match_last4_or_legacy_plaintext(self):
        assert _api_key_search_filter("a1B2") == "last4.eq.a1B2,api_key.ilike.*a1B2*"

    def test_full_key_matches_hash_or_legacy_plaintext(self):
        expected_hash = sha256_key_hash(FULL_KEY)
        assert _api_key_search_filter(FULL_KEY) == (
            f"key_hash.eq.{expected_hash},api_key.ilike.*{FULL_KEY}*"
        )

    def test_full_key_is_trimmed(self):
        assert _api_key_search_filter(f"  {FULL_KEY}\n") == _api_key_search_filter(FULL_KEY)

    @pytest.mark.parametrize("query", ["gw_live", "abc", "abcde", "gw_live_short"])
    def test_other_queries_keep_the_legacy_ilike(self, query):
        assert _api_key_search_filter(query) is None

    @pytest.mark.parametrize("query", ["ab,c", "a.b)", "ab c", "a*b%", ""])
    def test_unsafe_characters_never_reach_an_or_filter(self, query):
        assert _api_key_search_filter(query) is None

    def test_missing_salt_falls_back_to_legacy(self, monkeypatch):
        monkeypatch.delenv("KEY_HASH_SALT", raising=False)
        assert _api_key_search_filter(FULL_KEY) is None


class _FakeQuery:
    def __init__(self, rec):
        self._rec = rec

    def select(self, *args, **kwargs):
        return self

    def ilike(self, column, pattern):
        self._rec["ilike"].append((column, pattern))
        return self

    def or_(self, filters, reference_table=None):
        self._rec["or"].append((filters, reference_table))
        return self

    def _noop(self, *args, **kwargs):
        return self

    eq = order = range = limit = _noop

    def execute(self):
        return MagicMock(data=[{"id": 7, "email": "a@example.com", "api_keys_new": []}], count=1)


class _FakeClient:
    def __init__(self, rec):
        self._rec = rec

    def table(self, name):
        return _FakeQuery(self._rec)


def _get(path):
    rec = {"ilike": [], "or": []}
    with patch("src.db.client.get_db", return_value=_FakeClient(rec)):
        response = client.get(path)
    return response, rec


@pytest.mark.parametrize("path", ["/admin/users", "/admin/users/stats"])
class TestRoutes:
    def test_last4_search_uses_or_filter_on_api_keys_new(self, path):
        response, rec = _get(f"{path}?api_key=a1B2")

        assert response.status_code == 200
        assert rec["or"], "4-character search did not use the last4 filter"
        assert all(f == ("last4.eq.a1B2,api_key.ilike.*a1B2*", "api_keys_new") for f in rec["or"])
        assert not [c for c, _ in rec["ilike"] if c.startswith("api_keys_new")]

    def test_full_key_search_uses_hash(self, path):
        response, rec = _get(f"{path}?api_key={FULL_KEY}")

        assert response.status_code == 200
        assert rec["or"]
        expected_hash = sha256_key_hash(FULL_KEY)
        for filters, table in rec["or"]:
            assert table == "api_keys_new"
            assert filters.startswith(f"key_hash.eq.{expected_hash},")

    def test_partial_search_keeps_legacy_ilike(self, path):
        response, rec = _get(f"{path}?api_key=gw_live")

        assert response.status_code == 200
        assert not rec["or"]
        assert ("api_keys_new.api_key", "%gw_live%") in rec["ilike"]
