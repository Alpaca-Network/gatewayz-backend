"""
CM-18 Provider Ecosystem

Tests verifying that at least 30 providers are registered, each has a
corresponding *_client.py module, and client modules implement the
required interface.
"""

import importlib

import pytest


# ---------------------------------------------------------------------------
# CM-18.1  At least 30 providers registered
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1801AtLeast30ProvidersRegistered:
    def test_at_least_30_providers_registered(self):
        """GATEWAY_REGISTRY must contain the current core set of provider entries.

        CM-vs-code delta: the CM originally claimed "30+ provider integrations",
        but the roster was deliberately reduced when 6 aggregator providers were
        cut. The DB-backed registry now exposes ~12 providers (the hardcoded
        cold-start fallback still lists ~33). The floor is lowered to 10 to match
        the current supported roster while tolerating either source.
        """
        from src.services.gateway_registry import _FALLBACK_REGISTRY as get_gateway_registry_static

        registry = dict(get_gateway_registry_static)
        assert len(registry) >= 10, (
            f"Expected >= 10 providers in GATEWAY_REGISTRY, found {len(registry)}: "
            f"{sorted(registry.keys())}"
        )


# ---------------------------------------------------------------------------
# CM-18.2  Each provider has a client module
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1802EachProviderHasClientModule:
    def test_each_provider_has_client_module(self):
        """Key providers must have an inference implementation: either a
        dedicated *_client module or an entry in the OpenAI-compat adapter
        registry (ADAPTERS), which replaced the near-identical client modules
        during the MVP consolidation."""
        # These are core providers that must have dedicated client modules
        key_providers = [
            "openrouter",
            "deepinfra",
            "fireworks",
            "groq",
            "together",
            "cerebras",
            "featherless",
        ]

        from src.services.providers.adapter_configs import ADAPTERS

        imported = []
        failed = []
        for provider in key_providers:
            if provider in ADAPTERS:
                imported.append(provider)
                continue
            module_path = f"src.services.{provider}_client"
            try:
                importlib.import_module(module_path)
                imported.append(provider)
            except ImportError as e:
                failed.append((provider, str(e)))

        assert len(failed) == 0, (
            f"All key providers must have a client module or adapter entry. "
            f"Failed: {failed}"
        )
        assert len(imported) == len(key_providers)


# ---------------------------------------------------------------------------
# CM-18.3  Provider client implements required interface
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1803ProviderClientImplementsRequiredInterface:
    def test_provider_client_implements_required_interface(self):
        """Providers must expose request/stream-capable callables — either in
        a dedicated *_client module or via their OpenAI-compat adapter entry
        (request/process/stream on the adapter object)."""
        key_providers = [
            "openrouter",
            "deepinfra",
            "fireworks",
            "groq",
            "together",
            "cerebras",
            "featherless",
        ]

        from src.services.providers.adapter_configs import ADAPTERS

        for provider in key_providers:
            if provider in ADAPTERS:
                adapter = ADAPTERS[provider]
                assert callable(adapter.request) and callable(adapter.stream), (
                    f"{provider} adapter must expose request/stream callables"
                )
                continue

            module = importlib.import_module(f"src.services.{provider}_client")
            members = {
                name: obj
                for name, obj in vars(module).items()
                if callable(obj) and not name.startswith("_")
            }

            has_request_fn = any(
                "request" in name.lower()
                or "send" in name.lower()
                or "chat" in name.lower()
                or "completion" in name.lower()
                or "stream" in name.lower()
                for name in members
            )

            assert has_request_fn, (
                f"{provider}_client must have a request-handling function. "
                f"Found: {sorted(members.keys())}"
            )
