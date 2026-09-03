"""``community`` provider adapter (gatewayz-backend#2262 #2265, spec §1/§4).

Trust-boundary decision (spec §1, binding): a community GPU operator IS the
compute and sees prompt content by construction. Community routing is
**opt-in per request only** -- a client must explicitly ask for model id
``community/<model>``. This module is never wired into failover or
auto-routing (enforced in ``src/services/provider_failover.py`` and by
simply never being present in ``FALLBACK_PROVIDER_PRIORITY``/the
multi-provider registry); the model id prefix IS the user's consent.

Unlike ``adapter_configs.py`` (one static ``ProviderConfig`` per slug, loaded
once at import time), a community "provider" fans out to many operator-run
nodes, selected per request from ``src.db.gpu.select_nodes_for_model`` (owned
by the parallel W-A1 workstream). This module therefore builds and caches one
``OpenAICompatAdapter`` per *node*, not per provider, and picks a node fresh
on every call.

``src/db/gpu.py`` does not exist until W-A1 merges -- every call into it is
lazy-imported and any ``ImportError`` is treated as "no nodes available" so
this module works standalone (tests patch these lazy-import points directly).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from fastapi import HTTPException

from src.services.gpu.hashing import hash_prompt, hash_response
from src.services.providers.openai_compat import OpenAICompatAdapter, ProviderConfig, make_adapter

logger = logging.getLogger(__name__)

MODEL_PREFIX = "community/"
NO_NODE_DETAIL = "no_community_node_available"

# node_id -> (adapter, endpoint_url, encrypted_key). Keyed on the fields a
# PATCH/rotate-token can change so a stale entry is detected even without an
# explicit invalidate_adapter() call; invalidate_adapter() is still the fast
# path W-A1's routes call directly after a mutation.
_adapter_cache: dict[Any, tuple[OpenAICompatAdapter, str, str]] = {}

# A single adapter instance used only for .process() -- that method doesn't
# touch the client/config beyond the slug (used in log lines), so it doesn't
# need to be per-node.
_PROCESS_ADAPTER = make_adapter(
    ProviderConfig(
        slug="community",
        base_url="",
        api_key_env="COMMUNITY_NODE_API_KEY_PLACEHOLDER",
        display_name="community",
    )
)


# ---------------------------------------------------------------------------
# Lazy W-A1 DB access -- see module docstring.
# ---------------------------------------------------------------------------


def _select_nodes_for_model(model: str) -> list[dict]:
    try:
        from src.db.gpu import select_nodes_for_model
    except ImportError:
        logger.warning("src.db.gpu not available yet; community routing has no nodes")
        return []
    try:
        return select_nodes_for_model(model) or []
    except Exception as e:
        logger.warning("select_nodes_for_model(%r) failed: %s", model, e)
        return []


def _get_provider(provider_id: Any) -> dict | None:
    if provider_id is None:
        return None
    try:
        from src.db.gpu import get_provider
    except ImportError:
        return None
    try:
        return get_provider(provider_id)
    except Exception as e:
        logger.warning("get_provider(%r) failed: %s", provider_id, e)
        return None


def _adjust_outstanding(node_id: Any, delta: int) -> None:
    try:
        from src.db.gpu import adjust_outstanding
    except ImportError:
        return
    try:
        adjust_outstanding(node_id, delta)
    except Exception as e:
        logger.warning("adjust_outstanding(%r, %s) failed: %s", node_id, delta, e)


# ---------------------------------------------------------------------------
# Model id / node selection
# ---------------------------------------------------------------------------


def strip_community_prefix(model_id: str) -> str:
    """``"community/llama-3.1-8b-instruct"`` -> ``"llama-3.1-8b-instruct"``."""
    if model_id.startswith(MODEL_PREFIX):
        return model_id[len(MODEL_PREFIX) :]
    return model_id


def _select_head_node(model_suffix: str) -> dict:
    nodes = _select_nodes_for_model(model_suffix)
    if not nodes:
        raise HTTPException(status_code=503, detail=NO_NODE_DETAIL)
    return nodes[0]


# ---------------------------------------------------------------------------
# Per-node adapter cache
# ---------------------------------------------------------------------------


def invalidate_adapter(node_id: Any) -> None:
    """Drop the cached client for *node_id*. Call this from the PATCH/rotate-
    token routes (W-A1) whenever a node's endpoint or key changes."""
    _adapter_cache.pop(node_id, None)


def clear_adapter_cache() -> None:
    """Test helper: drop every cached node adapter."""
    _adapter_cache.clear()


def _decrypt_node_key(encrypted: str) -> str:
    from src.utils.crypto import decrypt_api_key

    return decrypt_api_key(encrypted)


