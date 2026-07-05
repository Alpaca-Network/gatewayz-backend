"""
Prompt Router - Main Orchestration Service.

Implements fail-open prompt-level routing with:
- Hard timeout (2ms) - returns default if exceeded
- Capability gating before scoring
- Health snapshot reads (single Redis GET)
- Stable model selection
- Compatible fallback chains

This is the main entry point for prompt-level routing.
"""

import logging
import time
from typing import Any

from src.schemas.router import (
    ModelCapabilities,
    RequiredCapabilities,
    RouterDecision,
    RouterOptimization,
    UserRouterPreferences,
)
from src.services.capability_gating import extract_capabilities, filter_by_capabilities
from src.services.fallback_chain import build_fallback_chain
from src.services.health_snapshots import get_healthy_models_sync
from src.services.model_selector import select_model
from src.services.prompt_classifier_rules import classify_prompt

logger = logging.getLogger(__name__)

# Hard timeout for the routing decision (milliseconds). If exceeded, fail open to
# the default cheap model. The pipeline is in-memory (one Redis GET for the health
# snapshot), so this only needs to cover a warm snapshot read + classification +
# scoring. The old 2ms budget was so tight it fired on nearly every request —
# collapsing "best model for the task" into an unconditional gpt-4o-mini fallback.
# 50ms is imperceptible to users yet lets the router actually complete; fail-open
# still catches a genuinely hung snapshot read. Override via ROUTER_TIMEOUT_MS.
import os as _os

ROUTER_TIMEOUT_MS = float(_os.environ.get("ROUTER_TIMEOUT_MS", "50.0"))

# Default cheap model for fail-open
DEFAULT_CHEAP_MODEL = "openai/gpt-4o-mini"
DEFAULT_PROVIDER = "openai"

# Hardcoded fallback list — used only when DB cache is unavailable.
# The authoritative source is the models table (latency_tier <= 2 + healthy).
STABLE_FALLBACK_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-haiku",
    "google/gemini-1.5-flash",
]


# --------------------------------------------------------------------------- #
# Capabilities registry — real, chat-only, cost-aware (pure builder + I/O shell)
# --------------------------------------------------------------------------- #

_CAPABILITY_MODEL_COLS = (
    "provider_model_id,canonical_id,modality,context_length,"
    "supports_function_calling,supports_vision,has_json_mode,"
    "model_pricing(price_per_input_token,price_per_output_token)"
)


# Non-chat model families to exclude by name even when the catalog mislabels their
# modality (observed: whisper rows duplicated as "text->text"). Defends the router
# against dirty catalog data — these families never serve chat completions.
_NON_CHAT_NAME_MARKERS = (
    "whisper",
    "tts",
    "text-to-speech",
    "embed",  # embedding models
    "dall-e",
    "dalle",
    "stable-diffusion",
    "flux",
    "sdxl",
    "clip",
    "rerank",
)


def _looks_non_chat_by_name(model_id: str) -> bool:
    """True if the model id names a known non-chat family (guards mislabeled data)."""
    mid = model_id.lower()
    return any(marker in mid for marker in _NON_CHAT_NAME_MARKERS)


def _is_chat_modality(modality: str | None) -> bool:
    """True if the model both accepts and returns text (a chat-completions model).

    Modality is like ``"text->text"``, ``"text+image->text"``, ``"audio"``,
    ``"text->image"``, ``"audio->text"``. A chat model must have text on BOTH the
    input and output sides, which excludes:
      * audio/image-only models (whisper ``"audio"``, ``"text->image"``), and
      * transcription/generation models that only bridge modalities
        (``"audio->text"`` speech-to-text, ``"text->audio"`` TTS).
    """
    if not modality:
        return True  # unknown → assume chat rather than drop a usable model
    m = str(modality).lower()
    if "->" in m:
        inp, out = m.split("->", 1)
    else:
        inp = out = m
    return "text" in inp and "text" in out


