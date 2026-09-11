"""Bare vendor-native model ids must resolve to canonical catalog ids.

Regression cover for the production defect where `claude-sonnet-4-6` — the id
every Anthropic SDK sends — was rejected with 503 while
`anthropic/claude-sonnet-4-6` served fine.
"""

from __future__ import annotations

import pytest

from src.services import model_resolution
from src.services.model_resolution import resolve_catalog_model_id

# Captured at import, before the autouse fixture below replaces the attribute.
# Tests that need the REAL loader (to exercise its catalog sources) restore it.
_REAL_LOAD_CATALOG_IDS = model_resolution._load_catalog_ids

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


class TestUndatedSnapshotAlias:
    """An undated vendor id must reach its dated snapshot.

    Anthropic publishes both `claude-sonnet-4-5` and the dated snapshot
    `claude-sonnet-4-5-20250929`, and the undated form is what its own docs
    and most SDK examples use. Our catalog carries only the dated row, so the
    undated id resolved to nothing — measured in production 2026-09-09,
    `anthropic/claude-sonnet-4-5` came back 503 for a model we actually serve.

    This is resolution, not substitution: the alias denotes exactly one
    catalog model. Where it would denote two, we refuse and name them, the
    same rule the rest of this module follows.
    """

    DATED = [
        {"id": "anthropic/claude-sonnet-4-5-20250929"},
        {"id": "anthropic/claude-haiku-4-5-20251001"},
        {"id": "openai/gpt-4o-mini"},
    ]

    @pytest.fixture(autouse=True)
    def _dated_catalog(self, monkeypatch):
        monkeypatch.setattr(
            model_resolution, "_load_catalog_ids", lambda: [r["id"] for r in self.DATED]
        )
        monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
        model_resolution.invalidate_resolution_index()
        yield
        model_resolution.invalidate_resolution_index()

    def test_qualified_undated_id_resolves_to_the_snapshot(self):
        r = resolve_catalog_model_id("anthropic/claude-sonnet-4-5")
        assert r.canonical_id == "anthropic/claude-sonnet-4-5-20250929"
        assert r.matched_by == "undated"

    def test_bare_undated_id_resolves_to_the_snapshot(self):
        r = resolve_catalog_model_id("claude-sonnet-4-5")
        assert r.canonical_id == "anthropic/claude-sonnet-4-5-20250929"

    def test_free_suffix_survives_the_alias(self):
        r = resolve_catalog_model_id("claude-sonnet-4-5:free")
        assert r.canonical_id == "anthropic/claude-sonnet-4-5-20250929:free"

    def test_two_snapshots_refuse_rather_than_pick_the_newest(self, monkeypatch):
        # Picking "latest" would silently move a caller between models on a
        # catalog sync. Two candidates is a refusal everywhere else here.
        monkeypatch.setattr(
            model_resolution,
            "_load_catalog_ids",
            lambda: [
                "anthropic/claude-sonnet-4-5-20250929",
                "anthropic/claude-sonnet-4-5-20251215",
            ],
        )
        model_resolution.invalidate_resolution_index()
        r = resolve_catalog_model_id("claude-sonnet-4-5")
        assert r.canonical_id is None
        assert r.matched_by == "ambiguous"
        assert len(r.candidates) == 2

    def test_a_genuinely_unknown_id_is_still_unresolved(self):
        assert resolve_catalog_model_id("claude-sonnet-9-9").canonical_id is None

    def test_exact_still_wins_over_the_alias(self, monkeypatch):
        monkeypatch.setattr(
            model_resolution,
            "_load_catalog_ids",
            lambda: [
                "anthropic/claude-sonnet-4-5",
                "anthropic/claude-sonnet-4-5-20250929",
            ],
        )
        model_resolution.invalidate_resolution_index()
        r = resolve_catalog_model_id("anthropic/claude-sonnet-4-5")
        assert r.canonical_id == "anthropic/claude-sonnet-4-5"
        assert r.matched_by == "exact"


