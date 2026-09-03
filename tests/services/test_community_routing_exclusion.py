"""Community routing exclusion (gatewayz-backend#2262 #2265, spec §1/§4):
opt-in only, never a failover target, never chosen by auto-routing, and
absent from PROVIDER_ROUTING entirely when the flag is off.
"""

from __future__ import annotations

import importlib

from src.config import Config
from src.services.provider_failover import build_provider_failover_chain


def test_explicit_community_request_routes_to_adapter_only():
    chain = build_provider_failover_chain("community")
    assert chain == ["community"]


def test_community_never_enters_another_providers_chain():
    for initial in ["openrouter", "deepinfra", "groq", "together", None, ""]:
        chain = build_provider_failover_chain(initial)
        assert "community" not in chain, f"community leaked into chain for {initial!r}: {chain}"


def test_community_never_enters_chain_even_if_db_registry_ranks_it(monkeypatch):
    """Defense in depth: even if src.services.gateway_registry ever grows a
    'community' entry with a failover_priority (it should not -- see
    gateway_registry.py's comment), the explicit exclusion still holds.
    """

    def fake_registry():
        return {
            "openrouter": {"failover_priority": 0},
            "community": {"failover_priority": 1},
        }

    monkeypatch.setattr(
        "src.services.gateway_registry.get_gateway_registry", fake_registry, raising=False
    )
    chain = build_provider_failover_chain("openrouter")
    assert "community" not in chain


def test_community_absent_from_provider_routing_when_flag_off(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", False, raising=False)
    import src.handlers.provider_registry as provider_registry_module

    importlib.reload(provider_registry_module)
    try:
        assert "community" not in provider_registry_module.PROVIDER_ROUTING
    finally:
        # Force back to the real production default (off) deterministically:
        # monkeypatch's own teardown of COMMUNITY_ROUTING_ENABLED runs after
        # this test returns, too late for this reload to observe it.
        monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", False, raising=False)
        importlib.reload(provider_registry_module)


def test_community_present_in_provider_routing_when_flag_on(monkeypatch):
    monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", True, raising=False)
    import src.handlers.provider_registry as provider_registry_module

    importlib.reload(provider_registry_module)
    try:
        assert "community" in provider_registry_module.PROVIDER_ROUTING
        entry = provider_registry_module.PROVIDER_ROUTING["community"]
        assert entry["request"] and entry["process"] and entry["stream"]
    finally:
        monkeypatch.setattr(Config, "COMMUNITY_ROUTING_ENABLED", False, raising=False)
        importlib.reload(provider_registry_module)


def test_community_not_in_fallback_provider_priority_tuple():
    from src.services.provider_failover import FALLBACK_PROVIDER_PRIORITY

    assert "community" not in FALLBACK_PROVIDER_PRIORITY