def adapter_for_node(node: dict) -> OpenAICompatAdapter:
    """Build (or reuse) the ``OpenAICompatAdapter`` for one node.

    Cached per ``node["id"]``; automatically rebuilt if the node's
    ``endpoint_url``/``endpoint_api_key_encrypted`` changed since the entry
    was cached (covers a missed ``invalidate_adapter`` call too).
    """
    node_id = node["id"]
    endpoint_url = node["endpoint_url"]
    encrypted_key = node.get("endpoint_api_key_encrypted") or ""

    cached = _adapter_cache.get(node_id)
    if cached and cached[1] == endpoint_url and cached[2] == encrypted_key:
        return cached[0]

    plaintext_key = _decrypt_node_key(encrypted_key) if encrypted_key else ""

    def _client_factory():
        from openai import OpenAI

        # Real key: authenticates to the operator's vLLM server. Never the
        # Config-level placeholder below (that's only there to satisfy
        # OpenAICompatAdapter._get_client()'s pre-client_factory truthiness
        # check -- see Config.COMMUNITY_NODE_API_KEY_PLACEHOLDER).
        return OpenAI(base_url=endpoint_url, api_key=plaintext_key or "unused")

    cfg = ProviderConfig(
        slug=f"community:{node_id}",
        base_url=endpoint_url,
        api_key_env="COMMUNITY_NODE_API_KEY_PLACEHOLDER",
        display_name=f"community/{node.get('name', node_id)}",
        client_factory=_client_factory,
    )
    adapter = make_adapter(cfg)
    _adapter_cache[node_id] = (adapter, endpoint_url, encrypted_key)
    return adapter


# ---------------------------------------------------------------------------
# The call itself
# ---------------------------------------------------------------------------

_ATTESTATION_HEADER = "x-gatewayz-attestation"
_REQUEST_ID_HEADER = "X-Gatewayz-Request-Id"


def _call_node(
    adapter: OpenAICompatAdapter,
    model_suffix: str,
    messages: list[dict[str, Any]],
    *,
    stream: bool,
    billing_ref: str | None,
    **kwargs: Any,
):
    """Issue the actual chat-completion call for one node.

    Non-streaming: uses ``with_raw_response`` so the node's
    ``X-Gatewayz-Attestation`` response header (spec §4) is captured
    alongside the parsed completion -- ``adapter.request()`` alone discards
    headers. Streaming responses don't expose headers without switching to
    ``with_streaming_response`` (a different chunk-consumption shape than the
    rest of this codebase uses for ``stream()``), so attestation capture is
    deferred to a future iteration for the streaming path; see W-A2 report.
    """
    from src.services.providers.reasoning_effort import (
        apply_reasoning_effort,
        normalize_token_limit,
    )

    client = adapter._get_client()
    resolved = adapter._resolve_model(model_suffix)

    effort = kwargs.pop("reasoning_effort", None)
    kwargs = normalize_token_limit(dict(kwargs), adapter.cfg.slug, resolved)
    kwargs = apply_reasoning_effort(kwargs, adapter.cfg.slug, resolved, effort)

    extra_headers = dict(kwargs.pop("extra_headers", None) or {})
    if billing_ref:
        # W-E's node agent keys its attestation replay/logging on this --
        # same header name the RequestIDMiddleware sets on the *response* to
        # the client, reused here for the *outbound* leg to the node.
        extra_headers[_REQUEST_ID_HEADER] = billing_ref

    if stream:
        raw_stream = client.chat.completions.create(
            model=resolved, messages=messages, stream=True, extra_headers=extra_headers, **kwargs
        )
        return raw_stream, {}

    raw_response = client.chat.completions.with_raw_response.create(
        model=resolved, messages=messages, extra_headers=extra_headers, **kwargs
    )
    return raw_response.parse(), dict(raw_response.headers)


def _record_receipt(
    *,
    node: dict,
    model_suffix: str,
    messages: list[dict[str, Any]],
    response_text: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    status: str,
    response_headers: dict[str, str] | None,
    billing_ref: str | None,
) -> None:
    from src.db.gpu_work import mark_attested, record_work

    prompt_hash = hash_prompt(messages)
    response_hash = hash_response(response_text or "")

    work = record_work(
        billing_ref=billing_ref,
        node_id=node.get("id"),
        provider_id=node.get("provider_id"),
        model=model_suffix,
        prompt_hash=prompt_hash,
        response_hash=response_hash,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        status=status,
    )
    if not work:
        return

    sig = (response_headers or {}).get(_ATTESTATION_HEADER)
    if not sig:
        return

    provider = _get_provider(node.get("provider_id"))
    wallet = (provider or {}).get("payout_wallet_address")
    if not wallet:
        return

    from src.security.wallet_signature import verify_wallet_signature

    message = (
        f"{billing_ref}|{model_suffix}|{prompt_hash}|{response_hash}|"
        f"{prompt_tokens}|{completion_tokens}"
    )
    if verify_wallet_signature(wallet, message, sig):
        mark_attested(work["id"], sig)
    else:
        logger.warning(
            "community node %s: X-Gatewayz-Attestation present but signature "
            "verification failed for billing_ref=%s",
            node.get("id"),
            billing_ref,
        )


