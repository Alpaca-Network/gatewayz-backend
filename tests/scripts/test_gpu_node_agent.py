"""Tests for scripts/gpu_node_agent.py -- real eth_account for signature
verification (no mocks; see CLAUDE.md's GitNexus/eth_account rules and the
M1 lesson referenced in src/security/wallet_signature.py), httpx.MockTransport
for HTTP so nothing touches the network (gatewayz-backend#2267).
"""

import argparse
import json
import time

import httpx
import pytest
from eth_account import Account

from scripts.gpu_node_agent import (
    ATTESTATION_HEADER,
    BILLING_REF_HEADER,
    build_attestation,
    build_heartbeat_payload,
    hash_prompt,
    hash_response,
    heartbeat_once,
    next_backoff,
    probe_local_server,
    run_loop,
    send_heartbeat,
    sign_message,
    start_attest_proxy,
)
from src.security.wallet_signature import verify_wallet_signature
from src.services.gpu import hashing as backend_hashing

# ---------------------------------------------------------------------------
# Heartbeat payload shape
# ---------------------------------------------------------------------------


def test_heartbeat_payload_shape_unsigned():
    payload = build_heartbeat_payload(models=["llama-3.1-8b-instruct"], outstanding=3, node_id="42")
    assert payload == {
        "load": {"outstanding": 3},
        "models": ["llama-3.1-8b-instruct"],
    }


def test_heartbeat_payload_includes_gpu_util_and_version_when_given():
    payload = build_heartbeat_payload(
        models=["m"], outstanding=0, node_id="42", version="1.0.0", gpu_util_pct=87.5
    )
    assert payload["load"] == {"outstanding": 0, "gpu_util_pct": 87.5}
    assert payload["version"] == "1.0.0"


def test_heartbeat_payload_signature_shape_and_verifies_with_backend_function():
    """The signature travels as {"ts": <int>, "value": "0x..."} -- our own
    design decision (spec names the signed message but not the wire shape).
    Verify end-to-end with the REAL src.security.wallet_signature function
    the backend will use, not a mock -- a fake that accepts any signature
    proves nothing (see CLAUDE.md / M1 lesson).
    """
    account = Account.create()
    now = 1_800_000_000.0

    payload = build_heartbeat_payload(
        models=["m"], outstanding=0, node_id="42", account=account, now=now
    )

    sig = payload["signature"]
    assert sig["ts"] == 1_800_000_000
    assert sig["value"].startswith("0x")

    message = "gatewayz-heartbeat:42:1800000000"
    assert verify_wallet_signature(account.address, message, sig["value"]) is True


def test_heartbeat_payload_signature_rejects_wrong_node_id():
    account = Account.create()
    payload = build_heartbeat_payload(
        models=["m"], outstanding=0, node_id="42", account=account, now=1_800_000_000.0
    )
    sig = payload["signature"]
    # Signature was made for node_id "42" -- verifying against a different
    # node id's message must fail, proving node_id is actually bound in.
    wrong_message = "gatewayz-heartbeat:99:1800000000"
    assert verify_wallet_signature(account.address, wrong_message, sig["value"]) is False


def test_sign_message_matches_backend_verify_wallet_signature():
    account = Account.create()
    signature = sign_message(account, "hello world")
    assert verify_wallet_signature(account.address, "hello world", signature) is True


# ---------------------------------------------------------------------------
# probe_local_server / send_heartbeat over httpx.MockTransport (no network)
# ---------------------------------------------------------------------------


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_probe_local_server_reads_models_and_outstanding_from_metrics():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        if request.url.path == "/metrics":
            return httpx.Response(
                200,
                text="vllm:num_requests_running 2.0\nvllm:num_requests_waiting 1.0\n",
            )
        return httpx.Response(404)

    with _mock_client(handler) as client:
        models, outstanding = probe_local_server(client, "http://127.0.0.1:8000")

    assert models == ["a", "b"]
    assert outstanding == 3


def test_probe_local_server_defaults_outstanding_to_zero_without_metrics():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "a"}]})
        return httpx.Response(404)

    with _mock_client(handler) as client:
        models, outstanding = probe_local_server(client, "http://127.0.0.1:8000")

    assert models == ["a"]
    assert outstanding == 0


def test_probe_local_server_raises_on_unreachable_models_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            probe_local_server(client, "http://127.0.0.1:8000")


def test_send_heartbeat_posts_bearer_token_and_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"status": "active"}})

    with _mock_client(handler) as client:
        result = send_heartbeat(
            client,
            "https://api.gatewayz.ai",
            "42",
            "gw_node_abc",
            {"models": ["m"], "load": {"outstanding": 0}},
        )

    assert seen["path"] == "/gpu/nodes/42/heartbeat"
    assert seen["auth"] == "Bearer gw_node_abc"
    assert seen["body"] == {"models": ["m"], "load": {"outstanding": 0}}
    assert result == {"success": True, "data": {"status": "active"}}


