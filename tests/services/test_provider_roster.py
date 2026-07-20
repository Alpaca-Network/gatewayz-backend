ROSTER = {
    "openai","anthropic","google-vertex","xai","deepseek","alibaba","xiaomi",
    "moonshot","minimax","deepinfra","novita","together","fireworks","groq",
    "cerebras","perplexity","mistral","featherless",
}
ENABLED_DEFAULT = ROSTER | {"openrouter"}
CUT = {
    "chutes","aimo","near","fal","huggingface","nebius","clarifai","simplismart",
    "cloudflare-workers-ai","modelz","cohere","zai","morpheus","sybil","canopywave",
    "akash","alpaca-network","nosana","code-router",
}

def test_fallback_slugs_are_roster_plus_openrouter():
    from src.db.providers_db import _FALLBACK_PROVIDER_SLUGS
    assert set(_FALLBACK_PROVIDER_SLUGS) == ENABLED_DEFAULT

def test_env_var_map_has_no_cut_providers():
    from src.services.provider_model_sync_service import PROVIDER_ENV_VAR_MAP
    assert CUT.isdisjoint(PROVIDER_ENV_VAR_MAP.keys())

def test_enabled_providers_default_is_roster():
    import os, importlib
    os.environ.pop("ENABLED_PROVIDERS", None)
    import src.config.config as c; importlib.reload(c)
    assert c.Config.ENABLED_PROVIDERS == frozenset(ENABLED_DEFAULT)
