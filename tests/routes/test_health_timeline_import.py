"""
Tests for health_timeline route import fixes.

Validates that the correct imports are used after fixing the ImportError.
"""

import pytest


def test_health_timeline_imports_successfully():
    """Test that health_timeline module imports without errors."""
    try:
        from src.routes import health_timeline

        assert health_timeline.router is not None
        assert hasattr(health_timeline, "get_providers_uptime")
        assert hasattr(health_timeline, "get_models_uptime")
    except ImportError as e:
        pytest.fail(f"Failed to import health_timeline module: {e}")


def test_health_timeline_uses_correct_supabase_import():
    """Test that health_timeline uses get_supabase_client (not get_supabase_admin)."""
    import inspect

    from src.routes import health_timeline

    # Get the source code of the module
    source = inspect.getsource(health_timeline)

    # Verify correct import is used
    assert "get_supabase_client" in source, "Should import get_supabase_client"
    assert (
        "get_supabase_admin" not in source
    ), "Should not import get_supabase_admin (doesn't exist)"

    # Verify the function is being called correctly
    assert "supabase = get_supabase_client()" in source, "Should call get_supabase_client()"


def test_supabase_config_exports():
    """Test that supabase_config exports the correct functions."""
    from src.config import supabase_config

    # Verify get_supabase_client exists
    assert hasattr(supabase_config, "get_supabase_client"), "Should export get_supabase_client"

    # Verify get_supabase_admin does NOT exist
    assert not hasattr(
        supabase_config, "get_supabase_admin"
    ), "Should NOT export get_supabase_admin"


@pytest.mark.asyncio
async def test_health_timeline_endpoints_defined():
    """Test that health timeline endpoints are properly defined."""
    from src.routes import health_timeline

    # Check that router has the expected routes
    routes = [route.path for route in health_timeline.router.routes]

    assert "/health/providers/uptime" in routes, "Should have providers uptime endpoint"
    assert "/health/models/uptime" in routes, "Should have models uptime endpoint"
