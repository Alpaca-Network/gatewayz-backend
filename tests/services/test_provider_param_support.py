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

    def test_minimal_provider_gets_universal_subset_only(self, monkeypatch):
        """No provider on the current roster is minimal, so this uses a stand-in.

        The branch still needs cover: a future reduced-schema provider will use
        it, and an untested branch is where that provider's bugs will live.
        """
        monkeypatch.setattr(
            "src.services.provider_param_support.MINIMAL_PARAM_PROVIDERS",
            frozenset({"toy-provider"}),
        )
        assert supported_params_for("toy-provider") == UNIVERSAL_PARAMS


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

    def test_minimal_provider_drops_tools(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.provider_param_support.MINIMAL_PARAM_PROVIDERS",
            frozenset({"toy-provider"}),
        )
        params = {"temperature": 0.5, "tools": [{"type": "function"}]}
        filtered, dropped = filter_params_for_provider("toy-provider", params)
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


class TestStreamOptionsSerialization:
    """stream_options must reach providers as a dict, never a Pydantic model.

    ProxyRequest types it as StreamOptions. Forwarded as an object it is not
    JSON-serializable at the provider call, the streaming generator dies, and
    the caller receives HTTP 200 with an EMPTY body — no error, no chunks.
    Verified against production on 2026-08-07: identical requests returned 8 SSE
    lines without stream_options and 0 with it, on both Anthropic and OpenAI.

    Any client asking for token counts on a stream sets include_usage, so this
    silently broke usage reporting for every streaming caller that wanted it.
    """

    def _optional(self, **kw):
        from src.schemas.proxy import ProxyRequest

        req = ProxyRequest(
            model="m", messages=[{"role": "user", "content": "hi"}], stream=True, **kw
        )
        # Mirror the allowlist copy in prepare_upstream_request.
        opt = {}
        for name in ("stream_options", "tools"):
            v = getattr(req, name, None)
            if v is not None:
                opt[name] = v
        if opt.get("stream_options") is not None:
            so = opt["stream_options"]
            if hasattr(so, "model_dump"):
                opt["stream_options"] = so.model_dump(exclude_none=True)
        return opt

    def test_stream_options_becomes_a_plain_dict(self):
        opt = self._optional(stream_options={"include_usage": True})
        assert isinstance(opt["stream_options"], dict)
        assert opt["stream_options"] == {"include_usage": True}

    def test_serialized_stream_options_is_json_encodable(self):
        import json

        opt = self._optional(stream_options={"include_usage": True})
        json.dumps(opt["stream_options"])

    def test_absent_stream_options_is_not_invented(self):
        assert "stream_options" not in self._optional()
