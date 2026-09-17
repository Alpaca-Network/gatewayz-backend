"""Regression tests for GET /admin/users after users.credits was dropped.

`users.credits` was removed by migration
20260417000000_drop_legacy_credits_column.sql, but both select lists in
`get_all_users_info` still asked for it. PostgREST answers 42703 and fails the
whole query, so the admin panel's core Users page returned 500 for every
request. The balance now lives in `subscription_allowance` + `purchased_credits`
and `credits` is synthesized in the response, so the panel's contract is
unchanged.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.routes.admin import _with_synthesized_credits
from src.security.deps import require_admin

client = TestClient(app)

ADMIN = {"id": 1, "email": "admin@example.com", "role": "admin"}

# Columns dropped from / never present on public.users. Selecting any of them
# fails the entire query.
PHANTOM_COLUMNS = ("credits", "last_login", "api_usage_count", "role_metadata")


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def admin_override():
    app.dependency_overrides[require_admin] = lambda: ADMIN
    return ADMIN


def _columns(select: str) -> set[str]:
    """Top-level column names in a PostgREST select list.

    Split on commas rather than substring-matching: `purchased_credits`
    contains "credits", so a naive `in` check can never tell the real column
    from the dropped one.
    """
    names = set()
    depth = 0
    current = ""
    for ch in select + ",":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            name = current.strip()
            if name and "(" not in name:
                names.add(name)
            current = ""
        else:
            current += ch
    return names


def _row(**overrides):
    row = {
        "id": 7,
        "username": "alice",
        "email": "alice@example.com",
        "subscription_allowance": 2.5,
        "purchased_credits": 10.0,
        "is_active": True,
        "role": "user",
        "created_at": "2026-09-01T00:00:00Z",
    }
    row.update(overrides)
    return row


class _FakeQuery:
    """Records the select list and every filter applied to it."""

    def __init__(self, recorder, rows, count):
        self._recorder = recorder
        self._rows = rows
        self._count = count

    def select(self, *args, **kwargs):
        self._recorder["selects"].append(", ".join(a for a in args if isinstance(a, str)))
        return self

    def _filter(self, column, *_args, **_kwargs):
        self._recorder["filters"].append(column)
        return self

    ilike = eq = gt = gte = lt = lte = in_ = order = _filter

    def range(self, *_args):
        return self

    def execute(self):
        return MagicMock(data=self._rows, count=self._count)


class _FakeClient:
    def __init__(self, recorder, rows, count, rpc_error=None, rpc_rows=None):
        self._recorder = recorder
        self._rows = rows
        self._count = count
        self._rpc_error = rpc_error
        self._rpc_rows = rpc_rows

    def table(self, name):
        self._recorder["tables"].append(name)
        return _FakeQuery(self._recorder, self._rows, self._count)

    def rpc(self, name, params):
        self._recorder["rpc"].append((name, params))
        if self._rpc_error is not None:
            raise self._rpc_error
        return _FakeQuery(self._recorder, self._rpc_rows, None)


def _recorder():
    return {"selects": [], "filters": [], "tables": [], "rpc": []}


def _patched_client(rec, **kwargs):
    return patch(
        "src.db.client.get_db",
        return_value=_FakeClient(
            rec, kwargs.pop("rows", [_row()]), kwargs.pop("count", 1), **kwargs
        ),
    )


class TestSynthesizedCreditsHelper:
    def test_sums_the_two_component_columns(self):
        assert (
            _with_synthesized_credits({"subscription_allowance": 2.5, "purchased_credits": 10})[
                "credits"
            ]
            == 12.5
        )

    def test_treats_missing_and_null_components_as_zero(self):
        assert _with_synthesized_credits({"subscription_allowance": None})["credits"] == 0.0
        assert _with_synthesized_credits({})["credits"] == 0.0

    def test_keeps_the_component_fields(self):
        row = _with_synthesized_credits({"subscription_allowance": 1, "purchased_credits": 2})
        assert row["subscription_allowance"] == 1
        assert row["purchased_credits"] == 2


class TestStandardQueryPath:
    def test_does_not_select_any_dropped_column(self, admin_override):
        rec = _recorder()
        with _patched_client(rec):
            response = client.get("/admin/users?limit=10")

        assert response.status_code == 200
        assert rec["selects"], "no select was issued"
        for select in rec["selects"]:
            bad = _columns(select) & set(PHANTOM_COLUMNS)
            assert not bad, f"select still asks for users.{sorted(bad)}: {select}"

    def test_selects_the_real_balance_columns(self, admin_override):
        rec = _recorder()
        with _patched_client(rec):
            client.get("/admin/users?limit=10")

        data_selects = [s for s in rec["selects"] if "username" in s]
        assert data_selects
        for select in data_selects:
            assert "subscription_allowance" in select
            assert "purchased_credits" in select

    def test_api_key_join_path_also_avoids_dropped_columns(self, admin_override):
        rec = _recorder()
        with _patched_client(rec):
            response = client.get("/admin/users?api_key=gw_live")

        assert response.status_code == 200
        joined = [s for s in rec["selects"] if "api_keys_new" in s and "username" in s]
        assert joined, "api_key filter did not take the join path"
        for select in joined:
            assert "credits" not in _columns(select)
            assert {"subscription_allowance", "purchased_credits"} <= _columns(select)

    def test_response_still_exposes_credits(self, admin_override):
        rec = _recorder()
        with _patched_client(rec, rows=[_row()]):
            response = client.get("/admin/users?limit=10")

        user = response.json()["users"][0]
        assert user["credits"] == 12.5
        assert user["subscription_allowance"] == 2.5
        assert user["purchased_credits"] == 10.0


class TestEmailSearchRpcPath:
    def test_uses_the_rpc_and_synthesizes_credits(self, admin_override):
        rec = _recorder()
        rpc_rows = [
            {
                "id": 7,
                "email": "alice@example.com",
                "subscription_allowance": 1.0,
                "purchased_credits": 4.0,
                "total_count": 1,
            }
        ]
        with _patched_client(rec, rpc_rows=rpc_rows):
            response = client.get("/admin/users?email=alice")

        assert response.status_code == 200
        assert rec["rpc"][0][0] == "search_users_by_email"
        body = response.json()
        assert body["total_users"] == 1
        assert body["users"][0]["credits"] == 5.0
        assert "total_count" not in body["users"][0]

    def test_falls_back_to_the_standard_query_when_the_rpc_fails(self, admin_override):
        """A broken or missing function must not 500 the whole page."""
        rec = _recorder()
        with _patched_client(rec, rpc_error=RuntimeError("42703 column users.credits")):
            response = client.get("/admin/users?email=alice")

        assert response.status_code == 200, response.text
        assert rec["rpc"], "the RPC was never attempted"
        assert "users" in rec["tables"], "no fallback query was issued"
        assert "email" in rec["filters"], "the fallback dropped the email filter"
        assert response.json()["users"][0]["credits"] == 12.5