class TestUndatedAliasWhenTheIndexIsOnlyTheAliasTable:
    """Production reality, measured 2026-09-10 (#2298).

    `/health/model-resolution` showed every fully-qualified catalog id
    reporting `unresolved` while every bare form reported `alias`: the index
    built from `get_cached_unique_models()` holds none of the prefixed ids
    that GET /v1/models advertises, so the curated `model_aliases` table is
    what actually maps names onto models.

    An undated scan over the exact-id set alone therefore scans an empty
    universe and finds nothing — which is exactly what shipped in #2297 and
    #2300 and why neither fixed anything in production while both passed
    against a stubbed catalog. These stub the catalog the way production
    really behaves.
    """

    ALIASES = {
        "claude-sonnet-4-5-20250929": "anthropic/claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
    }

    @pytest.fixture(autouse=True)
    def _alias_only_catalog(self, monkeypatch):
        # The catalog index is non-empty but carries unrelated ids -- the
        # served Anthropic models are reachable only through aliases.
        monkeypatch.setattr(
            model_resolution, "_load_catalog_ids", lambda: ["deepinfra/some-other-model"]
        )
        monkeypatch.setattr(model_resolution, "_load_alias_map", lambda: dict(self.ALIASES))
        model_resolution.invalidate_resolution_index()
        yield
        model_resolution.invalidate_resolution_index()

    def test_undated_alias_resolves_through_the_alias_targets(self):
        r = resolve_catalog_model_id("claude-sonnet-4-5")
        assert r.canonical_id == "anthropic/claude-sonnet-4-5-20250929"
        assert r.matched_by == "undated"

    def test_qualified_undated_alias_resolves_too(self):
        r = resolve_catalog_model_id("anthropic/claude-sonnet-4-5")
        assert r.canonical_id == "anthropic/claude-sonnet-4-5-20250929"

    def test_a_curated_alias_still_wins_before_the_undated_scan(self):
        r = resolve_catalog_model_id("claude-sonnet-4-6")
        assert r.matched_by == "alias"

    def test_an_unknown_model_is_still_unresolved(self):
        assert resolve_catalog_model_id("claude-sonnet-9-9").canonical_id is None


class TestEveryAdvertisedModelResolves:
    """#2304: if GET /v1/models lists it, the resolver has to accept it.

    A catalog that advertises ids the gateway then rejects is a broken promise
    to anyone building from it — which is exactly what Flashy would have hit.
    Until 2026-09-11 the index came only from the DEDUPED catalog while
    /v1/models serves the FLAT one, and in production the two shared no ids at
    all.

    These fixtures deliberately make the two sources DISAGREE, which is what
    production actually looked like. The pre-existing fixtures used one
    realistic source and agreed with themselves, which is why two rounds of
    fixes passed CI while changing nothing live.
    """

    FLAT = [
        {"id": "anthropic/claude-sonnet-4-6"},
        {"id": "anthropic/claude-sonnet-4-5-20250929"},
        {"id": "openai/gpt-4o-mini"},
    ]
    UNIQUE = [{"id": "deepinfra/unrelated-model"}]

    @pytest.fixture(autouse=True)
    def _two_disagreeing_sources(self, monkeypatch):
        # The module-level autouse fixture stubs _load_catalog_ids wholesale;
        # this class is testing that function itself, so put the real one back
        # and stub the sources it reads instead.
        monkeypatch.setattr(model_resolution, "_load_catalog_ids", _REAL_LOAD_CATALOG_IDS)
        monkeypatch.setattr(
            "src.services.models.get_cached_models", lambda _g: list(self.FLAT), raising=False
        )
        monkeypatch.setattr(
            "src.services.cache.model_catalog_cache.get_cached_unique_models",
            lambda: list(self.UNIQUE),
            raising=False,
        )
        monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
        model_resolution.invalidate_resolution_index()
        yield
        model_resolution.invalidate_resolution_index()

    @pytest.mark.parametrize("row", FLAT)
    def test_every_advertised_id_resolves_to_itself(self, row):
        r = resolve_catalog_model_id(row["id"])
        assert r.canonical_id == row["id"], f"{row['id']} is advertised but does not resolve"

    def test_the_deduped_source_is_still_indexed(self):
        # Kept in the union rather than replaced: nothing should depend on the
        # two sources agreeing.
        assert resolve_catalog_model_id("deepinfra/unrelated-model").canonical_id is not None

    def test_a_bare_advertised_name_resolves(self):
        assert resolve_catalog_model_id("gpt-4o-mini").canonical_id == "openai/gpt-4o-mini"

    def test_an_undated_advertised_alias_resolves(self):
        r = resolve_catalog_model_id("claude-sonnet-4-5")
        assert r.canonical_id == "anthropic/claude-sonnet-4-5-20250929"

    def test_one_dead_source_does_not_take_down_the_other(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("cache down")

        monkeypatch.setattr("src.services.models.get_cached_models", _boom, raising=False)
        model_resolution.invalidate_resolution_index()
        # The deduped source alone must still produce a usable index.
        assert resolve_catalog_model_id("deepinfra/unrelated-model").canonical_id is not None