def _price_per_1k(model_pricing, field: str) -> float:
    """Per-1k price for ``field`` from a model_pricing join (dict/list/None). 0.0 if absent."""
    if not model_pricing:
        return 0.0
    row = model_pricing[0] if isinstance(model_pricing, list) else model_pricing
    if not isinstance(row, dict):
        return 0.0
    try:
        v = float(row.get(field) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return v * 1000.0 if v > 0 else 0.0


def build_capabilities_registry(rows: list[dict]) -> dict[str, ModelCapabilities]:
    """Project catalog rows → a chat-only, cost-aware capabilities registry (pure).

    Skips non-chat models (audio/image output) and id-less rows. When the same
    model id appears for multiple providers, keeps the cheapest input cost so
    auto-routing scores against the best price we can serve it at.
    """
    registry: dict[str, ModelCapabilities] = {}
    for r in rows:
        model_id = r.get("provider_model_id") or r.get("canonical_id")
        if not model_id:
            continue
        if _looks_non_chat_by_name(model_id):
            continue
        if not _is_chat_modality(r.get("modality")):
            continue

        cost_in = _price_per_1k(r.get("model_pricing"), "price_per_input_token")
        existing = registry.get(model_id)
        if existing is not None and existing.cost_per_1k_input <= cost_in:
            continue  # keep the cheaper offer for this model id

        modality = str(r.get("modality") or "").lower()
        try:
            max_context = int(r.get("context_length") or 0) or 128000
        except (TypeError, ValueError):
            max_context = 128000

        registry[model_id] = ModelCapabilities(
            model_id=model_id,
            provider=model_id.split("/")[0] if "/" in model_id else "unknown",
            tools=bool(r.get("supports_function_calling")),
            json_mode=bool(r.get("has_json_mode")),
            json_schema=False,
            vision=bool(r.get("supports_vision")) or "image" in modality.split("->", 1)[0],
            max_context=max_context,
            tool_schema_adherence="medium",
            cost_per_1k_input=cost_in,
            cost_per_1k_output=_price_per_1k(r.get("model_pricing"), "price_per_output_token"),
        )
    return registry


def _fetch_capability_rows() -> list[dict]:
    """Fetch active chat-capable catalog rows with pricing. [] on failure (caller defaults)."""
    from src.config.supabase_config import get_supabase_client

    client = get_supabase_client()
    rows: list[dict] = []
    start = 0
    while True:
        resp = (
            client.table("models")
            .select(_CAPABILITY_MODEL_COLS)
            .eq("is_active", True)
            .range(start, start + 999)
            .execute()
        )
        batch = getattr(resp, "data", None) or []
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        start += 1000


def _get_stable_fallback_models() -> list[str]:
    """Return stable fallback models from DB cache, with hardcoded fallback."""
    try:
        from src.services.model_capabilities_cache import get_stable_models

        result = get_stable_models()
        if result:
            return result
    except Exception:
        pass
    return STABLE_FALLBACK_MODELS


class PromptRouter:
    """
    Main prompt router with fail-open behavior.

    Usage:
        router = PromptRouter()
        decision = router.route(request)
        # Use decision.selected_model and decision.fallback_chain
    """

    def __init__(self):
        self._capabilities_registry: dict[str, ModelCapabilities] = {}
        self._load_capabilities()

    def _load_capabilities(self) -> None:
        """Load real model capabilities from the models + model_pricing catalog.

        Builds the registry from the DB so auto-routing scores over REAL costs,
        context lengths, and capability flags — and never selects a non-chat model
        (audio/image output like whisper). Falls back to a minimal default set on
        any failure so routing degrades gracefully rather than breaking.
        """
        try:
            rows = _fetch_capability_rows()
            registry = build_capabilities_registry(rows)
            if registry:
                self._capabilities_registry = registry
                logger.info(
                    "Loaded capabilities for %d chat models from catalog", len(registry)
                )
                return
            logger.warning("Catalog capability rows empty; loading defaults")
        except Exception as e:
            logger.error("Failed to load capabilities from catalog: %s", e)
        self._load_default_capabilities()

    def _load_default_capabilities(self) -> None:
        """Load minimal default capabilities for common models."""
        defaults = {
            "openai/gpt-4o-mini": ModelCapabilities(
                model_id="openai/gpt-4o-mini",
                provider="openai",
                tools=True,
                json_mode=True,
                json_schema=True,
                vision=True,
                max_context=128000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.00015,
                cost_per_1k_output=0.0006,
            ),
            "openai/gpt-4o": ModelCapabilities(
                model_id="openai/gpt-4o",
                provider="openai",
                tools=True,
                json_mode=True,
                json_schema=True,
                vision=True,
                max_context=128000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.0025,
                cost_per_1k_output=0.01,
            ),
            "anthropic/claude-3-haiku": ModelCapabilities(
                model_id="anthropic/claude-3-haiku",
                provider="anthropic",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=True,
                max_context=200000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.00025,
                cost_per_1k_output=0.00125,
            ),
            "anthropic/claude-3.5-sonnet": ModelCapabilities(
                model_id="anthropic/claude-3.5-sonnet",
                provider="anthropic",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=True,
                max_context=200000,
                tool_schema_adherence="high",
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
            ),
            "google/gemini-flash-1.5": ModelCapabilities(
                model_id="google/gemini-flash-1.5",
                provider="google",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=True,
                max_context=1000000,
                tool_schema_adherence="medium",
                cost_per_1k_input=0.000075,
                cost_per_1k_output=0.0003,
            ),
            "deepseek/deepseek-chat": ModelCapabilities(
                model_id="deepseek/deepseek-chat",
                provider="deepseek",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=False,
                max_context=64000,
                tool_schema_adherence="medium",
                cost_per_1k_input=0.00014,
                cost_per_1k_output=0.00028,
            ),
            "mistral/mistral-small": ModelCapabilities(
                model_id="mistral/mistral-small",
                provider="mistral",
                tools=True,
                json_mode=True,
                json_schema=False,
                vision=False,
                max_context=32000,
                tool_schema_adherence="medium",
                cost_per_1k_input=0.0002,
                cost_per_1k_output=0.0006,
            ),
            "meta-llama/llama-3.1-8b-instant": ModelCapabilities(
                model_id="meta-llama/llama-3.1-8b-instant",
                provider="meta",
                tools=False,
                json_mode=True,
                json_schema=False,
                vision=False,
                max_context=8192,
                tool_schema_adherence="low",
                cost_per_1k_input=0.0001,
                cost_per_1k_output=0.0001,
            ),
        }
        self._capabilities_registry = defaults
        logger.info(f"Loaded default capabilities for {len(defaults)} models")

    def route(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        user_preferences: UserRouterPreferences | None = None,
        conversation_id: str | None = None,
        tier: str = "small",
    ) -> RouterDecision:
        """
        Route a request to the optimal model.

        FAIL-OPEN: If anything takes too long or fails, returns default cheap model.

        Target total latency: < 2ms

        Args:
            messages: Conversation messages
            tools: Optional tools/functions
            response_format: Optional response format
            user_preferences: User routing preferences
            conversation_id: Optional conversation ID for sticky routing
            tier: Routing tier ("small", "medium", "large")

        Returns:
            RouterDecision with selected model and fallback chain
        """
        start = time.perf_counter()

        try:
            # 1. Extract required capabilities (< 0.1ms, no I/O)
            max_cost = user_preferences.max_cost_per_1k_tokens if user_preferences else None
            required = extract_capabilities(
                messages=messages,
                tools=tools,
                response_format=response_format,
                max_cost_per_1k=max_cost,
            )

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_capability_extraction")

            # 2. Get healthy models (single Redis GET, < 0.5ms)
            try:
                healthy = get_healthy_models_sync(tier)
            except Exception as e:
                logger.warning(f"Health snapshot read failed: {e}")
                # Use curated stable models instead of entire registry to avoid unhealthy models
                healthy = [
                    m for m in _get_stable_fallback_models() if m in self._capabilities_registry
                ]
                if not healthy:
                    # Last resort: use all models from registry
                    healthy = list(self._capabilities_registry.keys())

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_health_check")

            # 3. Filter by capabilities (< 0.2ms, no I/O)
            candidates = filter_by_capabilities(
                models=healthy,
                capabilities_registry=self._capabilities_registry,
                required=required,
            )

            if not candidates:
                logger.warning("No candidates after capability gating")
                return self._fail_open("no_capable_models", required=required)

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_capability_filter")

            # 4. Classify prompt (< 1ms, no I/O)
            classification = classify_prompt(messages)

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_classification")

            # 5. Select model (< 0.3ms, no I/O)
            optimization = RouterOptimization.BALANCED
            excluded = []
            preferred = []

            if user_preferences:
                optimization = user_preferences.default_optimization
                excluded = user_preferences.excluded_models
                preferred = user_preferences.preferred_models

            selected_model, reason = select_model(
                candidates=candidates,
                classification=classification,
                capabilities_registry=self._capabilities_registry,
                optimization=optimization,
                conversation_id=conversation_id,
                excluded_models=excluded,
                preferred_models=preferred,
            )

            # Check timeout
            if self._check_timeout(start):
                return self._fail_open("timeout_after_selection")

            # 6. Build fallback chain (< 0.2ms, no I/O)
            fallback_chain = build_fallback_chain(
                primary_model=selected_model,
                required_capabilities=required,
                healthy_candidates=candidates,
                capabilities_registry=self._capabilities_registry,
                tier=tier,
                excluded_models=excluded,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000

            # Get provider from capabilities
            selected_caps = self._capabilities_registry.get(selected_model)
            selected_provider = selected_caps.provider if selected_caps else None

            # Estimate cost
            estimated_cost = None
            if selected_caps:
                estimated_cost = selected_caps.cost_per_1k_input

            return RouterDecision(
                selected_model=selected_model,
                selected_provider=selected_provider,
                fallback_chain=fallback_chain,
                classification=classification,
                required_capabilities=required,
                estimated_cost_per_1k=estimated_cost,
                decision_time_ms=elapsed_ms,
                reason=reason,
            )

        except Exception as e:
            logger.exception(f"Router failed with exception: {e}")
            return self._fail_open(f"exception:{type(e).__name__}")

    def _check_timeout(self, start: float) -> bool:
        """Check if we've exceeded the timeout budget."""
        elapsed_ms = (time.perf_counter() - start) * 1000
        return elapsed_ms > ROUTER_TIMEOUT_MS

    def _fail_open(
        self,
        reason: str,
        required: RequiredCapabilities | None = None,
    ) -> RouterDecision:
        """
        Return safe default when router can't complete.
        This is the fail-open behavior.
        """
        logger.warning(f"Router failing open: {reason}")

        return RouterDecision(
            selected_model=DEFAULT_CHEAP_MODEL,
            selected_provider=DEFAULT_PROVIDER,
            fallback_chain=[],
            classification=None,
            required_capabilities=required,
            estimated_cost_per_1k=0.00015,  # gpt-4o-mini cost
            decision_time_ms=0,
            reason=f"fail_open:{reason}",
        )

    def get_capabilities(self, model_id: str) -> ModelCapabilities | None:
        """Get capabilities for a model."""
        return self._capabilities_registry.get(model_id)

    def list_capable_models(self, required: RequiredCapabilities) -> list[str]:
        """List all models that satisfy given requirements."""
        return filter_by_capabilities(
            models=list(self._capabilities_registry.keys()),
            capabilities_registry=self._capabilities_registry,
            required=required,
        )


# Global router instance
_router: PromptRouter | None = None


def get_router() -> PromptRouter:
    """Get global router instance."""
    global _router
    if _router is None:
        _router = PromptRouter()
    return _router


def route_request(
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    user_preferences: UserRouterPreferences | None = None,
    conversation_id: str | None = None,
    tier: str = "small",
) -> RouterDecision:
    """Convenience function to route a request."""
    router = get_router()
    return router.route(
        messages=messages,
        tools=tools,
        response_format=response_format,
        user_preferences=user_preferences,
        conversation_id=conversation_id,
        tier=tier,
    )


# Aliases that mean "auto-route to the best model for the task". The canonical
# trigger is the `router*` prefix; `auto` / `gatewayz/auto` are OpenRouter-style
# ergonomic aliases. `openrouter/auto` is deliberately EXCLUDED — it is a real
# passthrough model served by OpenRouter and must reach the provider unchanged.
_AUTO_ROUTE_BARE_ALIASES = {"auto", "gatewayz/auto"}


def _normalize_auto_route_model(model: str) -> str:
    """Fold an `auto*` alias onto the equivalent `router*` string; else unchanged.

    'auto' -> 'router', 'auto:price' -> 'router:price', 'gatewayz/auto' -> 'router'.
    Anything not an auto alias is returned as-is (lower-cased for matching).
    """
    m = (model or "").lower().strip()
    if m in _AUTO_ROUTE_BARE_ALIASES:
        return "router"
    if m.startswith("auto:"):
        return "router:" + m[len("auto:"):]
    return m


def is_auto_route_request(model: str) -> bool:
    """Check if a model string indicates auto-routing.

    Triggers on the `router*` prefix and the ergonomic `auto` / `auto:<opt>` /
    `gatewayz/auto` aliases. Does NOT trigger on `openrouter/auto` (a real model).
    """
    if not model:
        return False
    return _normalize_auto_route_model(model).startswith("router")


def parse_auto_route_options(model: str) -> tuple[str, RouterOptimization]:
    """
    Parse auto-route model string into tier and optimization.

    Examples:
        "router" -> ("small", BALANCED)
        "router:small" -> ("small", BALANCED)
        "router:medium" -> ("medium", BALANCED)
        "router:price" -> ("small", PRICE)
        "router:quality" -> ("medium", QUALITY)
    """
    normalized = _normalize_auto_route_model(model)
    if not normalized.startswith("router"):
        return ("small", RouterOptimization.BALANCED)

    parts = normalized.split(":")
    if len(parts) == 1:
        return ("small", RouterOptimization.BALANCED)

    modifier = parts[1]

    # Tier modifiers
    if modifier == "small":
        return ("small", RouterOptimization.BALANCED)
    elif modifier == "medium":
        return ("medium", RouterOptimization.BALANCED)
    elif modifier == "large":
        return ("large", RouterOptimization.BALANCED)

    # Optimization modifiers
    elif modifier == "price":
        return ("small", RouterOptimization.PRICE)
    elif modifier == "quality":
        return ("medium", RouterOptimization.QUALITY)
    elif modifier == "fast":
        return ("small", RouterOptimization.FAST)
    elif modifier == "balanced":
        return ("small", RouterOptimization.BALANCED)

    return ("small", RouterOptimization.BALANCED)
