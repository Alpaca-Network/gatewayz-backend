"""Tests for src.services.providers.community_adapter (gatewayz-backend#2262 #2265).

``src/db/gpu.py`` is owned by the parallel W-A1 workstream and does not exist
on this branch -- every test that needs "nodes exist" installs a fake module
into ``sys.modules['src.db.gpu']`` (removed via monkeypatch's automatic
teardown), matching the module's lazy-import contract. Tests that want the
"W-A1 not merged yet" behavior simply leave it absent.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.services.providers import community_adapter as ca

NODE = {
    "id": "node-1",
    "provider_id": "provider-1",
    "endpoint_url": "https://node1.example.test/v1",
    "endpoint_api_key_encrypted": "encrypted-token",
    "name": "alice-rig",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    ca.clear_adapter_cache()
    yield
    ca.clear_adapter_cache()


def _install_fake_gpu_module(
    monkeypatch,
    *,
    nodes=None,
    provider=None,
    select_error: Exception | None = None,
):
    calls = {"adjust_outstanding": []}
    module = types.ModuleType("src.db.gpu")

    def select_nodes_for_model(model):
        if select_error:
            raise select_error
        return nodes if nodes is not None else []

    def get_provider(provider_id):
        return provider

    def adjust_outstanding(node_id, delta):
        calls["adjust_outstanding"].append((node_id, delta))

    module.select_nodes_for_model = select_nodes_for_model
    module.get_provider = get_provider
    module.adjust_outstanding = adjust_outstanding
    monkeypatch.setitem(sys.modules, "src.db.gpu", module)
    return calls


def _install_fake_db_gpu_work(monkeypatch):
    recorded = {"work": [], "attested": []}

    def record_work(**kwargs):
        row = {"id": len(recorded["work"]) + 1, **kwargs}
        recorded["work"].append(row)
        return row

    def mark_attested(work_id, sig):
        recorded["attested"].append((work_id, sig))
        return True

    monkeypatch.setattr("src.db.gpu_work.record_work", record_work)
    monkeypatch.setattr("src.db.gpu_work.mark_attested", mark_attested)
    return recorded


# --- model id / node selection ---------------------------------------------


def test_strip_community_prefix():
    assert ca.strip_community_prefix("community/llama-3.1-8b-instruct") == "llama-3.1-8b-instruct"
    assert ca.strip_community_prefix("llama-3.1-8b-instruct") == "llama-3.1-8b-instruct"


def test_no_nodes_available_raises_503(monkeypatch):
    _install_fake_gpu_module(monkeypatch, nodes=[])
    with pytest.raises(HTTPException) as exc:
        ca.community_request([{"role": "user", "content": "hi"}], "community/some-model")
    assert exc.value.status_code == 503
    assert exc.value.detail == "no_community_node_available"


def test_gpu_module_missing_treated_as_no_nodes(monkeypatch):
    monkeypatch.delitem(sys.modules, "src.db.gpu", raising=False)
    with pytest.raises(HTTPException) as exc:
        ca.community_request([{"role": "user", "content": "hi"}], "community/some-model")
    assert exc.value.status_code == 503


def test_selects_head_node_of_returned_list(monkeypatch):
    other = {**NODE, "id": "node-2"}
    _install_fake_gpu_module(monkeypatch, nodes=[NODE, other])
    node = ca._select_head_node("some-model")
    assert node["id"] == "node-1"


# --- adapter cache -----------------------------------------------------------


def test_adapter_for_node_is_cached(monkeypatch):
    monkeypatch.setattr(ca, "_decrypt_node_key", lambda enc: "plain-key")
    a1 = ca.adapter_for_node(NODE)
    a2 = ca.adapter_for_node(NODE)
    assert a1 is a2


def test_adapter_cache_invalidated_on_endpoint_change(monkeypatch):
    monkeypatch.setattr(ca, "_decrypt_node_key", lambda enc: "plain-key")
    a1 = ca.adapter_for_node(NODE)
    changed = {**NODE, "endpoint_url": "https://node1-new.example.test/v1"}
    a2 = ca.adapter_for_node(changed)
    assert a1 is not a2


def test_invalidate_adapter_forces_rebuild(monkeypatch):
    monkeypatch.setattr(ca, "_decrypt_node_key", lambda enc: "plain-key")
    a1 = ca.adapter_for_node(NODE)
    ca.invalidate_adapter(NODE["id"])
    a2 = ca.adapter_for_node(NODE)
    assert a1 is not a2


def test_adapter_client_factory_uses_decrypted_key(monkeypatch):
    monkeypatch.setattr(ca, "_decrypt_node_key", lambda enc: f"decrypted:{enc}")
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    adapter = ca.adapter_for_node(NODE)
    adapter.cfg.client_factory()

    assert captured["api_key"] == "decrypted:encrypted-token"
    assert captured["base_url"] == NODE["endpoint_url"]


# --- non-streaming request: outstanding count, receipts, attestation --------


def _fake_openai_client(
    monkeypatch, *, headers=None, content="hello", prompt_tokens=5, completion_tokens=7, error=None
):
    monkeypatch.setattr(ca, "_decrypt_node_key", lambda enc: "plain-key")

    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )

    raw_response = MagicMock()
    raw_response.headers = headers or {}
    raw_response.parse.return_value = completion

    with_raw_response = MagicMock()
    if error:
        with_raw_response.create.side_effect = error
    else:
        with_raw_response.create.return_value = raw_response

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(with_raw_response=with_raw_response)
            )

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    return with_raw_response


def test_request_increments_and_decrements_outstanding(monkeypatch):
    calls = _install_fake_gpu_module(monkeypatch, nodes=[NODE])
    _install_fake_db_gpu_work(monkeypatch)
    _fake_openai_client(monkeypatch)

    ca.community_request([{"role": "user", "content": "hi"}], "community/some-model")

    assert calls["adjust_outstanding"] == [("node-1", 1), ("node-1", -1)]


def test_request_decrements_outstanding_on_provider_exception(monkeypatch):
    calls = _install_fake_gpu_module(monkeypatch, nodes=[NODE])
    _install_fake_db_gpu_work(monkeypatch)
    _fake_openai_client(monkeypatch, error=RuntimeError("node offline"))

    with pytest.raises(RuntimeError):
        ca.community_request([{"role": "user", "content": "hi"}], "community/some-model")

    assert calls["adjust_outstanding"] == [("node-1", 1), ("node-1", -1)]


def test_request_records_receipt_without_content(monkeypatch):
    _install_fake_gpu_module(monkeypatch, nodes=[NODE])
    recorded = _install_fake_db_gpu_work(monkeypatch)
    _fake_openai_client(monkeypatch, content="the answer", prompt_tokens=3, completion_tokens=4)

    ca.community_request(
        [{"role": "user", "content": "hi"}],
        "community/some-model",
        _gatewayz_billing_ref="ref-abc",
    )

    assert len(recorded["work"]) == 1
    row = recorded["work"][0]
    assert row["billing_ref"] == "ref-abc"
    assert row["node_id"] == "node-1"
    assert row["provider_id"] == "provider-1"
    assert row["model"] == "some-model"
    assert row["prompt_tokens"] == 3
    assert row["completion_tokens"] == 4
    assert row["status"] == "completed"
    assert "prompt" not in row and "content" not in row and "messages" not in row


def test_request_records_failed_receipt_on_exception(monkeypatch):
    _install_fake_gpu_module(monkeypatch, nodes=[NODE])
    recorded = _install_fake_db_gpu_work(monkeypatch)
    _fake_openai_client(monkeypatch, error=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        ca.community_request(
            [{"role": "user", "content": "hi"}],
            "community/some-model",
            _gatewayz_billing_ref="ref-1",
        )

    assert len(recorded["work"]) == 1
    assert recorded["work"][0]["status"] == "failed"


def test_billing_ref_kwarg_never_reaches_openai_client(monkeypatch):
    _install_fake_gpu_module(monkeypatch, nodes=[NODE])
    _install_fake_db_gpu_work(monkeypatch)
    with_raw_response = _fake_openai_client(monkeypatch)

    ca.community_request(
        [{"role": "user", "content": "hi"}],
        "community/some-model",
        _gatewayz_billing_ref="ref-abc",
        temperature=0.2,
    )

    _, call_kwargs = with_raw_response.create.call_args
    assert "_gatewayz_billing_ref" not in call_kwargs
    assert call_kwargs["extra_headers"]["X-Gatewayz-Request-Id"] == "ref-abc"


def test_attestation_verified_with_real_wallet_signature(monkeypatch):
    from eth_account import Account

    acct = Account.create()
    _install_fake_gpu_module(
        monkeypatch,
        nodes=[NODE],
        provider={"payout_wallet_address": acct.address},
    )
    recorded = _install_fake_db_gpu_work(monkeypatch)

    from src.services.gpu.hashing import canonical_json, sha256_hex

    messages = [{"role": "user", "content": "hi"}]
    prompt_hash = sha256_hex(canonical_json(messages))
    response_hash = sha256_hex("the answer")
    message = f"ref-abc|some-model|{prompt_hash}|{response_hash}|3|4"

    from eth_account.messages import encode_defunct

    signature = acct.sign_message(encode_defunct(text=message)).signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature

    _fake_openai_client(
        monkeypatch,
        headers={"x-gatewayz-attestation": signature},
        content="the answer",
        prompt_tokens=3,
        completion_tokens=4,
    )

    ca.community_request(messages, "community/some-model", _gatewayz_billing_ref="ref-abc")

    assert recorded["attested"] == [(recorded["work"][0]["id"], signature)]


def test_attestation_with_wrong_key_is_not_attested(monkeypatch):
    from eth_account import Account

    real_acct = Account.create()
    wrong_acct = Account.create()
    _install_fake_gpu_module(
        monkeypatch,
        nodes=[NODE],
        provider={"payout_wallet_address": real_acct.address},
    )
    recorded = _install_fake_db_gpu_work(monkeypatch)

    from src.services.gpu.hashing import canonical_json, sha256_hex

    messages = [{"role": "user", "content": "hi"}]
    prompt_hash = sha256_hex(canonical_json(messages))
    response_hash = sha256_hex("the answer")
    message = f"ref-abc|some-model|{prompt_hash}|{response_hash}|3|4"

    from eth_account.messages import encode_defunct

    signature = wrong_acct.sign_message(encode_defunct(text=message)).signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature

    _fake_openai_client(
        monkeypatch,
        headers={"x-gatewayz-attestation": signature},
        content="the answer",
        prompt_tokens=3,
        completion_tokens=4,
    )

    ca.community_request(messages, "community/some-model", _gatewayz_billing_ref="ref-abc")

    assert recorded["attested"] == []


# --- streaming ----------------------------------------------------------------


def _fake_openai_stream_client(monkeypatch, chunks, error=None):
    monkeypatch.setattr(ca, "_decrypt_node_key", lambda enc: "plain-key")

    completions = MagicMock()
    if error:
        completions.create.side_effect = error
    else:
        completions.create.return_value = iter(chunks)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=completions)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    return completions


def test_stream_yields_chunks_and_records_receipt_on_completion(monkeypatch):
    calls = _install_fake_gpu_module(monkeypatch, nodes=[NODE])
    recorded = _install_fake_db_gpu_work(monkeypatch)

    chunk1 = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="hel"))], usage=None
    )
    chunk2 = SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content="lo"))],
        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3),
    )
    _fake_openai_stream_client(monkeypatch, [chunk1, chunk2])

    gen = ca.community_stream(
        [{"role": "user", "content": "hi"}], "community/some-model", _gatewayz_billing_ref="ref-9"
    )
    out = list(gen)

    assert out == [chunk1, chunk2]
    assert calls["adjust_outstanding"] == [("node-1", 1), ("node-1", -1)]
    assert len(recorded["work"]) == 1
    row = recorded["work"][0]
    assert row["status"] == "completed"
    assert row["prompt_tokens"] == 2
    assert row["completion_tokens"] == 3


def test_stream_no_node_raises_before_returning_generator(monkeypatch):
    _install_fake_gpu_module(monkeypatch, nodes=[])
    with pytest.raises(HTTPException):
        ca.community_stream([{"role": "user", "content": "hi"}], "community/some-model")


def test_stream_connect_error_decrements_outstanding_and_records_failure(monkeypatch):
    calls = _install_fake_gpu_module(monkeypatch, nodes=[NODE])
    recorded = _install_fake_db_gpu_work(monkeypatch)
    _fake_openai_stream_client(monkeypatch, [], error=RuntimeError("connect failed"))

    with pytest.raises(RuntimeError):
        ca.community_stream([{"role": "user", "content": "hi"}], "community/some-model")

    assert calls["adjust_outstanding"] == [("node-1", 1), ("node-1", -1)]
    assert recorded["work"][0]["status"] == "failed"


def test_community_process_delegates_to_shared_normalization():
    response = SimpleNamespace(
        id="chatcmpl-1",
        object="chat.completion",
        created=1,
        model="some-model",
        choices=[
            SimpleNamespace(
                index=0,
                message=SimpleNamespace(content="hi", role="assistant", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    result = ca.community_process(response)
    assert result["id"] == "chatcmpl-1"
    assert result["choices"][0]["message"]["content"] == "hi"
