"""Tests for the per-provider generation-parameter support matrix.

Regression cover for the class of bug this module was written to kill: a
parameter that ProxyRequest advertises being dropped between the route and the
provider without anyone noticing.
"""

from src.services.provider_param_support import (
    OPENAI_COMPATIBLE_PARAMS,
    UNIVERSAL_PARAMS,
    filter_params_for_provider,
    supported_params_for,
)


class TestSupportedParamsFor:
    def test_unknown_provider_gets_openai_baseline(self):
        assert supported_params_for("some-new-provider") == OPENAI_COMPATIBLE_PARAMS

    def test_none_provider_gets_openai_baseline(self):
        assert supported_params_for(None) == OPENAI_COMPATIBLE_PARAMS

    def test_provider_slug_is_case_insensitive(self):
        assert supported_params_for("Anthropic") == supported_params_for("anthropic")

    def test_anthropic_excludes_logit_bias(self):
        assert "logit_bias" not in supported_params_for("anthropic")

    def test_anthropic_still_supports_tools_and_tool_choice(self):
        supported = supported_params_for("anthropic")
        assert "tools" in supported
        assert "tool_choice" in supported

    def test_minimal_provider_gets_universal_subset_only(self):
        assert supported_params_for("nosana") == UNIVERSAL_PARAMS


class TestFilterParamsForProvider:
    def test_tool_choice_survives_for_openai_compatible_provider(self):
        """The original bug: tool_choice never reached the provider."""
        params = {"tools": [{"type": "function"}], "tool_choice": "required"}
        filtered, dropped = filter_params_for_provider("openrouter", params)
        assert filtered["tool_choice"] == "required"
        assert dropped == []

    def test_response_format_survives(self):
        """JSON mode / structured outputs were silently disabled."""
        params = {"response_format": {"type": "json_object"}}
        filtered, dropped = filter_params_for_provider("openrouter", params)
        assert filtered["response_format"] == {"type": "json_object"}
        assert dropped == []

    def test_stop_sequences_survive(self):
        params = {"stop": ["```"]}
        filtered, _ = filter_params_for_provider("openrouter", params)
        assert filtered["stop"] == ["```"]

    def test_unsupported_param_is_dropped_and_reported(self):
        params = {"temperature": 0.5, "logit_bias": {"123": 10}}
        filtered, dropped = filter_params_for_provider("anthropic", params)
        assert "logit_bias" not in filtered
        assert filtered["temperature"] == 0.5
        assert dropped == ["logit_bias"]

    def test_none_values_removed_without_being_reported_as_dropped(self):
        """A None param carries no user intent, so it is not a 'drop'."""
        params = {"temperature": 0.5, "tool_choice": None}
        filtered, dropped = filter_params_for_provider("openrouter", params)
        assert "tool_choice" not in filtered
        assert dropped == []

    def test_dropped_list_is_sorted(self):
        params = {"logprobs": True, "logit_bias": {"1": 1}, "seed": 42}
        _, dropped = filter_params_for_provider("anthropic", params)
        assert dropped == sorted(dropped)

    def test_minimal_provider_drops_tools(self):
        params = {"temperature": 0.5, "tools": [{"type": "function"}]}
        filtered, dropped = filter_params_for_provider("nosana", params)
        assert "tools" not in filtered
        assert "tools" in dropped

    def test_empty_params_is_a_no_op(self):
        filtered, dropped = filter_params_for_provider("openrouter", {})
        assert filtered == {}
        assert dropped == []

    def test_original_dict_is_not_mutated(self):
        params = {"temperature": 0.5, "logit_bias": {"1": 1}}
        filter_params_for_provider("anthropic", params)
        assert "logit_bias" in params
