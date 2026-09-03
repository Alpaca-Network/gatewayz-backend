"""Tests for scripts/gpu_node_agent.py -- real eth_account for signature
verification (no mocks; see CLAUDE.md's GitNexus/eth_account rules and the
M1 lesson referenced in src/security/wallet_signature.py), httpx.MockTransport
for HTTP so nothing touches the network (gatewayz-backend#2267).
"""

import argparse
import hashlib
import json
import time

import httpx
import pytest
from eth_account import Account

from scripts.gpu_node_agent import (
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
)
from src.security.wallet_signature import verify_wallet_signature

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
# Attestation hashing -- vendored expected values (src/services/gpu/hashing.py
# does not exist yet as of this PR; W-A2 has not merged. These hand-compute
# the canonical rule this file's docstring commits to, independent of the
# implementation under test, so the test would catch either side drifting.
# See this PR's body for the cross-repo contract note to W-A2.
# ---------------------------------------------------------------------------


def test_hash_prompt_matches_hand_computed_canonical_json_sha256():
    messages = [{"role": "user", "content": "hi"}]
    expected = hashlib.sha256(
        json.dumps(messages, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert hash_prompt(messages) == expected


def test_hash_prompt_is_key_order_independent():
    a = [{"role": "user", "content": "hi"}]
    b = [{"content": "hi", "role": "user"}]
    assert hash_prompt(a) == hash_prompt(b)


def test_hash_response_matches_hand_computed_sha256_of_raw_text():
    text = "The answer is 42."
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert hash_response(text) == expected


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
