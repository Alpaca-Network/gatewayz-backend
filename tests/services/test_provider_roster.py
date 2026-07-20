ROSTER = {  # full North Star §3 roster (18); dark 6 included here
    "openai","anthropic","google-vertex","xai","deepseek","alibaba","xiaomi",
    "moonshot","minimax","deepinfra","novita","together","fireworks","groq",
    "cerebras","perplexity","mistral","featherless",
}
DARK = {"deepseek","moonshot","minimax","xiaomi","perplexity","mistral"}
LIVE_ROSTER = ROSTER - DARK  # 12 providers that have client code + keys today
ENABLED_DEFAULT = LIVE_ROSTER | {"openrouter"}  # 13 — dark 6 withheld until keys land
ENV_MAP_KEYS = ROSTER | {"openrouter"}  # 19 — dark 6 keep their env-var mapping
CUT = {
    "chutes","aimo","near","fal","huggingface","nebius","clarifai","simplismart",
    "cloudflare-workers-ai","modelz","cohere","zai","morpheus","sybil","canopywave",
    "akash","alpaca-network","nosana","code-router",
}

def test_fallback_slugs_are_live_roster_plus_openrouter():
    from src.db.providers_db import _FALLBACK_PROVIDER_SLUGS
    assert set(_FALLBACK_PROVIDER_SLUGS) == ENABLED_DEFAULT

def test_env_var_map_is_roster_plus_openrouter_no_cut():
    from src.services.provider_model_sync_service import PROVIDER_ENV_VAR_MAP
    keys = set(PROVIDER_ENV_VAR_MAP.keys())
    assert CUT.isdisjoint(keys)
    assert keys == ENV_MAP_KEYS

def test_enabled_providers_default_excludes_dark():
    import os, importlib
    os.environ.pop("ENABLED_PROVIDERS", None)
    import src.config.config as c; importlib.reload(c)
    assert c.Config.ENABLED_PROVIDERS == frozenset(ENABLED_DEFAULT)
    assert DARK.isdisjoint(c.Config.ENABLED_PROVIDERS)  # dark 6 not enabled
