import json
import logging
from collections.abc import Generator
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import HTTPException

from src.config import Config
from src.services.model_catalog_cache import get_cached_gateway_catalog

# Initialize logging
logger = logging.getLogger(__name__)

# Hugging Face Inference Router base URL
HF_INFERENCE_BASE_URL = "https://router.huggingface.co/v1"
HF_INFERENCE_SUFFIX = ":hf-inference"

FOREIGN_PROVIDER_NAMESPACES = {
    "openrouter",
    "cerebras",
    "featherless",
    "vercel-ai-gateway",
    "aihubmix",
    "anannas",
    "alibaba-cloud",
    "fireworks",
    "together",
    "google-vertex",
    "near",
    "aimo",
    "xai",
    "chutes",
    "alpaca-network",
    "clarifai",
    "helicone",
    "portkey",
    "gatewayz",
}

ALLOWED_PARAMS = {
    "max_tokens",
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "response_format",
    "tools",
    "stream",
}


class HFStreamChoice:
    """Lightweight structure that mimics OpenAI stream choice objects."""

    def __init__(self, data: dict[str, Any]):
        self.index = data.get("index", 0)
        self.delta = SimpleNamespace(**(data.get("delta") or {}))
        self.message = SimpleNamespace(**data["message"]) if data.get("message") else None
        self.finish_reason = data.get("finish_reason")


class HFStreamChunk:
    """Stream chunk compatible with OpenAI client chunks."""

    def __init__(self, payload: dict[str, Any]):
        self.id = payload.get("id")
        self.object = payload.get("object")
        self.created = payload.get("created")
        self.model = payload.get("model")
        self.choices = [HFStreamChoice(choice) for choice in payload.get("choices", [])]

        usage = payload.get("usage")
        if usage:
            self.usage = SimpleNamespace(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )
        else:
            self.usage = None


def _build_timeout_config(timeout: float | httpx.Timeout | None) -> httpx.Timeout:
    if isinstance(timeout, httpx.Timeout):
        return timeout

    base = timeout or 180.0  # generous default for slow-start models
    return httpx.Timeout(
        timeout=base,
        connect=min(30.0, base),
        read=base,
        write=base,
        pool=None,
    )


def get_huggingface_client(timeout: float | httpx.Timeout | None = None) -> httpx.Client:
    """Create an HTTPX client for the Hugging Face Router API."""
    if not Config.HUG_API_KEY:
        raise ValueError("Hugging Face API key (HUG_API_KEY) not configured")

    headers = {
        "Authorization": f"Bearer {Config.HUG_API_KEY}",
        "Content-Type": "application/json",
    }

    timeout_config = _build_timeout_config(timeout)

    return httpx.Client(base_url=HF_INFERENCE_BASE_URL, headers=headers, timeout=timeout_config)


def _normalize_namespace(namespace: str) -> str:
    """Normalize provider namespace for comparison."""
    clean = namespace.strip().lower()
    if clean.startswith("@"):
        clean = clean[1:]
    return clean.replace("_", "-")


def _resolve_model_id_case(model_id: str) -> str:
    """
    Resolve the correct case for a HuggingFace model ID by looking it up in the cache.

    HuggingFace Router API is case-sensitive, but model IDs coming from the gateway
    may have been lowercased by model_transformations.py. This function looks up the
    correct case from the cached HuggingFace model catalog.

    Args:
        model_id: The model ID to resolve (may be lowercase)

    Returns:
        The correctly-cased model ID, or the original if not found in cache
    """
    # Strip the :hf-inference suffix if present for lookup
    lookup_id = model_id
    has_suffix = False
    if lookup_id.endswith(HF_INFERENCE_SUFFIX):
        lookup_id = lookup_id[: -len(HF_INFERENCE_SUFFIX)]
        has_suffix = True

    # Check cache for the correct case
    cache_data = get_cached_gateway_catalog("huggingface")
    if cache_data:
        lookup_lower = lookup_id.lower()
        for cached_model in cache_data:
            cached_id = cached_model.get("id", "")
            if cached_id.lower() == lookup_lower:
                resolved = cached_id
                if resolved != lookup_id:
                    logger.info(
                        "Resolved HuggingFace model ID case: '%s' -> '%s'",
                        lookup_id,
                        resolved,
                    )
                # Re-add suffix if it was present
                if has_suffix:
                    resolved = f"{resolved}{HF_INFERENCE_SUFFIX}"
                return resolved

    # If not found in cache, return as-is
    logger.debug(
        "Model '%s' not found in HuggingFace cache, using as-is",
        lookup_id,
    )
    return model_id


def _extract_namespace(model: str) -> str | None:
    """Return the provider namespace portion of a model ID."""
    if not model or "/" not in model:
        return None
    namespace, _ = model.split("/", 1)
    return namespace


def _is_foreign_provider_model(model: str) -> tuple[bool, str | None]:
    """
    Determine if a model ID is scoped to another provider namespace,
    meaning it should not be routed through Hugging Face.
    """
    namespace = _extract_namespace(model)
    if not namespace:
        return False, None
    if namespace.startswith("@"):
        return True, namespace
    normalized = _normalize_namespace(namespace)
    return (normalized in FOREIGN_PROVIDER_NAMESPACES, namespace)


def _prepare_model(model: str) -> str:
    """
    Prepare a model ID for HuggingFace Router API.

    This function:
    1. Resolves the correct case from the HuggingFace model cache
    2. Adds the :hf-inference suffix if not already present
    """
    # First, resolve the correct case from cache
    model = _resolve_model_id_case(model)

    if model.endswith(HF_INFERENCE_SUFFIX):
        return model
    is_foreign, _ = _is_foreign_provider_model(model)
    if is_foreign:
        return model
    return f"{model}{HF_INFERENCE_SUFFIX}"


