"""One effort knob, four provider dialects.

Every expectation here was checked against the live provider APIs — each wrong
shape returns 400, so these are contract tests, not guesses:

    gpt-5.6-sol  reasoning_effort=low          200
    gpt-5.6-sol  reasoning_effort=minimal      400 does not support 'minimal'
    gpt-4o-mini  reasoning_effort=low          400 Unrecognized request argument
    gpt-5-nano   max_tokens                    400 Unsupported parameter
    sonnet-5     thinking.enabled              400 use adaptive + output_config
    sonnet-5     adaptive + output_config      200
    sonnet-4-6   thinking.enabled budget=1024  200 thinking_tokens=7
    grok-4       reasoning_effort=low          200 reasoning_tokens=91
"""

import pytest

from src.services.providers.reasoning_effort import (
    apply_reasoning_effort,
    apply_reasoning_effort_anthropic_native,
    claude_rejects_sampling,
    is_reasoning_model,
    normalize_sampling_params,
    normalize_token_limit,
    uses_max_completion_tokens,
)


class TestCapabilityDetection:
    @pytest.mark.parametrize(
        "gateway,model,expected",
        [
            ("openai", "gpt-5.6-sol", True),
            ("openai", "openai/gpt-5.6-sol", True),
            ("openai", "o1-2024-12-17", True),
            ("openai", "o3-mini", True),
            ("openai", "gpt-4o-mini", False),
            ("openai", "gpt-3.5-turbo", False),
            ("anthropic", "claude-sonnet-5", True),
            ("xai", "grok-4", True),
            ("moonshot", "kimi-k2.6", True),
            ("featherless", "whatever", False),
        ],
    )
    def test_reasoning_models_are_identified(self, gateway, model, expected):
        assert is_reasoning_model(gateway, model) is expected

    def test_only_openai_reasoning_needs_the_new_token_param(self):
        assert uses_max_completion_tokens("openai", "gpt-5.6-sol") is True
        assert uses_max_completion_tokens("openai", "gpt-4o-mini") is False
        # Anthropic and xAI keep max_tokens.
        assert uses_max_completion_tokens("anthropic", "claude-sonnet-5") is False
        assert uses_max_completion_tokens("xai", "grok-4") is False


class TestTokenLimitRename:
    def test_reasoning_model_gets_max_completion_tokens(self):
        payload = normalize_token_limit({"max_tokens": 100}, "openai", "gpt-5.6-sol")

        assert payload == {"max_completion_tokens": 100}

    def test_ordinary_model_keeps_max_tokens(self):
        payload = normalize_token_limit({"max_tokens": 100}, "openai", "gpt-4o-mini")

        assert payload == {"max_tokens": 100}

    def test_absent_max_tokens_is_left_alone(self):
        assert normalize_token_limit({}, "openai", "gpt-5.6-sol") == {}


class TestOpenAIDialect:
    def test_effort_is_passed_through(self):
        payload = apply_reasoning_effort({}, "openai", "gpt-5.6-sol", "high")

        assert payload["reasoning_effort"] == "high"

    def test_dropped_for_non_reasoning_model(self):
        """Forwarding here is a 400: 'Unrecognized request argument'."""
        payload = apply_reasoning_effort({}, "openai", "gpt-4o-mini", "high")

        assert "reasoning_effort" not in payload

    @pytest.mark.parametrize("effort", ["minimal", "none", "ultra", "", "  "])
    def test_unsupported_values_are_dropped(self, effort):
        """gpt-5.6-* rejects 'minimal' outright, so it must never be sent."""
        payload = apply_reasoning_effort({}, "openai", "gpt-5.6-sol", effort)

        assert "reasoning_effort" not in payload

    def test_case_and_whitespace_tolerated(self):
        payload = apply_reasoning_effort({}, "openai", "gpt-5.6-sol", " HIGH ")

        assert payload["reasoning_effort"] == "high"


class TestAnthropicCompatSurface:
    """The gateway reaches Anthropic over its OpenAI-compatible endpoint, which
    takes reasoning_effort and rejects the native thinking shape:
    "Adaptive thinking is not available via ..." (confirmed live)."""

    @pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-opus-4-8", "claude-sonnet-4-6"])
    def test_effort_passes_through(self, model):
        payload = apply_reasoning_effort({"max_tokens": 2000}, "anthropic", model, "medium")

        assert payload["reasoning_effort"] == "medium"
        assert "thinking" not in payload
        assert "output_config" not in payload


