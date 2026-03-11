"""
CM-17 Deployment

Tests verifying that create_app() returns a FastAPI instance,
the Vercel entry point imports successfully, and all major route
groups are registered.
"""

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# CM-17.1  create_app() returns a FastAPI instance
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1701CreateAppReturnsFastapiInstance:
    def test_create_app_returns_fastapi_instance(self):
        """create_app() must return a FastAPI application instance."""
        from fastapi import FastAPI

        # Import the already-created app (create_app is called at module level)
        from src.main import app

        assert isinstance(app, FastAPI), (
            f"Expected FastAPI instance, got {type(app).__name__}"
        )


# ---------------------------------------------------------------------------
# CM-17.2  Vercel entry point imports app successfully
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1702VercelEntryPointImportsApp:
    def test_vercel_entry_point_imports_app(self):
        """api/index.py must export an 'app' object that is a FastAPI instance."""
        from fastapi import FastAPI

        # api/index.py imports from src.main
        # We test that the module-level import chain works
        import importlib
        module = importlib.import_module("api.index")
        assert hasattr(module, "app"), "api/index.py must export 'app'"
        assert isinstance(module.app, FastAPI), (
            f"api/index.py 'app' must be FastAPI, got {type(module.app).__name__}"
        )


# ---------------------------------------------------------------------------
# CM-17.3  App includes all major route groups
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM1703AppIncludesAllRouteGroups:
    def test_app_includes_all_route_groups(self):
        """The app must have routes for chat, auth, users, admin, health,
        payments, and catalog."""
        from src.main import app

        # Collect all registered route paths
        route_paths = set()
        for route in app.routes:
            if hasattr(route, "path"):
                route_paths.add(route.path)
            # Also check sub-routers
            if hasattr(route, "routes"):
                for sub_route in route.routes:
                    if hasattr(sub_route, "path"):
                        route_paths.add(sub_route.path)

        all_paths_str = " ".join(route_paths)

        # Check for key route groups (using substring matching on paths)
        expected_patterns = [
            "chat",       # /v1/chat/completions
            "auth",       # /auth or /api/auth
            "user",       # /users or /api/users
            "admin",      # /api/admin
            "health",     # /health or /api/health
            "payment",    # /api/stripe or payments
            "model",      # /models or /v1/models
        ]

        for pattern in expected_patterns:
            assert pattern in all_paths_str.lower(), (
                f"Expected route group containing '{pattern}' in app routes. "
                f"Available paths include a subset: {list(route_paths)[:20]}..."
            )