def _build_payload(messages: list[dict[str, Any]], model: str, **kwargs) -> dict[str, Any]:
    # Validate messages format
    if not isinstance(messages, list):
        logger.error(f"Messages must be a list, got {type(messages).__name__}")
        raise TypeError(f"Messages must be a list, got {type(messages).__name__}")

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            logger.error(f"Message {i} must be a dict, got {type(msg).__name__}: {msg}")
            raise TypeError(f"Message {i} must be a dict, got {type(msg).__name__}")
        if "role" not in msg or "content" not in msg:
            logger.error(f"Message {i} missing required fields (role, content): {msg}")
            raise ValueError(f"Message {i} missing required fields (role, content): {msg}")

    is_foreign, namespace = _is_foreign_provider_model(model)
    if is_foreign:
        logger.info(
            "Model '%s' is scoped to provider namespace '%s'; skipping Hugging Face routing.",
            model,
            namespace,
        )
        raise HTTPException(
            status_code=404,
            detail=(
                f"Model '{model}' is namespaced to provider '{namespace}' and "
                "is not available on Hugging Face Router."
            ),
        )

    payload = {
        "messages": messages,
        "model": _prepare_model(model),
    }

    for key, value in kwargs.items():
        if key in ALLOWED_PARAMS and value is not None:
            payload[key] = value

    return payload


def make_huggingface_request_openai(messages, model, **kwargs):
    """Make request to Hugging Face Router using OpenAI-compatible schema."""
    client = get_huggingface_client(timeout=180.0)
    try:
        payload = _build_payload(messages, model, **kwargs)
        logger.info(f"Making Hugging Face request with model: {payload['model']}")
        logger.debug(
            "HF request payload (non-streaming): message_count=%s, payload_keys=%s",
            len(messages),
            list(payload.keys()),
        )

        response = client.post("/chat/completions", json=payload)
        response.raise_for_status()
        logger.info("Hugging Face request successful for model: %s", payload["model"])
        return response.json()
    except httpx.HTTPStatusError as e:
        # Log detailed error information for 400 errors to help diagnose issues
        if e.response.status_code == 400:
            error_body = e.response.text[:500] if hasattr(e.response, "text") else str(e)
            logger.error(
                "Hugging Face 400 Bad Request for model '%s': %s. "
                "This model may not be available on HF Inference Router despite appearing in the catalog. "
                "Request payload: %s",
                model,
                error_body,
                json.dumps(payload, default=str)[:500],
            )
        logger.error("Hugging Face request failed for model '%s': %s", model, e)
        raise
    except Exception as e:
        logger.error("Hugging Face request failed for model '%s': %s", model, e)
        raise
    finally:
        client.close()


def make_huggingface_request_openai_stream(
    messages, model, **kwargs
) -> Generator[HFStreamChunk, None, None]:
    """Stream responses from Hugging Face Router using SSE."""
    client = get_huggingface_client(timeout=300.0)

    try:
        payload = _build_payload(messages, model, **kwargs)
        payload["stream"] = True

        logger.info("Making Hugging Face streaming request with model: %s", payload["model"])
        logger.debug(
            "HF streaming request payload: message_count=%s, payload_keys=%s, all_params=%s",
            len(messages),
            list(payload.keys()),
            {k: type(v).__name__ for k, v in payload.items()},
        )

        with client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            logger.info(
                "Hugging Face streaming request initiated for model: %s",
                payload["model"],
            )

            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                if raw_line.startswith("data: "):
                    data = raw_line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk_payload = json.loads(data)
                        yield HFStreamChunk(chunk_payload)
                    except json.JSONDecodeError as err:
                        logger.warning("Failed to decode Hugging Face stream chunk: %s", err)
                        continue
    except httpx.HTTPStatusError as e:
        # Log detailed error information for 400 errors to help diagnose issues
        if e.response.status_code == 400:
            error_body = e.response.text[:500] if hasattr(e.response, "text") else str(e)
            logger.error(
                "Hugging Face 400 Bad Request (streaming) for model '%s': %s. "
                "This model may not be available on HF Inference Router despite appearing in the catalog. "
                "Request payload: %s",
                model,
                error_body,
                json.dumps(payload, default=str)[:500],
            )
        logger.error("Hugging Face streaming request failed for model '%s': %s", model, e)
        raise
    except (TypeError, ValueError) as ve:
        logger.error("Invalid request format for HuggingFace: %s", ve)
        raise
    except Exception as e:
        logger.error("Hugging Face streaming request failed for model '%s': %s", model, e)
        raise
    finally:
        client.close()


def process_huggingface_response(response):
    """Process Hugging Face response (dict) to OpenAI-compatible structure."""
    try:
        if not isinstance(response, dict):
            raise TypeError("Hugging Face response must be a dictionary")

        choices = []
        for choice in response.get("choices", []):
            message = choice.get("message") or {}
            msg_dict = {
                "role": message.get("role", "assistant"),
                "content": message.get("content", ""),
            }

            # Include tool_calls if present (for function calling)
            if "tool_calls" in message:
                msg_dict["tool_calls"] = message["tool_calls"]

            # Include function_call if present (for legacy function_call format)
            if "function_call" in message:
                msg_dict["function_call"] = message["function_call"]

            choices.append(
                {
                    "index": choice.get("index", 0),
                    "message": msg_dict,
                    "finish_reason": choice.get("finish_reason"),
                }
            )

        usage = response.get("usage") or {}
        processed = {
            "id": response.get("id"),
            "object": response.get("object", "chat.completion"),
            "created": response.get("created"),
            "model": response.get("model"),
            "choices": choices,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        return processed
    except Exception as e:
        logger.error("Failed to process Hugging Face response: %s", e)
        raise
