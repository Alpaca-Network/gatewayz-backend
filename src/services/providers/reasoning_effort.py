"""Normalize one ``reasoning_effort`` knob across provider dialects.

Callers send a single ``reasoning_effort`` of "low" / "medium" / "high". Every
provider spells that differently, and getting it wrong is not a soft failure —
each of the shapes below was checked against the live APIs, and the wrong one
returns 400:

On the OpenAI-COMPATIBLE chat surface — which is how this gateway reaches every
provider, Anthropic included — the parameter is `reasoning_effort` everywhere:

    OpenAI  reasoning models   reasoning_effort: low|medium|high    200
                               "minimal"                            400 unsupported value
            non-reasoning      reasoning_effort                     400 Unrecognized argument
    xAI     grok-4             reasoning_effort                     200 (usage.reasoning_tokens)
    Anthropic (compat)         reasoning_effort                     200
                               thinking + output_config             400 not available via compat
    Moonshot                   reasoning_effort                     200

Anthropic's NATIVE /v1/messages surface is the exception and needs a different
shape entirely — see apply_reasoning_effort_anthropic_native.

Because OpenAI rejects the parameter outright on models that do not reason, the
effort is DROPPED rather than forwarded when a model does not support it. A user
asking for effort on gpt-4o-mini gets an ordinary answer, not a 400.

Provider-dialect knowledge lives here, in the adapter layer, and never in
routing code (North Star §5).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

VALID_EFFORTS = ("low", "medium", "high")

# OpenAI families that take reasoning_effort. These are exactly the families
# that also reject `max_tokens`, so one predicate drives both decisions.
_OPENAI_REASONING_PATTERN = re.compile(r"^(o\d|gpt-5)", re.IGNORECASE)

# Anthropic generations that want thinking.adaptive + output_config.effort
# instead of a raw token budget.
_ANTHROPIC_ADAPTIVE_PATTERN = re.compile(
    r"(claude-)?(sonnet-5|opus-4-7|opus-4-8|fable-5|haiku-5)", re.IGNORECASE
)

# Effort → thinking budget for the older Anthropic shape. The floor is the
# API's own minimum (budget_tokens must be >= 1024).
_ANTHROPIC_BUDGET_BY_EFFORT = {"low": 1024, "medium": 4096, "high": 16384}

# Every gateway reached over an OpenAI-compatible chat endpoint takes the
# parameter verbatim — Anthropic included, because the gateway talks to its
# OpenAI-compatible surface rather than /v1/messages.
_PASSTHROUGH_GATEWAYS = frozenset({"openai", "anthropic", "xai", "moonshot", "deepseek", "zai"})


def _strip_gateway_prefix(model: str, gateway: str) -> str:
    return model[len(gateway) + 1 :] if model.startswith(f"{gateway}/") else model


def is_reasoning_model(gateway: str, model: str) -> bool:
    """True when *model* reasons, and therefore takes an effort setting.

    Deliberately not read from ``models.is_reasoning``: that column is populated
    by name heuristics and flags DeepSeek-R1 and ``*-Thinking`` while missing
    gpt-5.6-sol, claude-sonnet-5 and grok-4 — precisely the models this matters
    for.
    """
    name = _strip_gateway_prefix(model or "", gateway or "")
    gw = (gateway or "").lower()

    if gw == "openai":
        return bool(_OPENAI_REASONING_PATTERN.match(name))
    if gw == "anthropic":
        # Every Claude 4+ generation supports extended thinking.
        return "claude" in name.lower() or name.lower().startswith(("sonnet", "opus", "haiku"))
    if gw == "xai":
        return name.lower().startswith("grok-4") or "reasoning" in name.lower()
    if gw == "moonshot":
        return name.lower().startswith("kimi")
    return False


def uses_max_completion_tokens(gateway: str, model: str) -> bool:
    """True when the model rejects ``max_tokens`` and wants the newer name.

    OpenAI's reasoning generations answer `max_tokens` with
    "Unsupported parameter: 'max_tokens' is not supported".
    """
    return (gateway or "").lower() == "openai" and is_reasoning_model(gateway, model)


def normalize_token_limit(payload: dict, gateway: str, model: str) -> dict:
    """Rename ``max_tokens`` to ``max_completion_tokens`` where required."""
    if "max_tokens" in payload and uses_max_completion_tokens(gateway, model):
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    return payload


def apply_reasoning_effort(payload: dict, gateway: str, model: str, effort: str | None) -> dict:
    """Translate *effort* into the dialect *gateway* speaks, in place.

    Returns the payload unchanged when there is no effort to apply, the value is
    not one we accept, or the model does not reason — the last case being the
    one that would otherwise 400 on OpenAI.
    """
    if not effort:
        return payload

    effort = str(effort).strip().lower()
    if effort not in VALID_EFFORTS:
        logger.debug("Ignoring unsupported reasoning_effort %r", effort)
        return payload

    gw = (gateway or "").lower()

    if not is_reasoning_model(gw, model):
        # OpenAI 400s on unknown arguments, so forwarding here would turn a
        # harmless request into an error.
        logger.debug("Dropping reasoning_effort for non-reasoning model %s/%s", gw, model)
        return payload

    if gw in _PASSTHROUGH_GATEWAYS:
        payload["reasoning_effort"] = effort
        return payload

    logger.debug("No reasoning_effort dialect known for gateway %s; dropping", gw)
    return payload


def apply_reasoning_effort_anthropic_native(payload: dict, model: str, effort: str | None) -> dict:
    """Translate effort for Anthropic's NATIVE /v1/messages surface.

    Distinct from the OpenAI-compatible endpoint above, which takes
    `reasoning_effort` and rejects this shape outright:
    "Adaptive thinking is not available via ...". Both were confirmed live.

    Two generations, and Anthropic names the split in its own 400:
    '"thinking.type.enabled" is not supported for this model. Use
    "thinking.type.adaptive" and "output_config.effort"'.
    """
    if not effort:
        return payload
    effort = str(effort).strip().lower()
    if effort not in VALID_EFFORTS or not is_reasoning_model("anthropic", model):
        return payload

    name = _strip_gateway_prefix(model or "", "anthropic")
    if _ANTHROPIC_ADAPTIVE_PATTERN.search(name):
        payload["thinking"] = {"type": "adaptive"}
        payload["output_config"] = {**(payload.get("output_config") or {}), "effort": effort}
        return payload

    # Older generation takes a raw budget, constrained by the API to
    # 1024 <= budget_tokens < max_tokens. When max_tokens leaves no room for the
    # minimum, drop the request rather than emit a payload that 400s — the same
    # "drop, never break the call" rule applied everywhere else here.
    budget = _ANTHROPIC_BUDGET_BY_EFFORT[effort]
    max_tokens = payload.get("max_tokens")
    if isinstance(max_tokens, int):
        if max_tokens <= 1024:
            logger.debug(
                "Dropping thinking budget: max_tokens=%s leaves no room for the 1024 minimum",
                max_tokens,
            )
            return payload
        budget = min(budget, max_tokens - 1)
    payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
    return payload
