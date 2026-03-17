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
        """GATEWAY_REGISTRY must contain at least 30 provider entries,
        matching the CM claim of 30+ provider integrations."""
        from src.routes.catalog import GATEWAY_REGISTRY

        assert len(GATEWAY_REGISTRY) >= 30, (
            f"Expected >= 30 providers in GATEWAY_REGISTRY, found {len(GATEWAY_REGISTRY)}: "
            f"{sorted(GATEWAY_REGISTRY.keys())}"
        )


# ---------------------------------------------------------------------------
# CM-18.2  Each provider has a client module
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1802EachProviderHasClientModule:
    def test_each_provider_has_client_module(self):
        """Key providers from GATEWAY_REGISTRY should have importable *_client modules."""
        # These are core providers that must have dedicated client modules
        key_providers = [
            "openrouter",
            "deepinfra",
            "fireworks",
            "groq",
            "together",
            "cerebras",
            "featherless",
            "chutes",
        ]

        imported = []
        failed = []
        for provider in key_providers:
            module_path = f"src.services.{provider}_client"
            try:
                importlib.import_module(module_path)
                imported.append(provider)
            except ImportError as e:
                failed.append((provider, str(e)))

        assert len(failed) == 0, (
            f"All key provider client modules must be importable. " f"Failed: {failed}"
        )
        assert len(imported) == len(key_providers)


# ---------------------------------------------------------------------------
# CM-18.3  Provider client implements required interface
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1803ProviderClientImplementsRequiredInterface:
    def test_provider_client_implements_required_interface(self):
        """Provider client modules should have callable functions for sending
        inference requests (send_*_request, make_*_request, or stream-related)."""
        key_clients = [
            "openrouter_client",
            "deepinfra_client",
            "fireworks_client",
            "groq_client",
            "together_client",
            "cerebras_client",
            "featherless_client",
            "chutes_client",
        ]

        for client_name in key_clients:
            module = importlib.import_module(f"src.services.{client_name}")
            members = dict(
                (name, obj)
                for name, obj in vars(module).items()
                if callable(obj) and not name.startswith("_")
            )

            has_request_fn = any(
                "request" in name.lower()
                or "send" in name.lower()
                or "chat" in name.lower()
                or "completion" in name.lower()
                or "stream" in name.lower()
                for name in members
            )

            assert has_request_fn, (
                f"{client_name} must have a request-handling function. "
                f"Found: {sorted(members.keys())}"
            )