def test_heartbeat_once_end_to_end_over_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        if request.url.path == "/metrics":
            return httpx.Response(404)
        if request.url.path == "/gpu/nodes/7/heartbeat":
            return httpx.Response(200, json={"success": True, "data": {}})
        return httpx.Response(404)

    args = argparse.Namespace(
        gateway="https://api.gatewayz.ai",
        node_token="gw_node_x",
        node_id="7",
        local_vllm="http://127.0.0.1:8000",
        version=None,
    )
    with _mock_client(handler) as client:
        result = heartbeat_once(client, args, account=None)

    assert result == {"success": True, "data": {}}


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_next_backoff_doubles_and_caps():
    assert next_backoff(30, base=30, cap=300) == 60
    assert next_backoff(60, base=30, cap=300) == 120
    assert next_backoff(200, base=30, cap=300) == 300
    assert next_backoff(300, base=30, cap=300) == 300


# ---------------------------------------------------------------------------
# --once
# ---------------------------------------------------------------------------


def test_run_loop_once_returns_after_single_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        if request.url.path == "/gpu/nodes/1/heartbeat":
            return httpx.Response(200, json={"success": True, "data": {}})
        return httpx.Response(404)

    args = argparse.Namespace(
        gateway="https://api.gatewayz.ai",
        node_token="t",
        node_id="1",
        local_vllm="http://127.0.0.1:8000",
        version=None,
        once=True,
        interval=30,
    )
    with _mock_client(handler) as client:
        run_loop(client, args, account=None)  # must not raise, must not loop

    assert calls["n"] == 3  # /v1/models + /metrics + heartbeat, exactly once each


def test_run_loop_once_raises_on_failure_without_retrying():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500)

    args = argparse.Namespace(
        gateway="https://api.gatewayz.ai",
        node_token="t",
        node_id="1",
        local_vllm="http://127.0.0.1:8000",
        version=None,
        once=True,
        interval=30,
    )
    with _mock_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            run_loop(client, args, account=None)

    assert attempts["n"] == 1  # --once must not retry


# ---------------------------------------------------------------------------
# Attestation hashing -- parity with the REAL backend implementation
# (src/services/gpu/hashing.py, merged by W-A2). Both sides independently
# chose json.dumps' default ensure_ascii=True; a non-ASCII fixture is the
# case that would actually catch either side drifting to ensure_ascii=False
# (see both modules' docstrings for the cross-repo contract).
# ---------------------------------------------------------------------------


def test_hash_prompt_matches_backend_hashing_module():
    messages = [{"role": "user", "content": "hi"}]
    assert hash_prompt(messages) == backend_hashing.hash_prompt(messages)


def test_hash_prompt_matches_backend_on_non_ascii_content():
    # Accents, CJK, and an emoji -- exactly the content ensure_ascii=False
    # would hash differently.
    messages = [{"role": "user", "content": "café 你好 \U0001f600"}]
    assert hash_prompt(messages) == backend_hashing.hash_prompt(messages)
    # And explicitly against canonical_json/sha256_hex, proving the
    # \uXXXX-escaping default (not just hash_prompt's behavior) matches.
    assert backend_hashing.canonical_json(messages) == json.dumps(
        messages, sort_keys=True, separators=(",", ":")
    )
    assert "café" not in backend_hashing.canonical_json(messages)  # escaped, not literal UTF-8


def test_hash_prompt_is_key_order_independent():
    a = [{"role": "user", "content": "hi"}]
    b = [{"content": "hi", "role": "user"}]
    assert hash_prompt(a) == hash_prompt(b) == backend_hashing.hash_prompt(a)


def test_hash_response_matches_backend_hashing_module():
    text = "The answer is 42."
    assert hash_response(text) == backend_hashing.hash_response(text)


def test_hash_response_matches_backend_on_non_ascii_content():
    text = "L'école dit 你好 \U0001f600"
    assert hash_response(text) == backend_hashing.hash_response(text)


