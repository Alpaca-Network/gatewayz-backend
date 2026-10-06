"""Validator mode through the REAL /v1/chat/completions pipeline.

The per-surface tests (tests/routes/test_validator_mode_privacy.py) prove each
guard in isolation. This file proves the wiring: that the purpose read from
the key row in get_api_key actually reaches the leaf writers that run later in
the request -- in the route body, in ChatInferenceHandler, and in Starlette
background tasks after the response is sent -- and that the substitution
guards fire on the real route.

Reuses the route environment of the identity-firewall e2e (real auth chain,
real gates, real ChatInferenceHandler, real provider client; HTTP intercepted
at httpx), but puts the REAL chat_completion_requests / activity_log writers
back, with only their Supabase client faked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.config import Config
from src.services import key_purpose as kp
from tests.security.test_upstream_identity_firewall_e2e import (  # noqa: F401 - fixtures
    SENTINEL_API_KEY,
    SENTINEL_USER_ROW,
    _headers,
    _public_dns,
    app_client,
    intercepted_http,
    route_env,
)


class _Recorder:
    def __init__(self):
        self.inserts: list[str] = []

    def table(self, name):
        chain = MagicMock()
        for m in ("select", "eq", "ilike", "limit", "order"):
            getattr(chain, m).return_value = chain
        chain.execute.return_value = SimpleNamespace(data=[{"id": 1}])

        def _insert(payload):
            self.inserts.append(name)
            return chain

        chain.insert.side_effect = _insert
        return chain


@pytest.fixture
def validator_env(route_env, monkeypatch):
    """route_env + a switchable key purpose + REAL analytics writers + spies."""
    from src.db import chat_completion_requests as ccr

    state = {"purpose": "validator"}

    def _get_user(api_key):
        if api_key != SENTINEL_API_KEY:
            return None
        return {**SENTINEL_USER_ROW, "key_id": 1, kp.USER_FIELD: state["purpose"]}

    for target in (
        "src.security.identity.get_user",
        "src.handlers.chat_handler.get_user",
        "src.security.deps.get_user",
        "src.db.users.get_user",
    ):
        monkeypatch.setattr(target, _get_user)

    # Put the real leaf writers back (route_env mocks them away).
    monkeypatch.setattr(
        "src.handlers.chat_handler.save_chat_completion_request_with_cost",
        ccr.save_chat_completion_request_with_cost,
    )
    monkeypatch.setattr(
        "src.routes.chat.save_chat_completion_request_with_cost",
        ccr.save_chat_completion_request_with_cost,
    )
    recorder = _Recorder()
    monkeypatch.setattr(ccr, "get_supabase_client", lambda: recorder)
    monkeypatch.setattr(ccr, "get_model_id_by_name", lambda *a, **k: 42)
    monkeypatch.setattr("src.db.activity.get_supabase_client", lambda: recorder)
    # route_env mocks _handle_credits_and_usage, so log_activity is reached
    # through chat.py's real wrapper.
    monkeypatch.setattr("src.routes.chat.get_provider_from_model", lambda m: "openai")

    billing = Mock(return_value=None)
    monkeypatch.setattr("src.handlers.chat_handler.deduct_credits", billing)

    audit = Mock()
    from src.security import deps

    monkeypatch.setattr(deps.audit_logger, "log_api_key_usage", audit)

    history = Mock(return_value={"id": 1})
    monkeypatch.setattr("src.routes.chat_context.save_chat_message", history)
    monkeypatch.setattr("src.routes.chat_context.get_chat_session", lambda *a: {"id": 5})
    monkeypatch.setattr(
        "src.routes.chat_context.inject_conversation_history",
        AsyncMock(side_effect=lambda sid, anon, user, msgs: (msgs, sid)),
        raising=False,
    )
    monkeypatch.setattr(
        "src.routes.chat.inject_conversation_history",
        AsyncMock(side_effect=lambda sid, anon, user, msgs: (msgs, sid)),
    )

    search = AsyncMock(return_value=SimpleNamespace(success=False, result=None, error="off"))
    monkeypatch.setattr("src.services.tools.execute_tool", search)

    monkeypatch.setattr(Config, "OPENAI_API_KEY", "sk-canary", raising=False)
    return SimpleNamespace(
        state=state,
        recorder=recorder,
        billing=billing,
        audit=audit,
        history=history,
        search=search,
    )


def _post(app_client, model="gpt-4o-mini", **extra):
    body = {
        "model": model,
        "provider": "openai",
        "messages": [{"role": "user", "content": "what is the latest news on the vote?"}],
        "stream": False,
        **extra,
    }
    return app_client.post("/v1/chat/completions?session_id=5", json=body, headers=_headers())


class TestPrivacyEndToEnd:
    @pytest.mark.parametrize("purpose", ["validator", None])
    def test_only_billing_survives_a_validator_request(
        self, app_client, validator_env, intercepted_http, purpose
    ):
        validator_env.state["purpose"] = purpose
        resp = _post(app_client, auto_web_search=True)
        assert resp.status_code == 200, resp.text
        assert intercepted_http, "the provider was never called -- the test proves nothing"
        assert validator_env.billing.call_count == 1  # charged either way

        if purpose == "validator":
            assert validator_env.recorder.inserts == []
            assert validator_env.history.call_count == 0
            assert validator_env.audit.call_count == 0
            assert validator_env.search.await_count == 0
            # The prompt reached the provider unmodified (no search context).
            assert b"Web Search Results" not in intercepted_http[0].content
        else:
            assert "chat_completion_requests" in validator_env.recorder.inserts
            assert "activity_log" in validator_env.recorder.inserts
            assert validator_env.history.call_count == 2
            assert validator_env.audit.call_count == 1
            assert validator_env.search.await_count == 1


class TestNoSilentSubstitution:
    def test_validator_router_alias_is_a_400(self, app_client, validator_env, intercepted_http):
        resp = _post(app_client, model="openrouter/auto")
        assert resp.status_code == 400, resp.text
        assert "model_substitution_refused" in resp.text
        assert intercepted_http == []

    def test_validator_provider_remap_is_a_400_not_a_different_model(
        self, app_client, validator_env, intercepted_http, monkeypatch
    ):
        # A provider mapping that serves a different model than requested --
        # the shape of the gemini-1.5 -> 2.5 retirement redirect.
        monkeypatch.setattr(
            "src.services.model_transformations.transform_model_id",
            lambda model, provider, *a, **k: "gpt-4o",
        )
        resp = _post(app_client, model="gpt-4o-mini")
        assert resp.status_code == 400, resp.text
        assert "model_substitution_refused" in resp.text
        assert intercepted_http == []

    def test_general_key_remap_behaviour_is_unchanged(
        self, app_client, validator_env, intercepted_http, monkeypatch
    ):
        validator_env.state["purpose"] = None
        monkeypatch.setattr(
            "src.services.model_transformations.transform_model_id",
            lambda model, provider, *a, **k: "gpt-4o",
        )
        resp = _post(app_client, model="gpt-4o-mini")
        assert resp.status_code == 200, resp.text
        assert b'"gpt-4o"' in intercepted_http[0].content


class TestUnknownModelIsA400NeverARoute:
    """An unconfigured model id is refused at the gate; no provider is tried."""

    @pytest.fixture
    def real_gate(self, monkeypatch):
        import sys

        import src.services.pricing  # noqa: F401
        from src.security import inference_gates
        from src.services.model_resolution import ModelResolution

        monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", True, raising=False)
        monkeypatch.setattr(
            inference_gates,
            "resolve_catalog_model_id",
            lambda _m: ModelResolution(None, "unresolved"),
        )
        monkeypatch.setattr(inference_gates, "index_is_empty", lambda: False)
        monkeypatch.setattr(
            sys.modules["src.services.pricing"], "model_has_pricing", lambda m: False
        )
        monkeypatch.setattr(
            "src.services.cache.model_capabilities_cache.is_free_model", lambda _m: False
        )

    @pytest.mark.parametrize(
        "model,code",
        [
            ("gpt-9-imaginary", "model_not_found"),
            # Fully-qualified ids keep the gate's pre-resolution treatment: no
            # catalog price -> 400 model_not_priced. Still a 400, still unrouted.
            ("google/gemini-1.5-pro", "model_not_priced"),
        ],
    )
    @pytest.mark.parametrize("purpose", ["validator", None])
    def test_unknown_model(
        self, app_client, validator_env, intercepted_http, real_gate, model, code, purpose
    ):
        validator_env.state["purpose"] = purpose
        resp = _post(app_client, model=model)
        assert resp.status_code == 400, resp.text
        assert code in resp.text
        assert intercepted_http == []
        assert validator_env.billing.call_count == 0


class TestSubstitutionGuardsStreamingAndRegistry:
    """The same refusal on the streaming path and on multi-provider registry routes."""

    def test_streaming_provider_remap_is_refused(
        self, app_client, validator_env, intercepted_http, monkeypatch
    ):
        monkeypatch.setattr(
            "src.services.model_transformations.transform_model_id",
            lambda model, provider, *a, **k: "gpt-4o",
        )
        resp = _post(app_client, model="gpt-4o-mini", stream=True)
        assert "model_substitution_refused" in resp.text, resp.text
        assert intercepted_http == []

    @pytest.fixture
    def registry_with_a_substitute(self, monkeypatch):
        from src.services.multi_provider_registry import ProviderConfig

        hop = ProviderConfig(name="openai", model_id="gpt-4o", priority=1)
        model = SimpleNamespace(get_enabled_providers=lambda: [hop])
        registry = SimpleNamespace(get_model=lambda m: model, select_provider=lambda m: hop)
        execute = Mock(return_value={"success": False, "error": "should not be reached"})
        selector = SimpleNamespace(registry=registry, execute_with_failover=execute)
        monkeypatch.setattr("src.handlers.chat_handler.get_selector", lambda: selector)
        return execute

    @pytest.mark.parametrize("stream", [False, True])
    def test_registry_hop_serving_another_model_is_refused(
        self, app_client, validator_env, intercepted_http, registry_with_a_substitute, stream
    ):
        resp = _post(app_client, model="gpt-4o-mini", stream=stream)
        assert "model_substitution_refused" in resp.text, resp.text
        assert intercepted_http == []
        # Refused BEFORE the selector runs: a refusal must not be recorded as a
        # provider failure (that would trip the hop's circuit breaker for everyone).
        assert registry_with_a_substitute.call_count == 0
