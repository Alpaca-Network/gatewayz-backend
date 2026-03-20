"""
Tests for share route import fixes.

Validates that the correct imports are used after fixing the ImportError.
"""

import pytest


def test_share_imports_successfully():
    """Test that share module imports without errors."""
    try:
        from src.routes import share

        assert share.router is not None
        assert hasattr(share, "create_share_link")
        assert hasattr(share, "get_my_share_links")
        assert hasattr(share, "get_shared_chat")
        assert hasattr(share, "delete_share_link")
    except ImportError as e:
        pytest.fail(f"Failed to import share module: {e}")


def test_share_uses_correct_security_imports():
    """Test that share uses get_optional_api_key (not get_api_key_optional)."""
    import inspect

    from src.routes import share

    # Get the source code of the module
    source = inspect.getsource(share)

    # Verify correct import is used
    assert "get_optional_api_key" in source, "Should import get_optional_api_key"
    assert (
        "get_api_key_optional" not in source
    ), "Should not import get_api_key_optional (doesn't exist)"


def test_security_deps_exports():
    """Test that security.deps exports the correct functions."""
    from src.security import deps

    # Verify get_optional_api_key exists
    assert hasattr(deps, "get_optional_api_key"), "Should export get_optional_api_key"

    # Verify get_optional_user exists
    assert hasattr(deps, "get_optional_user"), "Should export get_optional_user"

    # Verify get_api_key_optional does NOT exist
    assert not hasattr(deps, "get_api_key_optional"), "Should NOT export get_api_key_optional"


@pytest.mark.asyncio
async def test_share_endpoints_defined():
    """Test that share endpoints are properly defined."""
    from src.routes import share

    # Check that router has the expected routes
    routes = [(route.path, list(route.methods)) for route in share.router.routes]

    # Convert to dict for easier checking
    route_dict = {path: methods for path, methods in routes}

    # Check POST /v1/chat/share (create share link)
    assert "/v1/chat/share" in route_dict, "Should have create share link endpoint"
    assert "POST" in route_dict["/v1/chat/share"], "Create endpoint should support POST"

    # Check GET /v1/chat/share (list share links)
    assert "GET" in route_dict["/v1/chat/share"], "List endpoint should support GET"

    # Check GET /v1/chat/share/{token} (get shared chat)
    assert "/v1/chat/share/{token}" in route_dict, "Should have get shared chat endpoint"
    assert "GET" in route_dict["/v1/chat/share/{token}"], "Get shared endpoint should support GET"

    # Check DELETE /v1/chat/share/{token} (delete share link)
    assert "DELETE" in route_dict["/v1/chat/share/{token}"], "Delete endpoint should support DELETE"


def test_share_router_prefix():
    """Test that share router has the correct prefix."""
    from src.routes import share

    # Router should have /v1/chat/share prefix
    assert share.router.prefix == "/v1/chat/share", "Router should have correct prefix"


def test_share_router_tags():
    """Test that share router has the correct tags."""
    from src.routes import share

    # Router should be tagged with 'chat-share'
    assert "chat-share" in share.router.tags, "Router should be tagged with 'chat-share'"
