"""Config-driven adapter for OpenAI-compatible providers.

Replaces the near-identical per-provider client modules (deepinfra, together,
fireworks, groq, zai — formerly ``<slug>_client.py``) with one adapter that
implements the canonical ``ProviderAdapter`` contract from ``base.py``:

    request(messages, model, **params) -> OpenAI-SDK-shaped response
    stream(messages, model, **params)  -> OpenAI-SDK stream (chunks untouched)
    process(response)                  -> {id, object, created, model, choices, usage}

Per-provider differences are expressed as data in ``ProviderConfig``:

    base_url / api_key_env  — endpoint + Config attribute holding the key
    client_factory          — pooled-client getter (None -> plain OpenAI client,
                              matching the old deepinfra behavior)
    model_prefix            — optional "slug/" prefix stripped from model ids
    extra_headers           — provider-specific default headers
    quirks                  — middleware toggles (circuit breaker, sentry, timing)

Deliberately NOT handled here (kept in bespoke clients by design decision on
the MVP refactor): alibaba region failover, cerebras/xai vendor-SDK + reasoning
params, featherless message sanitization. Catalog fetch/normalization also
stays per-provider (in ``<slug>_catalog.py``) because pricing-unit math is
provider-specific and regression-tested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from openai import OpenAI

from src.config import Config
from src.services.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitBreakerError,
    get_circuit_breaker,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quirks:
    """Middleware toggles preserved from the old per-provider clients."""

    # Wrap provider calls in a circuit breaker (old groq/together behavior).
    circuit_breaker: CircuitBreakerConfig | None = None
    # Report provider failures to Sentry via capture_provider_error.
    sentry: bool = False
    # Record latency via ProviderTimingContext (old groq behavior).
    timing: bool = False


_NO_QUIRKS = Quirks()


@dataclass(frozen=True)
class ProviderConfig:
    """Everything the adapter needs to serve one OpenAI-compatible provider."""

    slug: str
    base_url: str
    api_key_env: str  # attribute name on Config holding the API key
    display_name: str | None = None  # used in log/error messages
    model_prefix: str | None = None  # stripped from incoming model ids if present
    extra_headers: dict[str, str] | None = None
    client_factory: Callable[[], Any] | None = None  # pooled getter; None -> plain OpenAI
    quirks: Quirks | None = None


class OpenAICompatAdapter:
    """Object-form ``ProviderAdapter`` for OpenAI-compatible providers."""

    def __init__(self, cfg: ProviderConfig) -> None:
        self.cfg = cfg
        self.name = cfg.display_name or cfg.slug
        self.quirks = cfg.quirks or _NO_QUIRKS

    # -- client acquisition -------------------------------------------------

    def _get_client(self) -> Any:
        api_key = getattr(Config, self.cfg.api_key_env, None)
        if not api_key:
            raise ValueError(f"{self.name} API key not configured")
        if self.cfg.client_factory is not None:
            return self.cfg.client_factory()
        kwargs: dict[str, Any] = {"base_url": self.cfg.base_url, "api_key": api_key}
        if self.cfg.extra_headers:
            kwargs["default_headers"] = dict(self.cfg.extra_headers)
        return OpenAI(**kwargs)

    def _resolve_model(self, model: str) -> str:
        prefix = self.cfg.model_prefix
        if prefix and model.startswith(prefix):
            return model[len(prefix) :]
        return model

    # -- core call ------------------------------------------------------------

    def _create(self, messages: list[dict[str, Any]], model: str, *, stream: bool, **kwargs: Any):
        client = self._get_client()
        resolved = self._resolve_model(model)
        if stream:
            kwargs = {**kwargs, "stream": True}
        if self.quirks.timing:
            from src.utils.provider_timing import ProviderTimingContext

            mode = "stream" if stream else "non_stream"
            with ProviderTimingContext(self.cfg.slug, resolved, mode):
                return client.chat.completions.create(
                    model=resolved, messages=messages, **kwargs
                )
        return client.chat.completions.create(model=resolved, messages=messages, **kwargs)

    def _capture(
        self,
        error: Exception,
        model: str,
        endpoint: str,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        from src.utils.sentry_context import capture_provider_error

        capture_provider_error(
            error,
            provider=self.cfg.slug,
            model=model,
            endpoint=endpoint,
            extra_context=extra_context,
        )

    def _call(self, messages: list[dict[str, Any]], model: str, *, stream: bool, **kwargs: Any):
        endpoint = "/chat/completions (stream)" if stream else "/chat/completions"
        cb_config = self.quirks.circuit_breaker
        try:
            if cb_config is not None:
                breaker = get_circuit_breaker(self.cfg.slug, cb_config)
                return breaker.call(self._create, messages, model, stream=stream, **kwargs)
            return self._create(messages, model, stream=stream, **kwargs)
        except CircuitBreakerError as e:
            logger.warning(f"{self.name} circuit breaker OPEN: {e.message}")
            if self.quirks.sentry:
                self._capture(
                    e, model, endpoint, {"circuit_breaker_state": e.state.value}
                )
            raise
        except Exception as e:
            try:
                logger.error(f"{self.name} request failed for model '{model}': {e}")
                logger.error(f"Error type: {type(e).__name__}")
                if hasattr(e, "response"):
                    logger.error(
                        f"Response status: {getattr(e.response, 'status_code', 'N/A')}"
                    )
            except UnicodeEncodeError:
                logger.error(f"{self.name} request failed (encoding error in logging)")
            if self.quirks.sentry:
                self._capture(e, model, endpoint)
            raise

    # -- ProviderAdapter contract ----------------------------------------------

    def request(self, messages: list[dict[str, Any]], model: str, **params: Any) -> Any:
        """Non-streaming chat completion (old make_<slug>_request_openai)."""
        return self._call(messages, model, stream=False, **params)

    def stream(
        self, messages: list[dict[str, Any]], model: str, **params: Any
    ) -> Iterator[Any]:
        """Streaming chat completion; the SDK stream (and its SSE chunks) is
        returned unmodified (old make_<slug>_request_openai_stream)."""
        return self._call(messages, model, stream=True, **params)

    def process(self, response: Any) -> dict[str, Any]:
        """Normalize an OpenAI-SDK response to the canonical dict shape
        (old process_<slug>_response — byte-identical logic)."""
        from src.services.providers.anthropic_transformer import extract_message_with_tools

        try:
            choices = []
            for choice in response.choices:
                msg = extract_message_with_tools(choice.message)

                choices.append(
                    {
                        "index": choice.index,
                        "message": msg,
                        "finish_reason": choice.finish_reason,
                    }
                )

            return {
                "id": response.id,
                "object": response.object,
                "created": response.created,
                "model": response.model,
                "choices": choices,
                "usage": (
                    {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    }
                    if response.usage
                    else {}
                ),
            }
        except Exception as e:
            logger.error(f"Failed to process {self.name} response: {e}")
            raise


def make_adapter(cfg: ProviderConfig) -> OpenAICompatAdapter:
    """Build a ProviderAdapter for one OpenAI-compatible provider config."""
    return OpenAICompatAdapter(cfg)
