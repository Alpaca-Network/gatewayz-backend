"""The unique-models catalog must not depend on an absent foreign key.

``unique_models_provider`` indexes ``provider_id`` but never declared a foreign
key to ``providers`` (20260129000001), so PostgREST answered ``providers!inner``
embeds with PGRST200. The query swallowed that into ``[]``, and the unique
catalog silently served nothing from the day the table was created.
"""

from unittest.mock import MagicMock

import pytest

from src.db import models_catalog_db


class _Query:
    """Minimal PostgREST query recorder."""

    def __init__(self, table: str, rows_by_table: dict, recorder: dict):
        self.table = table
        self.rows_by_table = rows_by_table
        self.recorder = recorder
        self._offset = 0

    def select(self, columns: str):
        self.recorder.setdefault("selects", {})[self.table] = columns
        return self

    def eq(self, field: str, value):
        self.recorder.setdefault("eq", []).append((self.table, field, value))
        return self

    def in_(self, field: str, values):
        self.recorder.setdefault("in_", []).append((self.table, field, list(values)))
        return self

    def range(self, start: int, _end: int):
        self._offset = start
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.rows_by_table.get(self.table, [])
        return MagicMock(data=rows if self._offset == 0 else [])


@pytest.fixture
def fake_supabase(monkeypatch):
    recorder: dict = {}
    rows_by_table = {
        "unique_models": [
            {"id": 1, "model_name": "GPT-4", "model_count": 1, "sample_model_id": "openai/gpt-4"},
            {"id": 2, "model_name": "Ghost", "model_count": 1, "sample_model_id": "gone/ghost"},
        ],
        "providers": [
            {"id": 10, "slug": "openai", "name": "OpenAI"},
            {"id": 99, "slug": "openrouter", "name": "OpenRouter"},
        ],
        "unique_models_provider": [
            {
                "unique_model_id": 1,
                "provider_id": 10,
                "models": {
                    "id": 500,
                    "model_name": "GPT-4",
                    "provider_model_id": "gpt-4",
                    "metadata": {"pricing_raw": {"prompt": "0.03", "completion": "0.06"}},
                    "context_length": 8192,
                    "health_status": "healthy",
                    "average_response_time_ms": 100,
                    "modality": "text->text",
                    "supports_streaming": True,
                    "supports_function_calling": True,
                    "supports_vision": False,
                    "description": "",
                    "is_active": True,
                },
            },
            {
                # Mapping to a provider row that is gone — must be skipped, not
                # emitted with a null provider the router cannot route to.
                "unique_model_id": 2,
                "provider_id": 4242,
                "models": {
                    "id": 501,
                    "model_name": "Ghost",
                    "provider_model_id": "ghost",
                    "metadata": {},
                    "context_length": 1,
                    "health_status": "unknown",
                    "average_response_time_ms": 0,
                    "modality": "text->text",
                    "supports_streaming": False,
                    "supports_function_calling": False,
                    "supports_vision": False,
                    "description": "",
                    "is_active": True,
                },
            },
        ],
    }

    client = MagicMock()
    client.table.side_effect = lambda name: _Query(name, rows_by_table, recorder)
    monkeypatch.setattr(models_catalog_db, "get_client_for_query", lambda **_k: client)
    monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda slug: True)
    return recorder


def test_mappings_query_does_not_embed_providers(fake_supabase):
    """PGRST200 regression: the embed has no foreign key to resolve."""
    models_catalog_db.get_all_unique_models_for_catalog(include_inactive=False)

    select = fake_supabase["selects"]["unique_models_provider"]
    assert "providers!inner" not in select
    assert "provider_id" in select


def test_returns_models_joined_via_provider_id(fake_supabase):
    result = models_catalog_db.get_all_unique_models_for_catalog(include_inactive=False)

    assert len(result) == 1
    assert result[0]["providers"][0]["provider_slug"] == "openai"


def test_orphan_provider_mapping_is_skipped(fake_supabase):
    result = models_catalog_db.get_all_unique_models_for_catalog(include_inactive=False)

    assert all(um["model_name"] != "Ghost" for um in result)


def test_only_active_providers_are_loaded(fake_supabase):
    """The unique view must hide deactivated providers like every other surface."""
    models_catalog_db.get_all_unique_models_for_catalog(include_inactive=False)

    assert ("providers", "is_active", True) in fake_supabase["eq"]


def test_disabled_providers_are_filtered_out(monkeypatch, fake_supabase):
    """ENABLED_PROVIDERS applies here too (North Star §5)."""
    monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda slug: False)

    assert models_catalog_db.get_all_unique_models_for_catalog(include_inactive=False) == []


def test_provider_filter_is_pushed_into_the_query(fake_supabase):
    """Filtering in Python paged over every retired provider's mappings."""
    models_catalog_db.get_all_unique_models_for_catalog(include_inactive=False)

    pushed = [c for c in fake_supabase["in_"] if c[0] == "unique_models_provider"]
    assert pushed, "provider_id filter must be applied server-side"
    assert set(pushed[0][2]) == {10, 99}
