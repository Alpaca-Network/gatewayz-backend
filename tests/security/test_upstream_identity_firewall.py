"""Leak-canary test for the upstream identity firewall (#2257 #2259).

This is the executable form of G1 in docs/security/ANONYMITY_THREAT_MODEL.md:
"No identity leaves Gatewayz toward a provider." It must stay green.

Two layers:

1. A structural guard binding chat_handler.py's two kwargs-assembly sites to
   scrub_upstream_kwargs -- a regression where someone deletes the call fails
   here even if the functional tests below happen to still pass.
2. A functional canary, per provider client that makes outbound HTTP: drive
   the real client function with kwargs that carry a sentinel identity value,
   intercept the HTTP layer (httpx.Client.send / httpx.AsyncClient.send --
   every SDK used here, including the Cerebras SDK, is httpx-based), and
   assert the sentinel never appears in the outbound URL, headers, or body.
   Each provider also gets a negative control: the same call with UNSCRUBBED
   kwargs, proving the harness can actually detect a leak and that these
   provider clients really do forward whatever they're given.

No real network calls are made anywhere in this file.

Coverage: every provider wired into chat_handler.py's dispatch path
(src/handlers/provider_registry.py's PROVIDER_ROUTING, plus openrouter and
anthropic, which are imported directly) is exercised. Three modules are
explicitly skipped with reasons (see SKIPPED_PROVIDERS below) rather than
silently omitted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from src.config import Config
from src.services.upstream.anonymize import (
    FORBIDDEN_OUTBOUND_HEADER_NAMES,
    pseudonym,
    scrub_upstream_kwargs,
)

CHAT_HANDLER_PY = Path(__file__).resolve().parents[2] / "src" / "handlers" / "chat_handler.py"

MESSAGES = [{"role": "user", "content": "hi"}]

# --- Sentinel identity values (spec §3.2) -----------------------------------

SENTINEL_USER = "canary-end-user"
SENTINEL_REQUEST_ID = "canary-req-424242"
SENTINEL_EMAIL = "canary-424242@example.test"
SENTINEL_WALLET = "0xCA11A2000000000000000000000000000000CA11"

ALL_SENTINELS = [SENTINEL_USER, SENTINEL_REQUEST_ID, SENTINEL_EMAIL, SENTINEL_WALLET.lower()]


# --- Structural guard: both chat_handler.py kwargs-assembly sites must call
# scrub_upstream_kwargs. Mirrors the AST-based convention used for chat.py
# invariants in tests/routes/test_chat_identity.py. -------------------------


def test_both_kwargs_assembly_sites_call_scrub_upstream_kwargs():
    tree = ast.parse(CHAT_HANDLER_PY.read_text())
    call_sites = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "scrub_upstream_kwargs"
    ]
    assert len(call_sites) == 2, (
        "expected exactly two scrub_upstream_kwargs(...) call sites in "
        f"chat_handler.py (non-stream + stream kwargs assembly), found {len(call_sites)}"
    )


# --- HTTP interception -------------------------------------------------------

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


@pytest.fixture
def intercepted_http(monkeypatch):
    """Patch every httpx transport used by any provider client (raw httpx,
    the OpenAI SDK, and the Cerebras SDK, which is also httpx-based) and
    return the list of captured outbound requests.
    """
    captured: list[httpx.Request] = []

    def fake_send(self, request, **kwargs):
        captured.append(request)
        return _fake_response(request)

    async def fake_async_send(self, request, **kwargs):
        captured.append(request)
        return _fake_response(request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", fake_async_send)
    return captured


def _assert_clean(requests: list[httpx.Request], sentinels: list[str], label: str) -> None:
    assert requests, f"{label}: no outbound HTTP request was captured"
    for request in requests:
        haystack = "\n".join(
            [
                str(request.url),
                "\n".join(f"{k}:{v}" for k, v in request.headers.items()),
                (request.content or b"").decode("utf-8", "ignore"),
            ]
        ).lower()
        for sentinel in sentinels:
            assert sentinel.lower() not in haystack, (
                f"{label}: sentinel {sentinel!r} leaked to {request.url} "
                f"(headers={dict(request.headers)}, body={request.content!r})"
            )
        leaked_headers = FORBIDDEN_OUTBOUND_HEADER_NAMES & {k.lower() for k in request.headers}
        assert not leaked_headers, f"{label}: forbidden headers present: {leaked_headers}"


def _assert_leaks(requests: list[httpx.Request], sentinel: str, label: str) -> None:
    """Negative control: prove the harness can detect a real leak."""
    found = any(
        sentinel.lower()
        in "\n".join(
            [str(r.url), str(dict(r.headers)), (r.content or b"").decode("utf-8", "ignore")]
        ).lower()
        for r in requests
    )
    assert found, f"{label}: negative control did not leak -- test harness cannot detect a leak"


# --- Provider call sites -----------------------------------------------------
# Each entry: (label, callable(**kwargs) -> None, config patches to apply).
# The callable must make exactly one non-streaming outbound HTTP call.


def _patch_keys(monkeypatch, **attrs):
    for name, value in attrs.items():
        monkeypatch.setattr(Config, name, value, raising=False)


def _call_openai(monkeypatch, **kwargs):
    _patch_keys(monkeypatch, OPENAI_API_KEY="sk-canary")
    from src.services.providers.openai_client import make_openai_request

    make_openai_request(MESSAGES, "gpt-4o-mini", **kwargs)


def _call_xai(monkeypatch, **kwargs):
    _patch_keys(monkeypatch, XAI_API_KEY="xai-canary")
    from src.services.providers.xai_client import make_xai_request_openai

    make_xai_request_openai(MESSAGES, "grok-beta", **kwargs)


def _call_cerebras(monkeypatch, **kwargs):
    _patch_keys(monkeypatch, CEREBRAS_API_KEY="csk-canary")
    from src.services.providers.cerebras_client import make_cerebras_request_openai

    make_cerebras_request_openai(MESSAGES, "llama3.1-8b", **kwargs)


def _call_featherless(monkeypatch, **kwargs):
    _patch_keys(monkeypatch, FEATHERLESS_API_KEY="fw-canary")
    from src.services.providers.featherless_client import make_featherless_request_openai

    make_featherless_request_openai(MESSAGES, "some-model", **kwargs)


def _call_openrouter(monkeypatch, **kwargs):
    _patch_keys(monkeypatch, OPENROUTER_API_KEY="or-canary")
    from src.services.providers.openrouter_client import make_openrouter_request_openai

    make_openrouter_request_openai(MESSAGES, "openrouter/auto", **kwargs)


def _call_anthropic_native(monkeypatch, **kwargs):
    _patch_keys(monkeypatch, ANTHROPIC_API_KEY="sk-ant-canary")
    monkeypatch.setattr(
        "src.services.providers.anthropic_client._use_native_transport", lambda: True
    )
    from src.services.providers.anthropic_client import make_anthropic_request

    make_anthropic_request(MESSAGES, "claude-3-5-sonnet-20241022", **kwargs)


def _call_anthropic_compat(monkeypatch, **kwargs):
    _patch_keys(monkeypatch, ANTHROPIC_API_KEY="sk-ant-canary")
    monkeypatch.setattr(
        "src.services.providers.anthropic_client._use_native_transport", lambda: False
    )
    from src.services.providers.anthropic_client import make_anthropic_request

    make_anthropic_request(MESSAGES, "claude-3-5-sonnet-20241022", **kwargs)


def _call_adapter(slug: str):
    def _call(monkeypatch, **kwargs):
        from src.services.providers.adapter_configs import ADAPTER_CONFIGS, ADAPTERS

        _patch_keys(monkeypatch, **{ADAPTER_CONFIGS[slug].api_key_env: "adapter-canary"})
        ADAPTERS[slug].request(MESSAGES, "some-model", **kwargs)

    return _call


def _call_community(monkeypatch, **kwargs):
    """One fake node standing in for the community provider (gatewayz-backend
    #2262 #2265): community_request() calls a per-node client built from
    src.db.gpu.select_nodes_for_model() (owned by the parallel W-A1
    workstream and not present on this branch), so a fake module is
    installed the same way tests/services/test_community_adapter.py does.
    """
    import sys
    import types

    from src.services.providers import community_adapter

    community_adapter.clear_adapter_cache()
    node = {
        "id": "canary-node",
        "provider_id": "canary-provider",
        "endpoint_url": "https://node1.community.example.test/v1",
        "endpoint_api_key_encrypted": "",
        "name": "canary-node",
    }
    fake_gpu = types.ModuleType("src.db.gpu")
    fake_gpu.select_nodes_for_model = lambda model: [node]
    fake_gpu.get_provider = lambda provider_id: None
    fake_gpu.adjust_outstanding = lambda node_id, delta: None
    monkeypatch.setitem(sys.modules, "src.db.gpu", fake_gpu)
    monkeypatch.setattr(community_adapter, "_decrypt_node_key", lambda enc: "unused")
    # community_request() writes a provider_work receipt via a real Supabase
    # client -- irrelevant to this canary and unreachable in tests, so it's
    # skipped rather than letting its own try/except swallow a slow DNS
    # failure per call.
    monkeypatch.setattr("src.db.gpu_work.record_work", lambda **_: None)

    community_adapter.community_request(MESSAGES, "community/canary-model", **kwargs)


# Bespoke provider clients (direct HTTP or a stainless SDK, both httpx-based).
BESPOKE_PROVIDERS = {
    "openai": _call_openai,
    "xai": _call_xai,
    "cerebras": _call_cerebras,
    "featherless": _call_featherless,
    "openrouter": _call_openrouter,
    "anthropic-native": _call_anthropic_native,
    "anthropic-compat": _call_anthropic_compat,
}

# openai_compat adapter: one call site (openai_compat.py) serves every slug
# in ADAPTER_CONFIGS -- parametrize over all of them since it's mechanical.
ADAPTER_SLUGS = [
    "deepinfra",
    "together",
    "fireworks",
    "groq",
    "zai",
    "deepseek",
    "moonshot",
    "minimax",
    "xiaomi",
    "meta",
]

ALL_PROVIDERS = {
    **BESPOKE_PROVIDERS,
    **{f"adapter:{s}": _call_adapter(s) for s in ADAPTER_SLUGS},
    "community": _call_community,
}

# Modules that make outbound HTTP but are not covered above, with reasons.
# See tests/security/README section below (also in the PR body) for detail.
SKIPPED_PROVIDERS = {
    "google-vertex": (
        "make_google_vertex_request_openai/_prepare_vertex_contents never read "
        "kwargs['user'] or kwargs['metadata'] at all (confirmed by source read) -- "
        "no leak vector exists structurally. Exercising it needs ADC/protobuf "
        "setup unrelated to identity scrubbing."
    ),
    "alibaba-cloud": (
        "Same generic client.chat.completions.create(**kwargs) pattern proven by "
        "openai/xai/cerebras above (kwargs forwarded verbatim), but wrapped in "
        "region-failover + Redis-backed region-memory logic that would need "
        "unrelated mocking to exercise deterministically."
    ),
    "novita": (
        "Not wired into src.handlers.provider_registry.PROVIDER_FUNCTIONS / "
        "PROVIDER_ROUTING -- novita_client.py is catalog/image-only, not part "
        "of the chat inference dispatch path."
    ),
}


@pytest.mark.parametrize("label", sorted(ALL_PROVIDERS))
def test_no_sentinel_reaches_provider(label, monkeypatch, intercepted_http):
    """The real fix: kwargs that already went through scrub_upstream_kwargs
    (exactly what chat_handler.py now does) leave no sentinel in the outbound
    request, for every provider client in the dispatch path.
    """
    call = ALL_PROVIDERS[label]
    raw_kwargs = {"temperature": 0.1, "user": SENTINEL_USER}
    clean_kwargs = scrub_upstream_kwargs(raw_kwargs)

    call(monkeypatch, **clean_kwargs)

    _assert_clean(intercepted_http, ALL_SENTINELS, label)


@pytest.mark.parametrize("label", sorted(ALL_PROVIDERS))
def test_negative_control_unscrubbed_kwargs_do_leak(label, monkeypatch, intercepted_http):
    """Proof the harness can fail: the SAME provider call with the client's
    raw `user` value (i.e. as if scrub_upstream_kwargs were never called)
    does leak it upstream. If this test ever fails, the canary above is not
    actually testing anything.
    """
    call = ALL_PROVIDERS[label]
    raw_kwargs = {"temperature": 0.1, "user": SENTINEL_USER}

    call(monkeypatch, **raw_kwargs)

    _assert_leaks(intercepted_http, SENTINEL_USER, label)


@pytest.mark.parametrize("label,reason", sorted(SKIPPED_PROVIDERS.items()))
def test_skipped_providers_documented(label, reason):
    pytest.skip(f"{label}: {reason}")


def test_community_billing_ref_header_is_the_only_new_field(monkeypatch, intercepted_http):
    """N7 scoped exception to G1 (docs/security/ANONYMITY_THREAT_MODEL.md):
    community/<model> forwards billing_ref to the node as
    X-Gatewayz-Request-Id (W-E's attest-proxy needs it -- see
    scripts/gpu_node_agent.py's cross-repo contract note). This must be the
    ONLY new field the community path adds -- never a vector for any other
    identity to leak alongside it.
    """
    clean_kwargs = scrub_upstream_kwargs({"temperature": 0.1, "user": SENTINEL_USER})

    _call_community(monkeypatch, _gatewayz_billing_ref="billing-ref-canary-123", **clean_kwargs)

    assert intercepted_http, "no outbound request captured"
    for request in intercepted_http:
        header_names = {k.lower() for k in request.headers}
        gatewayz_headers = {h for h in header_names if h.startswith("x-gatewayz")}
        assert gatewayz_headers == {"x-gatewayz-request-id"}
        assert request.headers["x-gatewayz-request-id"] == "billing-ref-canary-123"
    _assert_clean(intercepted_http, ALL_SENTINELS, "community (billing_ref scoped exception)")


# --- Pseudonym mode ----------------------------------------------------------


class TestPseudonymMode:
    """UPSTREAM_ABUSE_PSEUDONYM=true: outbound `user` is the HMAC pseudonym of
    billing_ref, never the client's value and never the client's request id.
    """

    def test_anthropic_native_metadata_user_id_is_pseudonym(self, monkeypatch, intercepted_http):
        monkeypatch.setattr(Config, "UPSTREAM_ABUSE_PSEUDONYM", True, raising=False)
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "canary-secret", raising=False)
        kwargs = scrub_upstream_kwargs(
            {"user": SENTINEL_USER, "temperature": 0.1}, billing_ref="ref-1"
        )

        _call_anthropic_native(monkeypatch, **kwargs)

        body = intercepted_http[-1].content.decode()
        assert pseudonym("ref-1") in body
        assert SENTINEL_USER not in body
        assert SENTINEL_REQUEST_ID not in body

    def test_openai_compat_user_is_pseudonym(self, monkeypatch, intercepted_http):
        monkeypatch.setattr(Config, "UPSTREAM_ABUSE_PSEUDONYM", True, raising=False)
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "canary-secret", raising=False)
        kwargs = scrub_upstream_kwargs(
            {"user": SENTINEL_USER, "temperature": 0.1}, billing_ref="ref-1"
        )

        _call_adapter("deepinfra")(monkeypatch, **kwargs)

        body = intercepted_http[-1].content.decode()
        assert pseudonym("ref-1") in body
        assert SENTINEL_USER not in body

    def test_different_billing_refs_produce_different_pseudonyms(
        self, monkeypatch, intercepted_http
    ):
        monkeypatch.setattr(Config, "UPSTREAM_ABUSE_PSEUDONYM", True, raising=False)
        monkeypatch.setattr(Config, "UPSTREAM_PSEUDONYM_SECRET", "canary-secret", raising=False)
        assert pseudonym("ref-1") != pseudonym("ref-2")
