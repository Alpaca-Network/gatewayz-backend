"""Bare vendor-native model ids must resolve to canonical catalog ids.

Regression cover for the production defect where `claude-sonnet-4-6` — the id
every Anthropic SDK sends — was rejected with 503 while
`anthropic/claude-sonnet-4-6` served fine.
"""

from __future__ import annotations

import pytest

from src.services import model_resolution
from src.services.model_resolution import resolve_catalog_model_id

CATALOG = [
    {"id": "anthropic/claude-sonnet-4-6"},
    {"id": "anthropic/claude-sonnet-5"},
    {"id": "openai/gpt-4o-mini"},
    {"id": "meta-llama/llama-3.1-8b-instruct"},
    {"id": "community/llama-3.1-8b-instruct"},
]


@pytest.fixture(autouse=True)
def _stub_sources(monkeypatch):
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: [r["id"] for r in CATALOG])
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    model_resolution.invalidate_resolution_index()
    yield
    model_resolution.invalidate_resolution_index()


def test_exact_catalog_id_passes_through_unchanged():
    r = resolve_catalog_model_id("anthropic/claude-sonnet-4-6")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6"
    assert r.matched_by == "exact"


def test_bare_vendor_id_resolves_to_the_single_catalog_match():
    r = resolve_catalog_model_id("claude-sonnet-4-6")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6"
    assert r.matched_by == "suffix"


def test_ambiguous_bare_id_refuses_and_reports_every_candidate():
    r = resolve_catalog_model_id("llama-3.1-8b-instruct")
    assert r.canonical_id is None
    assert r.matched_by == "ambiguous"
    assert r.candidates == (
        "community/llama-3.1-8b-instruct",
        "meta-llama/llama-3.1-8b-instruct",
    )


def test_unknown_id_is_unresolved_with_no_candidates():
    r = resolve_catalog_model_id("totally-fake-model-xyz")
    assert r.canonical_id is None
    assert r.matched_by == "unresolved"
    assert r.candidates == ()


def test_resolution_is_case_insensitive_on_input():
    r = resolve_catalog_model_id("Claude-Sonnet-4-6")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6"


def test_free_suffix_is_preserved_through_resolution():
    r = resolve_catalog_model_id("claude-sonnet-4-6:free")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6:free"


def test_curated_alias_beats_a_suffix_match(monkeypatch):
    monkeypatch.setattr(
        model_resolution,
        "_load_alias_map",
        lambda: {"llama-3.1-8b-instruct": "meta-llama/llama-3.1-8b-instruct"},
    )
    model_resolution.invalidate_resolution_index()
    r = resolve_catalog_model_id("llama-3.1-8b-instruct")
    assert r.canonical_id == "meta-llama/llama-3.1-8b-instruct"
    assert r.matched_by == "alias"


def test_empty_input_is_unresolved_and_does_not_raise():
    assert resolve_catalog_model_id("").canonical_id is None
    assert resolve_catalog_model_id("   ").canonical_id is None


def test_cold_catalog_leaves_the_id_unresolved(monkeypatch):
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", list)
    model_resolution.invalidate_resolution_index()
    r = resolve_catalog_model_id("claude-sonnet-4-6")
    assert r.canonical_id is None
    assert r.matched_by == "unresolved"