class TestAnthropicNativeSurface:
    """/v1/messages needs the thinking shape, and two generations of it."""

    @pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-opus-4-8", "claude-fable-5"])
    def test_new_generation_uses_adaptive_and_output_config(self, model):
        payload = apply_reasoning_effort_anthropic_native({"max_tokens": 2000}, model, "medium")

        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["output_config"]["effort"] == "medium"

    def test_older_generation_uses_a_token_budget(self):
        payload = apply_reasoning_effort_anthropic_native(
            {"max_tokens": 8000}, "claude-sonnet-4-6", "low"
        )

        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1024}

    def test_budget_stays_strictly_below_max_tokens(self):
        payload = apply_reasoning_effort_anthropic_native(
            {"max_tokens": 2000}, "claude-sonnet-4-6", "high"
        )

        assert payload["thinking"]["budget_tokens"] < 2000

    @pytest.mark.parametrize("max_tokens", [100, 1000, 1024])
    def test_dropped_when_max_tokens_cannot_fit_the_minimum(self, max_tokens):
        """1024 <= budget < max_tokens is unsatisfiable here — emitting anything
        would 400, so the effort is dropped instead."""
        payload = apply_reasoning_effort_anthropic_native(
            {"max_tokens": max_tokens}, "claude-sonnet-4-6", "high"
        )

        assert "thinking" not in payload

    def test_existing_output_config_is_preserved(self):
        payload = apply_reasoning_effort_anthropic_native(
            {"output_config": {"something": 1}}, "claude-sonnet-5", "low"
        )

        assert payload["output_config"] == {"something": 1, "effort": "low"}


class TestOtherGateways:
    def test_xai_passes_through(self):
        assert apply_reasoning_effort({}, "xai", "grok-4", "low")["reasoning_effort"] == "low"

    def test_moonshot_passes_through(self):
        assert (
            apply_reasoning_effort({}, "moonshot", "kimi-k2.6", "high")["reasoning_effort"]
            == "high"
        )

    def test_unknown_gateway_drops_it(self):
        assert "reasoning_effort" not in apply_reasoning_effort({}, "novita", "some-model", "low")

    def test_no_effort_is_a_no_op(self):
        payload = {"max_tokens": 10}
        assert apply_reasoning_effort(payload, "openai", "gpt-5.6-sol", None) == {"max_tokens": 10}


class TestSamplingParams:
    """Kimi rejects any temperature but 1 (live 400 on the 2026-11-18 sweep)."""

    @pytest.mark.parametrize("model", ["kimi-k2.6", "moonshot/kimi-k3", "kimi-k3.1-pro"])
    def test_kimi_drops_non_one_temperature(self, model):
        payload = normalize_sampling_params({"temperature": 0.7, "top_p": 0.9}, "moonshot", model)
        assert "temperature" not in payload
        assert payload["top_p"] == 0.9

    def test_kimi_keeps_temperature_one(self):
        payload = normalize_sampling_params({"temperature": 1}, "moonshot", "kimi-k3")
        assert payload == {"temperature": 1}

    def test_kimi_without_temperature_untouched(self):
        payload = normalize_sampling_params({"max_tokens": 5}, "moonshot", "kimi-k3")
        assert payload == {"max_tokens": 5}

    @pytest.mark.parametrize(
        "gateway,model",
        [("openai", "gpt-4o-mini"), ("deepinfra", "kimi-k2.6"), ("moonshot", "moonshot-v1-8k")],
    )
    def test_other_models_keep_temperature(self, gateway, model):
        payload = normalize_sampling_params({"temperature": 0.2}, gateway, model)
        assert payload == {"temperature": 0.2}


class TestClaudeSampling:
    """Newer Claude models accept temperature only at 1 and reject top_p/top_k
    (live 2026-11-18: opus-4-7, fable-5-2, haiku-5-5 400 below 1; opus-5-5 top_p 400)."""

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("claude-opus-4-7", True),
            ("anthropic/claude-opus-4-8", True),
            ("claude-opus-5", True),
            ("claude-sonnet-5-5", True),
            ("claude-haiku-5-5", True),
            ("claude-fable-5", True),
            ("claude-fable-5-2", True),
            ("claude-opus-4-6", False),
            ("claude-sonnet-4-6", False),
            ("claude-haiku-4-5-20251001", False),
            ("claude-opus-4-5-20251101", False),
            ("claude-sonnet-4-5-20250929", False),
            ("claude-3-5-sonnet-20241022", False),
        ],
    )
    def test_detection(self, model, expected):
        assert claude_rejects_sampling(model) is expected

    def test_new_claude_drops_rejected_values(self):
        payload = {"temperature": 0, "top_p": 0.5, "top_k": 40, "max_tokens": 5}
        out = normalize_sampling_params(payload, "anthropic", "claude-opus-5-5")
        assert out == {"max_tokens": 5}

    def test_new_claude_keeps_temperature_one(self):
        out = normalize_sampling_params({"temperature": 1}, "anthropic", "claude-sonnet-5-5")
        assert out == {"temperature": 1}

    def test_older_claude_untouched(self):
        payload = {"temperature": 0, "top_p": 0.5}
        out = normalize_sampling_params(dict(payload), "anthropic", "claude-sonnet-4-6")
        assert out == payload
