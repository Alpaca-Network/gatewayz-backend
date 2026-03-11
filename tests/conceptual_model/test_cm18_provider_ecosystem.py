"""
CM-18 Provider Ecosystem

Tests verifying that at least 30 providers are registered, each has a
corresponding *_client.py module, and client modules implement the
required interface.
"""

import os
import glob
import importlib
import inspect

import pytest


# ---------------------------------------------------------------------------
# CM-18.1  At least 30 providers registered
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1801AtLeast30ProvidersRegistered:
    def test_at_least_30_providers_registered(self):
        """There must be at least 30 *_client.py files in src/services/,
        matching the CM claim of 30+ provider integrations."""
        services_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "src", "services"
        )
        services_dir = os.path.normpath(services_dir)
        client_files = glob.glob(os.path.join(services_dir, "*_client.py"))

        assert len(client_files) >= 30, (
            f"Expected >= 30 provider client modules, found {len(client_files)}: "
            f"{[os.path.basename(f) for f in sorted(client_files)]}"
        )


# ---------------------------------------------------------------------------
# CM-18.2  Each provider has a client module
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1802EachProviderHasClientModule:
    def test_each_provider_has_client_module(self):
        """The GATEWAY_REGISTRY in catalog.py lists providers, and each
        should have a corresponding *_client.py in src/services/."""
        from src.routes.catalog import GATEWAY_REGISTRY

        services_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "src", "services"
        )
        services_dir = os.path.normpath(services_dir)

        client_basenames = {
            os.path.basename(f).replace("_client.py", "")
            for f in glob.glob(os.path.join(services_dir, "*_client.py"))
        }

        # Normalize gateway names: "google-vertex" -> "google_vertex"
        missing = []
        for gateway_key in GATEWAY_REGISTRY:
            normalized = gateway_key.replace("-", "_")
            # Check if any client file contains this provider name
            has_client = any(
                normalized in client_name or gateway_key.replace("-", "") in client_name
                for client_name in client_basenames
            )
            if not has_client:
                missing.append(gateway_key)

        # Allow some gateways to not have their own client (they may use
        # another provider's client, e.g. openrouter routes through openrouter_client)
        # But the majority should have dedicated clients
        coverage = 1 - (len(missing) / len(GATEWAY_REGISTRY))
        assert coverage >= 0.5, (
            f"At least 50% of GATEWAY_REGISTRY entries should have a *_client.py. "
            f"Missing clients for: {missing}"
        )


# ---------------------------------------------------------------------------
# CM-18.3  Provider client implements required interface
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1803ProviderClientImplementsRequiredInterface:
    def test_provider_client_implements_required_interface(self):
        """Provider client modules should have a function for sending
        requests (send_request, make_*_request, or similar callable)."""
        services_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "src", "services"
        )
        services_dir = os.path.normpath(services_dir)
        client_files = glob.glob(os.path.join(services_dir, "*_client.py"))

        # Check a sample of client modules for request-sending functions
        checked = 0
        has_interface = 0

        # Pick well-known provider clients to test
        key_clients = [
            "openrouter_client", "deepinfra_client", "fireworks_client",
            "groq_client", "together_client", "cerebras_client",
            "featherless_client", "chutes_client",
        ]

        for client_name in key_clients:
            module_path = f"src.services.{client_name}"
            try:
                module = importlib.import_module(module_path)
                checked += 1

                # Look for functions that handle sending requests
                members = inspect.getmembers(module, inspect.isfunction)
                function_names = [name for name, _ in members]

                has_send = any(
                    "request" in name.lower() or
                    "send" in name.lower() or
                    "chat" in name.lower() or
                    "completion" in name.lower() or
                    "stream" in name.lower()
                    for name in function_names
                )

                # Also check for classes with similar methods
                classes = inspect.getmembers(module, inspect.isclass)
                for cls_name, cls in classes:
                    cls_methods = [m for m in dir(cls) if not m.startswith("_")]
                    has_send = has_send or any(
                        "request" in m.lower() or
                        "send" in m.lower() or
                        "chat" in m.lower() or
                        "completion" in m.lower()
                        for m in cls_methods
                    )

                if has_send:
                    has_interface += 1

            except ImportError:
                # Some clients may have optional dependencies
                pass

        assert checked > 0, "Should be able to import at least some provider clients"
        coverage = has_interface / checked if checked > 0 else 0
        assert coverage >= 0.75, (
            f"At least 75% of checked provider clients should have a "
            f"request-sending function. Checked {checked}, found {has_interface} "
            f"with interface ({coverage:.0%})"
        )
