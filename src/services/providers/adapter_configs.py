"""Config table for OpenAI-compatible providers served by ``openai_compat``.

Each entry replaces one former ``<slug>_client.py`` inference trio. Values are
copied 1:1 from the old clients:

  - base_url / api_key_env: identical endpoints and Config attributes,
  - client_factory: the same pooled getters the old clients called
    (deepinfra deliberately has none — its old client built a plain OpenAI
    client per request),
  - quirks: the middleware each old client wired up (circuit breaker + sentry
    for together and groq; request timing for groq only).

Catalog fetch/normalization for these providers lives in the per-provider
``<slug>_catalog.py`` modules and is intentionally NOT unified here: the
pricing-unit math is provider-specific and regression-tested
(tests/services/test_provider_price_units.py).
"""

from __future__ import annotations

from src.services.circuit_breaker import CircuitBreakerConfig
from src.services.connection_pool import (
    get_fireworks_pooled_client,
    get_groq_pooled_client,
    get_together_pooled_client,
    get_zai_pooled_client,
)
from src.services.providers.openai_compat import (
    OpenAICompatAdapter,
    ProviderConfig,
    Quirks,
    make_adapter,
)

# Identical values to the old GROQ_CIRCUIT_CONFIG and TOGETHER_CIRCUIT_CONFIG
# (they were the same numbers in both client modules).
_STANDARD_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,  # Open after 5 consecutive failures
    success_threshold=2,  # Close after 2 consecutive successes
    timeout_seconds=60,  # Wait 60s before retrying
    failure_window_seconds=60,  # Measure failure rate over 60s
    failure_rate_threshold=0.5,  # Open if >50% failure rate
    min_requests_for_rate=10,  # Need at least 10 requests
)

ADAPTER_CONFIGS: dict[str, ProviderConfig] = {
    "deepinfra": ProviderConfig(
        slug="deepinfra",
        base_url="https://api.deepinfra.com/v1/openai",
        api_key_env="DEEPINFRA_API_KEY",
        display_name="DeepInfra",
        # No client_factory: parity with the old client, which constructed a
        # plain OpenAI client per request instead of using the pool.
    ),
    "together": ProviderConfig(
        slug="together",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        display_name="Together",
        client_factory=get_together_pooled_client,
        quirks=Quirks(circuit_breaker=_STANDARD_CIRCUIT_CONFIG, sentry=True),
    ),
    "fireworks": ProviderConfig(
        slug="fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        display_name="Fireworks",
        client_factory=get_fireworks_pooled_client,
    ),
    "groq": ProviderConfig(
        slug="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        display_name="Groq",
        client_factory=get_groq_pooled_client,
        quirks=Quirks(circuit_breaker=_STANDARD_CIRCUIT_CONFIG, sentry=True, timing=True),
    ),
    "zai": ProviderConfig(
        slug="zai",
        base_url="https://api.z.ai/api/paas/v4",
        api_key_env="ZAI_API_KEY",
        display_name="Z.AI",
        client_factory=get_zai_pooled_client,
    ),
    # -- Tier-2 providers (Task 18) ---------------------------------------
    # None of these use a pooled client_factory or middleware quirks: parity
    # with the deepinfra config (plain OpenAI client built per request).
    "deepseek": ProviderConfig(
        slug="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
    ),
    "moonshot": ProviderConfig(
        slug="moonshot",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
        display_name="Moonshot AI",
        # Catalog stores ids as "moonshot/kimi-k2.6"; Moonshot's API expects the
        # bare id ("kimi-k2.6"). Strip the slug prefix before the upstream call.
        model_prefix="moonshot/",
    ),
    "minimax": ProviderConfig(
        slug="minimax",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        display_name="MiniMax",
    ),
    # Verified live: api.xiaomimimo.com resolves to Xiaomi-owned infra
    # (mimo-pri-azams.alb.xiaomi.com) and GET /v1/models returns an
    # OpenAI-shaped 401 without a key. No XIAOMI_API_KEY is provisioned yet
    # (see task-18-report.md) so this entry is code-only / untested live.
    "xiaomi": ProviderConfig(
        slug="xiaomi",
        base_url="https://api.xiaomimimo.com/v1",
        api_key_env="XIAOMI_API_KEY",
        display_name="Xiaomi MiMo",
    ),
}

ADAPTERS: dict[str, OpenAICompatAdapter] = {
    slug: make_adapter(cfg) for slug, cfg in ADAPTER_CONFIGS.items()
}
