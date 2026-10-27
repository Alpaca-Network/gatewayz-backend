"""The resolver matches case-insensitively but must hand back the catalog's own
spelling: pricing and routing look ids up case-sensitively. Production
2026-10-27: every DeepInfra model ('deepseek-ai/DeepSeek-V3.2') was resolved to
'deepseek-ai/deepseek-v3.2' and refused as model_not_priced."""

from __future__ import annotations

import pytest

from src.services import model_resolution
from src.services.model_resolution import resolve_catalog_model_id

CATALOG = ["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3-235B-A22B-Instruct-2507", "openai/gpt-4o-mini"]


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: list(CATALOG))
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    model_resolution.invalidate_resolution_index()
    yield
    model_resolution.invalidate_resolution_index()


@pytest.mark.parametrize(
    "sent",
    ["deepseek-ai/DeepSeek-V3.2", "deepseek-ai/deepseek-v3.2", "DEEPSEEK-AI/DEEPSEEK-V3.2"],
)
def test_exact_match_returns_catalog_case(sent):
    assert resolve_catalog_model_id(sent).canonical_id == "deepseek-ai/DeepSeek-V3.2"


def test_bare_suffix_returns_catalog_case():
    assert resolve_catalog_model_id("qwen3-235b-a22b-instruct-2507").canonical_id == (
        "Qwen/Qwen3-235B-A22B-Instruct-2507"
    )


def test_lowercase_catalog_ids_are_unchanged():
    assert resolve_catalog_model_id("openai/gpt-4o-mini").canonical_id == "openai/gpt-4o-mini"


def test_free_suffix_is_kept():
    assert resolve_catalog_model_id("deepseek-ai/deepseek-v3.2:free").canonical_id == (
        "deepseek-ai/DeepSeek-V3.2:free"
    )