# ---------------------------------------------------------------------------
# PROVIDER_ROUTING entry points (request/stream/process contract, base.py)
# ---------------------------------------------------------------------------


def community_request(messages: list[dict[str, Any]], model_id: str, **kwargs: Any) -> Any:
    """Non-streaming ``PROVIDER_ROUTING["community"]["request"]``."""
    billing_ref = kwargs.pop("_gatewayz_billing_ref", None)
    model_suffix = strip_community_prefix(model_id)
    node = _select_head_node(model_suffix)
    adapter = adapter_for_node(node)

    _adjust_outstanding(node["id"], 1)
    t0 = time.monotonic()
    status = "failed"
    raw: Any = None
    headers: dict[str, str] = {}
    try:
        raw, headers = _call_node(
            adapter, model_suffix, messages, stream=False, billing_ref=billing_ref, **kwargs
        )
        status = "completed"
        return raw
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        _adjust_outstanding(node["id"], -1)

        response_text = ""
        prompt_tokens = completion_tokens = 0
        if raw is not None:
            try:
                response_text = raw.choices[0].message.content or ""
            except Exception:
                pass
            usage = getattr(raw, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        _record_receipt(
            node=node,
            model_suffix=model_suffix,
            messages=messages,
            response_text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            response_headers=headers,
            billing_ref=billing_ref,
        )


def _consume_and_record(
    raw_stream: Any,
    node: dict,
    model_suffix: str,
    messages: list[dict[str, Any]],
    t0: float,
    billing_ref: str | None,
) -> Iterator[Any]:
    content_parts: list[str] = []
    prompt_tokens = completion_tokens = 0
    status = "failed"
    try:
        for chunk in raw_stream:
            try:
                delta = chunk.choices[0].delta
                if delta and getattr(delta, "content", None):
                    content_parts.append(delta.content)
            except (IndexError, AttributeError):
                pass
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", None) or prompt_tokens
                completion_tokens = getattr(usage, "completion_tokens", None) or completion_tokens
            yield chunk
        status = "completed"
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        _adjust_outstanding(node["id"], -1)
        _record_receipt(
            node=node,
            model_suffix=model_suffix,
            messages=messages,
            response_text="".join(content_parts),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            # Streaming attestation capture is deferred -- see _call_node docstring.
            response_headers=None,
            billing_ref=billing_ref,
        )


def community_stream(messages: list[dict[str, Any]], model_id: str, **kwargs: Any) -> Iterator[Any]:
    """Streaming ``PROVIDER_ROUTING["community"]["stream"]``.

    Node selection and stream creation happen eagerly (before this function
    returns) so a 503 for "no node available" raises synchronously, matching
    every other provider's ``stream()`` -- only the chunk consumption/receipt
    bookkeeping is deferred into the returned generator.
    """
    billing_ref = kwargs.pop("_gatewayz_billing_ref", None)
    model_suffix = strip_community_prefix(model_id)
    node = _select_head_node(model_suffix)
    adapter = adapter_for_node(node)

    _adjust_outstanding(node["id"], 1)
    t0 = time.monotonic()
    try:
        raw_stream, _headers = _call_node(
            adapter, model_suffix, messages, stream=True, billing_ref=billing_ref, **kwargs
        )
    except Exception:
        latency_ms = int((time.monotonic() - t0) * 1000)
        _adjust_outstanding(node["id"], -1)
        _record_receipt(
            node=node,
            model_suffix=model_suffix,
            messages=messages,
            response_text="",
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status="failed",
            response_headers=None,
            billing_ref=billing_ref,
        )
        raise

    return _consume_and_record(raw_stream, node, model_suffix, messages, t0, billing_ref)


def community_process(response: Any) -> dict[str, Any]:
    """``PROVIDER_ROUTING["community"]["process"]`` -- identical normalization
    to every other adapter-served provider (only used by the anonymous
    raw-dispatch path, per base.py)."""
    return _PROCESS_ADAPTER.process(response)
