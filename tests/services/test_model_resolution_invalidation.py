"""The resolution index is derived from the catalog, so a sync must drop it.

Without this, a catalog sync that introduces a second model sharing a bare
name would leave the stale index happily resolving that name to whichever
model it saw first — routing a prompt to a model the caller didn't ask for.
"""

from __future__ import annotations

from src.services import model_resolution


def test_catalog_invalidation_drops_the_resolution_index(monkeypatch):
    ids = ["anthropic/claude-sonnet-4-6"]
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: list(ids))
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    model_resolution.invalidate_resolution_index()

    assert (
        model_resolution.resolve_catalog_model_id("claude-sonnet-4-6").canonical_id
        == "anthropic/claude-sonnet-4-6"
    )

    # A catalog sync adds a competing re-host of the same bare name.
    ids.append("near/claude-sonnet-4-6")

    # Stale index: still resolves, because nothing has told it to rebuild.
    assert model_resolution.resolve_catalog_model_id("claude-sonnet-4-6").canonical_id is not None

    from src.services.cache.model_catalog_cache import invalidate_full_catalog

    invalidate_full_catalog()

    after = model_resolution.resolve_catalog_model_id("claude-sonnet-4-6")
    assert after.canonical_id is None
    assert after.matched_by == "ambiguous"


def test_index_is_empty_reports_a_cold_catalog(monkeypatch):
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", list)
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    model_resolution.invalidate_resolution_index()
    assert model_resolution.index_is_empty() is True

    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: ["openai/gpt-4o-mini"])
    model_resolution.invalidate_resolution_index()
    assert model_resolution.index_is_empty() is False
