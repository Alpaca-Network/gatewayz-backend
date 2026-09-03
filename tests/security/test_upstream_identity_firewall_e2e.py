"""Real-route leak-canary for the upstream identity firewall (fix round 1).

Complements tests/security/test_upstream_identity_firewall.py (the breadth
layer: every provider client, called directly). This file is the depth
layer: TestClient -> POST /v1/chat/completions or /v1/messages -> real auth
dependency chain (get_optional_api_key, get_request_identity) -> real gates
-> real ChatInferenceHandler -> real provider client, for the three
highest-risk paths (Anthropic native, OpenAI native, one openai_compat
adapter), non-streaming AND streaming. Only catalog/pricing/credits/DB
writes are mocked -- ChatInferenceHandler and the provider clients are never
touched. The HTTP layer is intercepted at httpx.Client/AsyncClient.send, so
no real network call is made.

Sentinels are genuinely injected: a real user row (looked up by the real
identity-resolution code from the Authorization header), a real linked
wallet row, real request headers (X-Request-ID, X-Forwarded-For,
User-Agent), and a real client-supplied `user` field in the request body.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.config import Config

# --- Sentinels ---------------------------------------------------------------

SENTINEL_USER_ID = 424242
SENTINEL_EMAIL = "canary-424242@example.test"
SENTINEL_API_KEY = "gw_live_CANARY424242xyzsentinelkey"
SENTINEL_WALLET = "0xCA11A2000000000000000000000000000000CA11"
SENTINEL_REQUEST_ID = "canary-req-424242"
SENTINEL_IP = "203.0.113.77"
SENTINEL_UA = "CanaryUA/1.0"
SENTINEL_CLIENT_USER = "canary-end-user"

ALL_SENTINELS = [
    str(SENTINEL_USER_ID),
    SENTINEL_EMAIL,
    SENTINEL_WALLET.lower(),
    SENTINEL_REQUEST_ID,
    SENTINEL_IP,
    SENTINEL_UA,
    SENTINEL_CLIENT_USER,
]

SENTINEL_USER_ROW = {
    "id": SENTINEL_USER_ID,
    "email": SENTINEL_EMAIL,
    "api_key": SENTINEL_API_KEY,
    "auth_method": "email",
    "subscription_allowance": 1000.0,
    "purchased_credits": 1000.0,
    "credits": 1000.0,
    "environment_tag": "live",
}

SENTINEL_WALLET_ROW = {
    "wallet_address": SENTINEL_WALLET.lower(),
    "is_primary": True,
    "user_id": SENTINEL_USER_ID,
}


# --- HTTP interception (same fixture shape as the unit-level canary) --------

_OPENAI_STYLE_RESPONSE = {
    "id": "chatcmpl-canary",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "canary-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

_ANTHROPIC_STYLE_RESPONSE = {
    "id": "msg_canary",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


def _fake_response(request: httpx.Request) -> httpx.Response:
    body = (
        _ANTHROPIC_STYLE_RESPONSE
        if request.url.path.endswith("/v1/messages")
        else _OPENAI_STYLE_RESPONSE
    )
    return httpx.Response(200, json=body, request=request)


# Real model-provider hosts this test cares about. Only requests to one of
# these are captured and faked. Gatewayz's own Supabase REST calls for
# identity/billing/analytics legitimately DO carry the sentinel user_id --
# that's not a leak, see G1's "Gatewayz still knows who you are" carve-out --
# so they aren't asserted against, but they must not make a real network
# call either ("no real network calls in tests" applies file-wide, not just
# to provider hosts). Raising a synthetic ConnectError immediately (rather
# than letting a real DNS lookup fail on its own) also keeps this file fast
# and deterministic: every one of these DB calls is already wrapped in
# try/except by the application code, so failing them instantly is
# equivalent to production behavior, just without the multi-second local
# DNS-failure latency that made this file flaky under full `-n auto`
# parallelism (each worker's real failed lookups compounded).
_PROVIDER_HOSTS = {
    "api.openai.com",
    "api.anthropic.com",
    "api.deepinfra.com",
    "openrouter.ai",
}

# Starlette's TestClient IS an httpx.Client subclass talking to the ASGI app
# over an in-process ASGITransport -- it reaches httpx.Client.send() exactly
# like any real provider call would, so our patch below sees it too. It must
# be passed through to the REAL (unpatched) send, not faked or blocked:
# faking/blocking it would prevent the app from ever running, making every
# assertion in this file vacuous (this was caught and fixed during
# fix-round-1 review -- see the negative-control tests, which rely on this
# distinction to prove the app actually ran).
_TEST_CLIENT_HOSTS = {"testserver", "test"}


def _is_provider_request(request: httpx.Request) -> bool:
    return request.url.host in _PROVIDER_HOSTS


def _is_test_client_self_request(request: httpx.Request) -> bool:
    return request.url.host in _TEST_CLIENT_HOSTS


@pytest.fixture
def intercepted_http(monkeypatch):
    captured: list[httpx.Request] = []
    real_send = httpx.Client.send
    real_async_send = httpx.AsyncClient.send

    def fake_send(self, request, **kwargs):
        if _is_provider_request(request):
            captured.append(request)
            return _fake_response(request)
        if _is_test_client_self_request(request):
            return real_send(self, request, **kwargs)
        raise httpx.ConnectError("blocked by test: no real network calls", request=request)

    async def fake_async_send(self, request, **kwargs):
        if _is_provider_request(request):
            captured.append(request)
            return _fake_response(request)
        if _is_test_client_self_request(request):
            return await real_async_send(self, request, **kwargs)
        raise httpx.ConnectError("blocked by test: no real network calls", request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", fake_async_send)
    return captured


# --- Route-level environment: identity/catalog/pricing/credits/DB only -----


@pytest.fixture
def route_env(monkeypatch):
    """Mock every identity-lookup, gate, and DB-write dependency the real
    /v1/chat/completions and /v1/messages routes touch on the way to
    ChatInferenceHandler. The handler and provider clients are never mocked.

    Each patch target is the NAME AS BOUND in the importing module (Python's
    `from x import y` copies a reference at import time; patching `x.y`
    afterwards does not reach a module that already imported `y` directly),
    verified against the actual import statements in each file.
    """

    def _get_user(api_key):
        return dict(SENTINEL_USER_ROW) if api_key == SENTINEL_API_KEY else None

    # Identity resolution (src/security/identity.py's get_request_identity,
    # ChatInferenceHandler._initialize_user_context, and get_api_key's own
    # audit-logging lookup in src/security/deps.py) -- three separate `from
    # ... import get_user` bindings, each needs its own patch (see module
    # docstring above).
    monkeypatch.setattr("src.security.identity.get_user", _get_user)
    monkeypatch.setattr("src.handlers.chat_handler.get_user", _get_user)
    monkeypatch.setattr("src.security.deps.get_user", _get_user)
    # validate_api_key_security's legacy-fallback branch does a LOCAL
    # `from src.db.users import get_user` at call time (security.py:217) --
    # patch the canonical source, which that fresh import always re-resolves.
    monkeypatch.setattr("src.db.users.get_user", _get_user)
    monkeypatch.setattr(
        "src.db.user_wallets.get_wallets_for_user",
        lambda user_id: [dict(SENTINEL_WALLET_ROW)] if user_id == SENTINEL_USER_ID else [],
    )

    # ChatInferenceHandler's own billing DB writes (post-provider-call).
    monkeypatch.setattr("src.handlers.chat_handler.deduct_credits", Mock(return_value=None))
    monkeypatch.setattr("src.handlers.chat_handler.record_usage", Mock(return_value=None))
    monkeypatch.setattr(
        "src.handlers.chat_handler.save_chat_completion_request_with_cost",
        Mock(return_value=None),
    )
    monkeypatch.setattr(
        "src.handlers.chat_handler.estimate_and_check_credits",
        Mock(return_value={"allowed": True, "capped_max_tokens": None, "max_cost": 0.0}),
    )
    # chat.py's own credit precheck (route-level, line ~603) imports this
    # fresh from the canonical module on every call (a local import inside
    # the function body, not a module-level binding) -- patch the source.
    monkeypatch.setattr(
        "src.services.billing.credit_precheck.estimate_and_check_credits",
        Mock(return_value={"allowed": True, "capped_max_tokens": None, "max_cost": 0.0}),
    )
    # Post-response cost calculation (ChatInferenceHandler._charge_user) does
    # a REAL pricing catalog lookup for the served model -- gpt-4o-mini and
    # claude-3-5-sonnet are flagged "high value" (must not silently under-bill)
    # and raise ValueError when no catalog/DB pricing exists, which it doesn't
    # in this test environment. Mock the lookup itself, not the handler logic
    # that calls it.
    monkeypatch.setattr(
        "src.handlers.chat_handler.get_model_pricing",
        Mock(return_value={"prompt": 0.0, "completion": 0.0}),
    )
    monkeypatch.setattr(
        "src.handlers.chat_handler.calculate_cost_split",
        Mock(return_value=(0.0, 0.0, 0.0)),
    )

    # Route-level gates and precheck DB reads.
    monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", False, raising=False)
    monkeypatch.setattr(
        "src.routes.chat.get_model_pricing_async",
        AsyncMock(return_value={"prompt": 0.0, "completion": 0.0}),
    )
    monkeypatch.setattr(
        "src.routes.chat._ensure_plan_capacity", AsyncMock(return_value={"allowed": True})
    )
    monkeypatch.setattr("src.routes.chat._handle_credits_and_usage", AsyncMock(return_value=0.0))
    monkeypatch.setattr(
        "src.routes.chat._handle_credits_and_usage_with_fallback",
        AsyncMock(return_value=(0.0, True)),
    )
    monkeypatch.setattr(
        "src.routes.chat._record_inference_metrics_and_health", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("src.routes.chat.capture_model_health", Mock(return_value=None))
    monkeypatch.setattr(
        "src.routes.chat.save_chat_completion_request_with_cost", Mock(return_value=None)
    )
    monkeypatch.setattr("src.db.api_keys.increment_api_key_usage", Mock(return_value=None))
    monkeypatch.setattr("src.db.plans.enforce_plan_limits", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(
        "src.services.trial_validation.validate_trial_access",
        lambda *a, **k: {"is_valid": True, "is_trial": False, "is_expired": False},
    )
    monkeypatch.setattr(
        "src.utils.api_key_lookup.get_api_key_id_with_retry", AsyncMock(return_value=1)
    )

    mock_rl_result = Mock(
        allowed=True,
        reason="",
        retry_after=None,
        remaining_requests=9999,
        remaining_tokens=999999,
        # get_rate_limit_headers() reads these via getattr(..., default) --
        # a bare Mock() auto-creates unset attributes instead of raising, so
        # they must be set explicitly to real numbers or that comparison
        # (`limit_requests > 0`) blows up with a Mock-vs-int TypeError.
        ratelimit_limit_requests=250,
        ratelimit_reset_requests=60,
        ratelimit_limit_tokens=10000,
        ratelimit_reset_tokens=60,
        burst_window_description="",
    )
    mock_rate_mgr = Mock()
    mock_rate_mgr.check_rate_limit = AsyncMock(return_value=mock_rl_result)
    mock_rate_mgr.release_concurrency = AsyncMock(return_value=None)
    monkeypatch.setattr("src.services.rate_limiting.get_rate_limit_manager", lambda: mock_rate_mgr)


@pytest.fixture
def app_client():
    from src.main import create_app

    return TestClient(create_app())


def _headers():
    return {
        "Authorization": f"Bearer {SENTINEL_API_KEY}",
        "X-Request-ID": SENTINEL_REQUEST_ID,
        "X-Forwarded-For": SENTINEL_IP,
        "User-Agent": SENTINEL_UA,
    }


def _assert_clean(requests: list[httpx.Request], label: str) -> None:
    assert requests, f"{label}: no outbound HTTP request was captured"
    for request in requests:
        haystack = "\n".join(
            [
                str(request.url),
                "\n".join(f"{k}:{v}" for k, v in request.headers.items()),
                (request.content or b"").decode("utf-8", "ignore"),
            ]
        ).lower()
        for sentinel in ALL_SENTINELS:
            assert sentinel.lower() not in haystack, (
                f"{label}: sentinel {sentinel!r} leaked to {request.url} "
                f"(headers={dict(request.headers)}, body={request.content!r})"
            )


# --- The three highest-risk paths, non-stream + stream ----------------------


class TestChatCompletionsRealRoute:
    """POST /v1/chat/completions through the real pipeline."""

    def _payload(self, model, provider, stream):
        return {
            "model": model,
            "provider": provider,
            "messages": [{"role": "user", "content": "hi"}],
            "user": SENTINEL_CLIENT_USER,
            "stream": stream,
            "auto_web_search": False,
        }

    @pytest.mark.parametrize("stream", [False, True])
    def test_anthropic_native(self, app_client, route_env, intercepted_http, monkeypatch, stream):
        monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "sk-ant-canary", raising=False)
        resp = app_client.post(
            "/v1/chat/completions",
            json=self._payload("claude-3-5-sonnet-20241022", "anthropic", stream),
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        _assert_clean(intercepted_http, f"anthropic-native stream={stream}")

    @pytest.mark.parametrize("stream", [False, True])
    def test_openai_native(self, app_client, route_env, intercepted_http, monkeypatch, stream):
        monkeypatch.setattr(Config, "OPENAI_API_KEY", "sk-canary", raising=False)
        resp = app_client.post(
            "/v1/chat/completions",
            json=self._payload("gpt-4o-mini", "openai", stream),
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        _assert_clean(intercepted_http, f"openai-native stream={stream}")

    @pytest.mark.parametrize("stream", [False, True])
    def test_openai_compat_deepinfra(
        self, app_client, route_env, intercepted_http, monkeypatch, stream
    ):
        monkeypatch.setattr(Config, "DEEPINFRA_API_KEY", "dk-canary", raising=False)
        resp = app_client.post(
            "/v1/chat/completions",
            json=self._payload("meta-llama/Llama-3.1-8B-Instruct", "deepinfra", stream),
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        _assert_clean(intercepted_http, f"deepinfra stream={stream}")

    def test_community_adapter(self, app_client, route_env, intercepted_http, monkeypatch):
        """The community provider (gatewayz-backend#2262 #2265) fans out to
        one operator-run node instead of a single platform host -- same
        canary, plus proof it must pass like every other provider (spec §4
        item 5)."""
        import importlib
        import sys
        import types

        import src.handlers.provider_registry as provider_registry_module

        node = {
            "id": "canary-node",
            "provider_id": "canary-provider",
            "endpoint_url": "https://node1.community.example.test/v1",
            "endpoint_api_key_encrypted": "",  # decrypt_api_key not exercised (no encrypted key)
            "name": "canary-node",
        }
        fake_gpu = types.ModuleType("src.db.gpu")
        fake_gpu.select_nodes_for_model = lambda model: [node]
        fake_gpu.get_provider = lambda provider_id: None
        fake_gpu.adjust_outstanding = lambda node_id, delta: None
        monkeypatch.setitem(sys.modules, "src.db.gpu", fake_gpu)
        monkeypatch.setattr(
            "src.services.providers.community_adapter._decrypt_node_key", lambda enc: "unused"
        )
        monkeypatch.setattr(
            "tests.security.test_upstream_identity_firewall_e2e._PROVIDER_HOSTS",
            _PROVIDER_HOSTS | {"node1.community.example.test"},
        )

        monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
        importlib.reload(provider_registry_module)
        try:
            resp = app_client.post(
                "/v1/chat/completions",
                json=self._payload("community/llama-3.1-8b-instruct", "community", False),
                headers=_headers(),
            )
            assert resp.status_code == 200, resp.text
            _assert_clean(intercepted_http, "community")

            # N7 scoped exception to G1 (see docs/security/ANONYMITY_THREAT_MODEL.md
            # and W-E's cross-repo contract note): the server-minted billing_ref
            # IS forwarded to the node, as X-Gatewayz-Request-Id -- but it must be
            # the ONLY new field the community path adds, never a vector for any
            # other identity leaking through.
            node_requests = [
                r for r in intercepted_http if r.url.host == "node1.community.example.test"
            ]
            assert node_requests, "no request captured to the community node"
            for request in node_requests:
                header_names = {k.lower() for k in request.headers}
                gatewayz_headers = {h for h in header_names if h.startswith("x-gatewayz")}
                assert gatewayz_headers == {"x-gatewayz-request-id"}, (
                    f"expected exactly one x-gatewayz-* header (x-gatewayz-request-id), "
                    f"got {gatewayz_headers}"
                )
                billing_ref = request.headers["x-gatewayz-request-id"]
                # A real per-request uuid4, never the client's own X-Request-ID
                # (SENTINEL_REQUEST_ID) or any other client/user-identifying value.
                assert billing_ref not in ALL_SENTINELS
                uuid.UUID(billing_ref)  # raises if not a valid uuid
        finally:
            # Force the module back to the real production default (off)
            # deterministically -- monkeypatch's own teardown of
            # COMMUNITY_ROUTING_ENABLED runs after this test function
            # returns, which would be too late for this reload to see it.
            monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", False, raising=False)
            importlib.reload(provider_registry_module)

    def test_negative_control_anthropic_metadata_user_id_leaks_when_scrub_disabled(
        self, app_client, route_env, intercepted_http, monkeypatch
    ):
        """Proof the real route's canary can fail: with scrub_upstream_kwargs
        monkeypatched to identity (as if chat_handler.py never called it), the
        client's `user` value reaches Anthropic's real metadata.user_id via the
        REAL route.
        """
        monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "sk-ant-canary", raising=False)
        monkeypatch.setattr(
            "src.handlers.chat_handler.scrub_upstream_kwargs",
            lambda kwargs, **_: kwargs,
        )
        resp = app_client.post(
            "/v1/chat/completions",
            json=self._payload("claude-3-5-sonnet-20241022", "anthropic", False),
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        bodies = [r.content.decode() for r in intercepted_http]
        assert any(
            SENTINEL_CLIENT_USER in b for b in bodies
        ), "negative control did not leak -- real-route harness cannot detect a leak"


class TestMessagesRealRoute:
    """POST /v1/messages (Anthropic-compat) through the real pipeline --
    shares chat_completions' pipeline (routes/messages.py's own docstring:
    "there is exactly one inference path in this service, and this endpoint
    does not fork it"). `metadata.user_id` is the native vector this route
    would carry if it forwarded the client's `metadata` field.
    """

    def test_anthropic_native_via_messages_endpoint(
        self, app_client, route_env, intercepted_http, monkeypatch
    ):
        """Was xfail (chat.py:350 AttributeError, every /v1/messages request
        500'd) until hotfix #2281 (179ca948) taught create_message() to
        resolve and pass `identity=` explicitly. Now a real passing
        assertion through the real, unmocked route."""
        monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "sk-ant-canary", raising=False)
        resp = app_client.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"user_id": "canary-meta"},
            },
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        # AnthropicMessagesRequest.metadata IS a declared schema field (no 422)
        # but never read into ProxyRequest/optional -- see
        # test_messages_metadata_field_is_schema_accepted_but_dropped below.
        haystack = "\n".join(r.content.decode() for r in intercepted_http)
        assert "canary-meta" not in haystack
        _assert_clean(intercepted_http, "anthropic-native via /v1/messages")

    def test_negative_control_scrub_disabled_still_clean_via_messages(
        self, app_client, route_env, intercepted_http, monkeypatch
    ):
        """Not a leak-detecting negative control like chat.py's (there is no
        `scrub_upstream_kwargs` call left to disable that would change this
        route's outcome): AnthropicMessagesRequest.metadata never reaches
        ProxyRequest/optional/kwargs at all (see the structural test below),
        so disabling scrub is a no-op here. Asserting that explicitly
        documents the two-independent-reasons story for /v1/messages --
        schema-layer drop AND the scrub boundary, not just the boundary --
        rather than silently relying on one of them.
        """
        monkeypatch.setattr(Config, "ANTHROPIC_API_KEY", "sk-ant-canary", raising=False)
        monkeypatch.setattr(
            "src.handlers.chat_handler.scrub_upstream_kwargs",
            lambda kwargs, **_: kwargs,
        )
        resp = app_client.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {"user_id": "canary-meta"},
            },
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text
        haystack = "\n".join(r.content.decode() for r in intercepted_http)
        assert "canary-meta" not in haystack

    def test_messages_metadata_field_is_schema_accepted_but_dropped(self):
        """Documents exactly what the schema layer does with `metadata`: it IS
        a declared field on AnthropicMessagesRequest (accepted, no 422), but
        transform_anthropic_to_openai() never reads it into ProxyRequest, so
        it cannot reach `optional`/kwargs regardless of the scrub boundary --
        the field is silently dropped one layer before scrub_upstream_kwargs
        would ever see it. This is itself part of the guarantee -- verified
        structurally (not blocked by the xfail above) so a future change that
        starts threading `metadata` through doesn't silently reopen this.
        """
        import inspect

        from src.services.providers.anthropic_transformer import transform_anthropic_to_openai

        params = inspect.signature(transform_anthropic_to_openai).parameters
        assert "metadata" not in params
