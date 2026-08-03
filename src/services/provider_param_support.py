"""Per-provider generation-parameter support matrix.

Historically the chat route forwarded only six generation parameters
(``max_tokens``, ``temperature``, ``top_p``, ``frequency_penalty``,
``presence_penalty``, ``tools``) even though ``ProxyRequest`` advertises the
full OpenAI surface. Everything else -- notably ``tool_choice``,
``response_format`` and ``stop`` -- was accepted by the schema and then
silently dropped, so forced tool calls degraded to ``auto`` and JSON mode did
nothing.

Forwarding everything unconditionally is not safe either: providers expose
different subsets of the OpenAI schema and most of them reject unknown fields
with a 400 rather than ignoring them. This module is the single place that
decides, per provider, which parameters may be forwarded.

Design notes:

* ``OPENAI_COMPATIBLE_PARAMS`` is the baseline. Nearly every client in
  ``src/services/providers/`` forwards ``**kwargs`` into an OpenAI-shaped SDK
  or endpoint, so the OpenAI surface is the correct default.
* ``PROVIDER_UNSUPPORTED_PARAMS`` records the known exceptions. Only providers
  we have positively verified are listed; an unlisted provider gets the
  baseline. Entries are deliberately conservative -- when in doubt, drop the
  parameter rather than risk a 400 on a paid request.
* Dropped parameters are reported back to the caller so the route can surface
  an ``X-Gatewayz-Dropped-Params`` header instead of failing silently. Silent
  divergence between "what the client asked for" and "what the model saw" is
  the exact failure mode this module exists to prevent.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Parameters supported by a well-behaved OpenAI-compatible chat completions
# endpoint. This is the default allowlist for any provider not listed in
# PROVIDER_UNSUPPORTED_PARAMS.
OPENAI_COMPATIBLE_PARAMS: frozenset[str] = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "n",
        "seed",
        "user",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "response_format",
        "logprobs",
        "top_logprobs",
        "logit_bias",
        "stream_options",
    }
)

# The subset that virtually every provider accepts. Used as the fallback when a
# provider is explicitly marked as minimal.
UNIVERSAL_PARAMS: frozenset[str] = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "stop",
    }
)

# Known exceptions to the OpenAI baseline, keyed by provider slug.
#
# anthropic: served through Anthropic's OpenAI-compatibility endpoint, which
#   ignores penalties and rejects logit_bias/logprobs. Prompt caching is not
#   available on that endpoint at all -- see anthropic_native_client.py, which
#   bypasses this path entirely when cache_control is present.
# google-vertex / cohere: native request shapes with their own transformers;
#   the OpenAI-only knobs never reach the wire.
PROVIDER_UNSUPPORTED_PARAMS: dict[str, frozenset[str]] = {
    "anthropic": frozenset(
        {
            "frequency_penalty",
            "presence_penalty",
            "logit_bias",
            "logprobs",
            "top_logprobs",
            "seed",
            "n",
        }
    ),
    "google-vertex": frozenset(
        {
            "frequency_penalty",
            "presence_penalty",
            "logit_bias",
            "logprobs",
            "top_logprobs",
            "parallel_tool_calls",
            "n",
        }
    ),
    # Fast inference providers that implement a deliberately reduced schema.
    "cerebras": frozenset({"logit_bias", "logprobs", "top_logprobs", "parallel_tool_calls"}),
    "groq": frozenset({"logit_bias", "logprobs", "top_logprobs"}),
    # Chinese-lab endpoints reached through the shared OpenAI-compatible
    # adapter. They implement the core chat surface but not the sampling
    # diagnostics.
    "moonshot": frozenset({"logit_bias", "logprobs", "top_logprobs", "n"}),
    "minimax": frozenset({"logit_bias", "logprobs", "top_logprobs", "n"}),
    "xiaomi": frozenset({"logit_bias", "logprobs", "top_logprobs", "n"}),
    "zai": frozenset({"logit_bias", "logprobs", "top_logprobs", "n"}),
    "alibaba-cloud": frozenset({"logit_bias", "logprobs", "top_logprobs"}),
    "deepseek": frozenset({"logit_bias", "top_logprobs", "n"}),
}

# Providers whose clients accept only the universal subset. Anything not in
# UNIVERSAL_PARAMS is dropped for these. Empty today — every provider on the
# current roster speaks a usable share of the OpenAI surface — but kept so a
# future minimal provider has an obvious home.
MINIMAL_PARAM_PROVIDERS: frozenset[str] = frozenset()


def supported_params_for(provider: str | None) -> frozenset[str]:
    """Return the set of generation parameters ``provider`` accepts."""
    slug = (provider or "").strip().lower()
    if slug in MINIMAL_PARAM_PROVIDERS:
        return UNIVERSAL_PARAMS
    unsupported = PROVIDER_UNSUPPORTED_PARAMS.get(slug, frozenset())
    return OPENAI_COMPATIBLE_PARAMS - unsupported


def filter_params_for_provider(
    provider: str | None,
    params: dict,
) -> tuple[dict, list[str]]:
    """Split ``params`` into what ``provider`` accepts and what must be dropped.

    Args:
        provider: Provider slug (e.g. ``"openrouter"``, ``"anthropic"``).
        params: Generation parameters as assembled by the caller.

    Returns:
        ``(filtered, dropped)`` where ``filtered`` is safe to forward and
        ``dropped`` is the sorted list of parameter names that were removed.
        ``None``-valued entries are removed without being reported as dropped:
        they carry no user intent.
    """
    supported = supported_params_for(provider)
    filtered: dict = {}
    dropped: list[str] = []

    for key, value in params.items():
        if value is None:
            continue
        if key in supported:
            filtered[key] = value
        else:
            dropped.append(key)

    if dropped:
        logger.info(
            "Dropped %d unsupported param(s) for provider '%s': %s",
            len(dropped),
            provider,
            ", ".join(sorted(dropped)),
        )

    return filtered, sorted(dropped)
