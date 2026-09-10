"""/health/model-resolution reports how an id resolves against the live index.

Added while chasing #2298: the resolution index is built from
`get_cached_unique_models()`, which the resolver is the only caller of and no
endpoint exposes. Its contents could only be inferred from the wording of a
400, and three successive hypotheses were argued from that inference before
one of them turned out to rest on a false premise. `matched_by` replaces the
whole exercise with one call.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services import model_resolution

CATALOG = [
    "anthropic/claude-sonnet-4-5-20250929",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-4o-mini",
]


@pytest.fixture(autouse=True)
def _stub_catalog(monkeypatch):
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: list(CATALOG))
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    model_resolution.invalidate_resolution_index()
    yield
    model_resolution.invalidate_resolution_index()


def _client():
    from src.routes import health

    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


def test_reports_exact_match():
    body = _client().get("/health/model-resolution?model=anthropic/claude-sonnet-4-6").json()
    assert body["matched_by"] == "exact"
    assert body["canonical_id"] == "anthropic/claude-sonnet-4-6"


def test_reports_suffix_match():
    body = _client().get("/health/model-resolution?model=claude-sonnet-4-6").json()
    assert body["matched_by"] == "suffix"


def test_reports_undated_match():
    body = _client().get("/health/model-resolution?model=claude-sonnet-4-5").json()
    assert body["matched_by"] == "undated"
    assert body["canonical_id"] == "anthropic/claude-sonnet-4-5-20250929"


def test_reports_unresolved_without_pretending():
    body = _client().get("/health/model-resolution?model=claude-nope-9").json()
    assert body["matched_by"] == "unresolved"
    assert body["canonical_id"] is None


def test_reports_whether_the_index_is_empty():
    # The difference between "not in the catalog" and "the catalog is cold" is
    # the single most useful thing this endpoint says.
    assert _client().get("/health/model-resolution?model=x").json()["index_is_empty"] is False


def test_response_carries_no_pricing_or_account_fields():
    body = _client().get("/health/model-resolution?model=claude-sonnet-4-6").json()
    assert set(body) == {
        "input",
        "canonical_id",
        "matched_by",
        "candidates",
        "index_is_empty",
        "commit",
    }
