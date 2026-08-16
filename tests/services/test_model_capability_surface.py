"""Tests for the public model capability surface.

The governing rule is that a false positive is worse than a false negative: an
agent that trusts an incorrect "tools: true" fails at runtime, whereas one that
routes around an under-reported model merely misses an option.
"""

import pytest

from src.services.model_capability_surface import (
    build_supported_parameters,
    enrich_model_with_capabilities,
    supports_caching,
    supports_reasoning,
    supports_tools,
    supports_vision,
)


class TestSupportsTools:
    def test_explicit_flag_wins_over_family_heuristic(self):
        model = {"id": "anthropic/claude-sonnet-4", "supports_tools": False}
        assert supports_tools(model) is False

    def test_known_family_detected(self):
        assert supports_tools({"id": "anthropic/claude-sonnet-4"}) is True

    def test_unknown_model_reports_no_tool_support(self):
        """Absence of evidence is reported as absence of support."""
        assert supports_tools({"id": "someone/mystery-model-v1"}) is False

    def test_explicit_true_respected(self):
        assert supports_tools({"id": "x/y", "supports_tools": True}) is True

    def test_alternate_flag_names_accepted(self):
        assert supports_tools({"id": "x/y", "function_calling": True}) is True


class TestSupportsVision:
    def test_known_vision_family(self):
        assert supports_vision({"id": "openai/gpt-4o"}) is True

    def test_text_only_model(self):
        assert supports_vision({"id": "meta/llama-3.1-8b"}) is False

    def test_explicit_flag_wins(self):
        assert supports_vision({"id": "openai/gpt-4o", "supports_vision": False}) is False


class TestSupportsCaching:
    def test_provider_slug_match(self):
        assert supports_caching({"id": "x/y", "provider_slug": "anthropic"}) is True

    def test_model_id_match(self):
        assert supports_caching({"id": "anthropic/claude-sonnet-4"}) is True

    def test_unknown_provider_reports_no_caching(self):
        """Claiming cache support the billing layer cannot price is the exact
        mispricing this change set exists to fix."""
        assert supports_caching({"id": "obscure/model", "provider_slug": "obscure"}) is False

    def test_explicit_flag_wins(self):
        model = {"id": "anthropic/claude-sonnet-4", "supports_caching": False}
        assert supports_caching(model) is False


class TestSupportedParameters:
    def test_baseline_always_present(self):
        params = build_supported_parameters({"id": "obscure/model"})
        for expected in ("max_tokens", "temperature", "top_p", "stop"):
            assert expected in params

    def test_tool_params_added_for_tool_models(self):
        params = build_supported_parameters({"id": "anthropic/claude-sonnet-4"})
        assert "tools" in params
        assert "tool_choice" in params
        assert "parallel_tool_calls" in params

    def test_tool_params_absent_for_non_tool_models(self):
        params = build_supported_parameters({"id": "obscure/model"})
        assert "tools" not in params

    def test_cache_control_advertised_for_cache_capable_models(self):
        params = build_supported_parameters({"id": "anthropic/claude-sonnet-4"})
        assert "cache_control" in params

    def test_cache_control_absent_otherwise(self):
        params = build_supported_parameters({"id": "obscure/model"})
        assert "cache_control" not in params

    def test_response_format_tracks_tool_support(self):
        assert "response_format" in build_supported_parameters({"id": "openai/gpt-4o"})
        assert "response_format" not in build_supported_parameters({"id": "obscure/model"})


class TestEnrichment:
    def test_adds_both_fields(self):
        model = enrich_model_with_capabilities({"id": "anthropic/claude-sonnet-4"})
        assert "supported_parameters" in model
        assert model["capabilities"]["tools"] is True
        assert model["capabilities"]["prompt_caching"] is True

    def test_streaming_always_true(self):
        model = enrich_model_with_capabilities({"id": "x/y"})
        assert model["capabilities"]["streaming"] is True

    def test_non_dict_passed_through_unchanged(self):
        assert enrich_model_with_capabilities(None) is None

    def test_returns_same_object_for_chaining(self):
        model = {"id": "x/y"}
        assert enrich_model_with_capabilities(model) is model

    def test_missing_id_does_not_raise(self):
        model = enrich_model_with_capabilities({})
        assert "supported_parameters" in model


