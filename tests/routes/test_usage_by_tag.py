"""GET /v1/usage — the read half of request-tag passthrough.

Without a read path the tag is write-only: a caller sets `x-gatewayz-tag` and
has no way to confirm the gateway recorded it. That makes the feature
unfalsifiable from outside, which is the failure this estate spent a week on in
three other forms.

The distinctions under test are all about not letting a dashboard lie:

  - "the rollup did not run" must not render as "this tag spent nothing"
  - failures are reported separately, never folded into the totals
  - untagged traffic is absent, not bucketed under an invented tag
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routes import api_keys as api_keys_route

ROWS = [
    {
        "tag": "init/orbital-refi-q3",
        "calls": 128,
        "failed": 4,
        "input_tokens": 1840355,
        "output_tokens": 96210,
        "cost_usd": 41.2,
        "first_at": "2026-09-01T00:00:00Z",
        "last_at": "2026-09-14T18:00:00Z",
    }
]


def _client():
    app = FastAPI()
    app.include_router(api_keys_route.router)
    app.dependency_overrides[api_keys_route.get_api_key] = lambda: "gw_test"
    return TestClient(app)


@pytest.fixture(autouse=True)
def _user():
    with patch.object(api_keys_route, "get_user", return_value={"id": 1}):
        yield


def test_returns_the_rollup():
    with patch.object(api_keys_route, "get_usage_by_tag", return_value=ROWS):
        r = _client().get("/v1/usage?tag=init/orbital-refi-q3")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == ROWS
    assert body["measured"] is True


def test_failures_are_reported_separately_from_calls():
    # A failed call cost real compute. Folding it into `calls` would flatter the
    # initiative that spent it.
    with patch.object(api_keys_route, "get_usage_by_tag", return_value=ROWS):
        row = _client().get("/v1/usage").json()["data"][0]
    assert row["calls"] == 128
    assert row["failed"] == 4


def test_no_tagged_usage_is_measured_false_not_a_silent_empty():
    with patch.object(api_keys_route, "get_usage_by_tag", return_value=[]):
        body = _client().get("/v1/usage").json()
    assert body["data"] == []
    assert body["measured"] is False


def test_a_broken_rollup_is_503_not_an_empty_list():
    # The distinction the whole endpoint turns on: "did not run" and "spent
    # nothing" are different answers and must not render identically.
    with patch.object(api_keys_route, "get_usage_by_tag", return_value=None):
        r = _client().get("/v1/usage")
    assert r.status_code == 503


def test_bad_since_is_a_400_with_a_usable_message():
    with patch.object(api_keys_route, "get_usage_by_tag", return_value=[]):
        r = _client().get("/v1/usage?since=not-a-date")
    assert r.status_code == 400
    assert "ISO-8601" in r.json()["detail"]


def test_an_invalid_key_is_401():
    with patch.object(api_keys_route, "get_user", return_value=None):
        r = _client().get("/v1/usage")
    assert r.status_code == 401
