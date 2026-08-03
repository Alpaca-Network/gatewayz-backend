"""Moonshot's catalog ids carry a "moonshot/" prefix but its API wants the bare
id — the adapter must strip it, else every Kimi call 404s."""

from src.services.providers.adapter_configs import ADAPTERS


def test_moonshot_strips_slug_prefix():
    a = ADAPTERS["moonshot"]
    assert a._resolve_model("moonshot/kimi-k2.6") == "kimi-k2.6"
    assert a._resolve_model("moonshot/kimi-k3") == "kimi-k3"


def test_moonshot_leaves_bare_ids_untouched():
    a = ADAPTERS["moonshot"]
    assert a._resolve_model("kimi-k3") == "kimi-k3"