class TestCurrentProviderRoster:
    """Families added when supply moved direct.

    A live production catalog reported tools=false for six models that plainly
    support tool calling. Under-reporting fails safe, but agent tools that
    filter on capability before sending a request never see those models.
    """

    @pytest.mark.parametrize(
        "model_id",
        [
            "moonshot/kimi-k2.6",
            "moonshot/kimi-k2.7-code",
            "moonshot/kimi-k3",
            "openai/gpt-3.5-turbo",
            "openai/gpt-3.5-turbo-0125",
            "openai/gpt-3.5-turbo-16k",
        ],
    )
    def test_models_that_were_mis_reported_now_advertise_tools(self, model_id):
        assert supports_tools({"id": model_id}) is True

    def test_genuinely_unknown_model_still_fails_safe(self):
        """The conservative default must survive the widened family list."""
        assert supports_tools({"id": "obscure/mystery-model-v1"}) is False


class TestServeTimeEnrichment:
    """Capabilities must be derivable from a bare DB row.

    Regression: enrichment was hooked into enrich_model_with_pricing, which
    only runs when the catalog is rebuilt from providers. Models served from
    the models_catalog table came back with no `capabilities` or
    `supported_parameters` keys at all — the fields appeared after a manual
    resync and vanished on the next scheduled one. Agent tools that filter on
    capability before sending a request saw nothing and assumed nothing.
    """

    DB_ROW = {
        "id": "anthropic/claude-sonnet-4",
        "provider_slug": "anthropic",
        "context_length": 200000,
        "pricing": {"prompt": "0.000003", "completion": "0.000015"},
    }

    def test_bare_db_row_gains_capabilities(self):
        model = enrich_model_with_capabilities(dict(self.DB_ROW))
        assert model["capabilities"]["tools"] is True
        assert "tools" in model["supported_parameters"]

    def test_enrichment_is_idempotent(self):
        """Serve-time enrichment runs on every request; twice must equal once."""
        once = enrich_model_with_capabilities(dict(self.DB_ROW))
        twice = enrich_model_with_capabilities(dict(once))
        assert once["capabilities"] == twice["capabilities"]
        assert once["supported_parameters"] == twice["supported_parameters"]

    def test_row_with_no_pricing_or_provider_still_enriches(self):
        model = enrich_model_with_capabilities({"id": "anthropic/claude-sonnet-4"})
        assert model["capabilities"]["tools"] is True


class TestRawCatalogRow:
    """`get_candidate_models` (auto-routing) passes raw `models` table rows,
    not the public API-formatted dict every other test in this file uses.

    That row shape has an INTEGER primary key in `id` (only meaningful for
    the `model_pricing` join) and the actual model slug in `provider_model_id`
    instead, with `provider_slug`/`source_gateway` nested under `metadata`.
    Regression: every check here used to call `.lower()` on the raw int `id`
    and raise `AttributeError` for every real auto-routing candidate.
    """

    DB_ROW = {
        "id": 2298,
        "provider_model_id": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
        "canonical_id": None,
        "supports_function_calling": True,
        "supports_vision": False,
        "is_reasoning": False,
        "metadata": {"provider_slug": "together", "source_gateway": "together"},
    }

    def test_supports_tools_does_not_raise_on_int_id(self):
        assert supports_tools(dict(self.DB_ROW)) is True

    def test_supports_vision_does_not_raise_on_int_id(self):
        assert supports_vision(dict(self.DB_ROW)) is False

    def test_supports_caching_does_not_raise_on_int_id(self):
        # "together" isn't in CACHE_CAPABLE_PROVIDERS -- just must not crash.
        assert supports_caching(dict(self.DB_ROW)) is False

    def test_supports_caching_reads_provider_from_metadata(self):
        row = dict(self.DB_ROW)
        row["metadata"] = {"provider_slug": "anthropic", "source_gateway": "anthropic"}
        assert supports_caching(row) is True  # cache-capable provider, nested under metadata

    def test_family_fallback_uses_provider_model_id_not_int_id(self):
        row = {"id": 999, "provider_model_id": "anthropic/claude-sonnet-4"}
        assert supports_tools(row) is True
        assert supports_caching(row) is True

    def test_no_provider_model_id_and_non_string_id_is_safe(self):
        assert supports_tools({"id": 12345}) is False


class TestSupportsReasoning:
    def test_flag_true(self):
        assert supports_reasoning({"id": 1, "is_reasoning": True}) is True

    def test_flag_false(self):
        assert supports_reasoning({"id": 1, "is_reasoning": False}) is False

    def test_missing_flag_fails_safe(self):
        """No family-heuristic fallback -- unflagged models report unsupported."""
        assert supports_reasoning({"id": "openai/o3"}) is False