def test_build_attestation_signs_the_documented_message_and_verifies():
    account = Account.create()
    request_body = json.dumps(
        {
            "model": "community/llama-3.1-8b-instruct",
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()
    response_body = json.dumps(
        {
            "model": "community/llama-3.1-8b-instruct",
            "choices": [{"message": {"content": "hello!"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
    ).encode()

    attestation = build_attestation(request_body, response_body, "billing-ref-123", account)

    prompt_hash = hash_prompt([{"role": "user", "content": "hi"}])
    response_hash = hash_response("hello!")
    expected_message = (
        f"billing-ref-123|community/llama-3.1-8b-instruct|{prompt_hash}|{response_hash}|5|3"
    )
    assert verify_wallet_signature(account.address, expected_message, attestation) is True


def test_build_attestation_returns_none_for_non_chat_exchange():
    account = Account.create()
    request_body = b"GET /v1/models has no body"
    response_body = json.dumps({"data": [{"id": "m"}]}).encode()
    assert build_attestation(request_body, response_body, "billing-ref-123", account) is None


def test_build_attestation_returns_none_when_request_not_json():
    account = Account.create()
    assert build_attestation(b"not json", b"{}", "billing-ref-123", account) is None


def test_build_attestation_uses_only_first_choice_for_n_greater_than_1():
    """community_adapter.py's _record_receipt only ever hashes
    raw.choices[0].message.content -- for n>1 the agent must match that
    exactly, not concatenate every choice (that produced a signature the
    backend could never verify -- the bug this test guards against).
    """
    account = Account.create()
    request_body = json.dumps(
        {
            "model": "community/llama-3.1-8b-instruct",
            "messages": [{"role": "user", "content": "hi"}],
            "n": 2,
        }
    ).encode()
    response_body = json.dumps(
        {
            "model": "community/llama-3.1-8b-instruct",
            "choices": [
                {"message": {"content": "first reply"}},
                {"message": {"content": "second reply"}},
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
    ).encode()

    attestation = build_attestation(request_body, response_body, "billing-ref-123", account)

    prompt_hash = hash_prompt([{"role": "user", "content": "hi"}])
    # Backend-equivalent: choices[0] only, via the real backend module.
    response_hash = backend_hashing.hash_response("first reply")
    expected_message = (
        f"billing-ref-123|community/llama-3.1-8b-instruct|{prompt_hash}|{response_hash}|5|3"
    )
    assert verify_wallet_signature(account.address, expected_message, attestation) is True

    # And explicitly: concatenating both choices would NOT verify -- proving
    # this is a real fix, not a test that would pass either way.
    wrong_response_hash = backend_hashing.hash_response("first replysecond reply")
    wrong_message = (
        f"billing-ref-123|community/llama-3.1-8b-instruct|{prompt_hash}|{wrong_response_hash}|5|3"
    )
    assert verify_wallet_signature(account.address, wrong_message, attestation) is False


# ---------------------------------------------------------------------------
# attest-proxy: buffered (non-streaming) vs. streaming passthrough.
# The upstream leg is httpx.MockTransport (no real HTTP to any upstream);
# the proxy itself is a real ThreadingHTTPServer bound to 127.0.0.1 (a real
# socket is unavoidable -- BaseHTTPRequestHandler parses off one -- but
# loopback-only, no network beyond this process).
# ---------------------------------------------------------------------------


def _run_attest_proxy(handler):
    """Start a real attest-proxy server on an OS-assigned port, backed by
    an httpx.MockTransport upstream. Returns (base_url, server); caller
    must call server.shutdown() + server.server_close().
    """
    account = Account.create()
    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    server = start_attest_proxy(0, "http://fake-upstream.invalid", account, client=mock_client)
    port = server.socket.getsockname()[1]
    return f"http://127.0.0.1:{port}", server, account


def test_attest_proxy_buffered_response_gets_attestation_header():
    def upstream_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get(BILLING_REF_HEADER) == "billing-ref-xyz"
        return httpx.Response(
            200,
            json={
                "model": "llama-3.1-8b-instruct",
                "choices": [{"message": {"content": "hello!"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )

    base_url, server, account = _run_attest_proxy(upstream_handler)
    try:
        with httpx.Client() as caller:
            resp = caller.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": "llama-3.1-8b-instruct",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={BILLING_REF_HEADER: "billing-ref-xyz"},
                timeout=5.0,
            )
    finally:
        server.shutdown()
        server.server_close()

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "hello!"
    attestation = resp.headers.get(ATTESTATION_HEADER.lower())
    assert attestation is not None

    prompt_hash = hash_prompt([{"role": "user", "content": "hi"}])
    response_hash = hash_response("hello!")
    expected_message = f"billing-ref-xyz|llama-3.1-8b-instruct|{prompt_hash}|{response_hash}|5|3"
    assert verify_wallet_signature(account.address, expected_message, attestation) is True


def test_attest_proxy_streaming_request_passes_through_unbuffered_without_attestation():
    sse_body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    base_url, server, _account = _run_attest_proxy(upstream_handler)
    try:
        with httpx.Client() as caller:
            resp = caller.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": "llama-3.1-8b-instruct",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers={BILLING_REF_HEADER: "billing-ref-xyz"},
                timeout=5.0,
            )
    finally:
        server.shutdown()
        server.server_close()

    assert resp.status_code == 200
    assert resp.content == sse_body  # forwarded byte-for-byte, not re-encoded
    assert ATTESTATION_HEADER.lower() not in {k.lower() for k in resp.headers}


def test_attest_proxy_detects_streaming_from_response_content_type_even_without_stream_flag():
    """The request didn't declare stream:true, but the upstream answered
    text/event-stream anyway -- the proxy must still relay unbuffered
    rather than trying (and failing) to treat it as a JSON document."""
    sse_body = b"data: unexpected-but-real-sse\n\n"

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse_body)

    base_url, server, _account = _run_attest_proxy(upstream_handler)
    try:
        with httpx.Client() as caller:
            resp = caller.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": "llama-3.1-8b-instruct",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={BILLING_REF_HEADER: "billing-ref-xyz"},
                timeout=5.0,
            )
    finally:
        server.shutdown()
        server.server_close()

    assert resp.content == sse_body
    assert ATTESTATION_HEADER.lower() not in {k.lower() for k in resp.headers}
