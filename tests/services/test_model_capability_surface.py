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
