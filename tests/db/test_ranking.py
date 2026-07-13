"""
Comprehensive tests for Ranking database operations
"""

from unittest.mock import MagicMock, Mock, patch

import pytest


class TestRanking:
    """Test Ranking database functionality"""

    @patch("src.db.ranking.get_supabase_client")
    def test_module_imports(self, mock_client):
        """Test that module imports successfully"""
        import src.db.ranking

        assert src.db.ranking is not None

    @patch("src.db.ranking.get_supabase_client")
    def test_module_has_expected_attributes(self, mock_client):
        """Test module exports"""
        from src.db import ranking

        assert hasattr(ranking, "__name__")


class TestFormatTokens:
    def test_formats_trillions(self):
        from src.db.ranking import _format_tokens

        assert _format_tokens(4_650_000_000_000) == "4.65T tokens"

    def test_formats_small_counts_without_suffix(self):
        from src.db.ranking import _format_tokens

        assert _format_tokens(42) == "42 tokens"

    def test_handles_none(self):
        from src.db.ranking import _format_tokens

        assert _format_tokens(None) == "0 tokens"


class TestDeriveModelUrl:
    def test_org_slash_model_form(self):
        from src.db.ranking import _derive_model_url

        assert _derive_model_url("openai/gpt-5.1", "openai") == "/models/openai/gpt-5.1"

    def test_colon_provider_form(self):
        from src.db.ranking import _derive_model_url

        assert _derive_model_url("aimo:model-name", None) == "/models/aimo/model-name"

    def test_bare_model_id_uses_provider_slug(self):
        from src.db.ranking import _derive_model_url

        assert _derive_model_url("gemma-4-31b", "cerebras") == "/models/cerebras/gemma-4-31b"


class TestGetRankingModelsFromUsage:
    """get_ranking_models_from_usage: real-usage rows enriched from the catalog,
    with a per-bucket fallback to the scraped snapshot when usage is sparse."""

    @patch("src.db.ranking._get_latest_models_for_bucket")
    @patch("src.db.gateway_analytics.get_trending_models")
    @patch("src.services.models.get_cached_models")
    def test_enriches_usage_rows_from_catalog_when_above_floor(
        self, mock_catalog, mock_trending, mock_fallback
    ):
        from src.db.ranking import _MIN_REAL_ROWS, get_ranking_models_from_usage

        mock_catalog.return_value = [
            {
                "id": "openai/gpt-5.1",
                "name": "GPT-5.1",
                "provider_slug": "openai",
                "context_length": 128000,
                "pricing": {"prompt": "1e-06", "completion": "2e-06"},
            }
        ]
        usage_rows = [
            {
                "model": "openai/gpt-5.1",
                "provider": "openai",
                "gateway": "openai",
                "requests": 100,
                "total_tokens": 4_650_000_000_000,
                "unique_users": 12,
            }
        ] * _MIN_REAL_ROWS
        mock_trending.return_value = usage_rows

        rows = get_ranking_models_from_usage(limit=1)

        mock_fallback.assert_not_called()
        assert rows[0]["model_name"] == "GPT-5.1"
        assert rows[0]["author"] == "openai"
        assert rows[0]["provider_slug"] == "openai"
        assert rows[0]["tokens"] == "4.65T tokens"
        assert rows[0]["time_period"] == "Trending"
        assert rows[0]["model_url"] == "/models/openai/gpt-5.1"
        assert rows[0]["rank"] == 1

    @patch("src.db.ranking._get_latest_models_for_bucket")
    @patch("src.db.gateway_analytics.get_trending_models")
    @patch("src.services.models.get_cached_models")
    def test_falls_back_to_scraped_snapshot_below_floor(
        self, mock_catalog, mock_trending, mock_fallback
    ):
        from src.db.ranking import get_ranking_models_from_usage

        mock_catalog.return_value = []
        mock_trending.return_value = []  # every bucket is below the floor
        mock_fallback.return_value = [{"model_name": "Scraped Model", "rank": 1}]

        rows = get_ranking_models_from_usage()

        # 4 time buckets, each falling back
        assert mock_fallback.call_count == 4
        assert all(r["model_name"] == "Scraped Model" for r in rows)

    @patch("src.db.ranking._get_latest_models_for_bucket")
    @patch("src.db.gateway_analytics.get_trending_models")
    @patch("src.services.models.get_cached_models")
    def test_unknown_model_still_produces_a_row(self, mock_catalog, mock_trending, mock_fallback):
        """A model with usage but no catalog entry shouldn't be dropped."""
        from src.db.ranking import _MIN_REAL_ROWS, get_ranking_models_from_usage

        mock_catalog.return_value = []
        mock_trending.return_value = [
            {"model": "some/untracked-model", "requests": 1, "total_tokens": 10}
        ] * _MIN_REAL_ROWS

        rows = get_ranking_models_from_usage(limit=1)

        assert rows[0]["model_name"] == "some/untracked-model"
        assert rows[0]["author"] == "some"
