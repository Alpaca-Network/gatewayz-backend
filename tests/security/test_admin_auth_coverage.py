"""
Test that ALL admin routes require authentication.

This test scans every route file for /admin paths and verifies they
are protected by either:
- Router-level dependencies=[Depends(require_admin)]
- Per-route Depends(require_admin) or Depends(get_admin_key)

The only intentional exception is POST /admin/create (user registration).
"""

import ast
import re
from pathlib import Path

import pytest


# Routes intentionally public (documented exceptions)
INTENTIONAL_PUBLIC_ADMIN_ROUTES = {
    "/create",  # POST /admin/create - user registration in admin.py
}

# Files that use inline admin auth (calling require_admin inside function body
# rather than via Depends). These are verified by manual inspection.
FILES_WITH_INLINE_AUTH = {
    "roles.py",  # Uses _require_admin_dependency() called inside each function body
}


def _get_route_files():
    """Get all route files in src/routes/."""
    routes_dir = Path("src/routes")
    return sorted(routes_dir.glob("*.py"))


def test_model_sync_router_has_auth_dependency():
    """
    CRITICAL: model_sync.py must have require_admin at router level.
    This was the primary finding in the O2 security audit — 11 admin
    endpoints were completely unprotected.
    """
    source = Path("src/routes/model_sync.py").read_text()
    assert "require_admin" in source, (
        "model_sync.py must import require_admin"
    )
    assert "dependencies=[Depends(require_admin)]" in source, (
        "model_sync.py router must have dependencies=[Depends(require_admin)]"
    )


def test_no_admin_route_without_auth():
    """
    Scan all route files for /admin paths and verify they have auth.
    Files can protect routes either at the router level (dependencies=[...])
    or per-route (Depends(require_admin) in function signature).
    """
    unprotected = []

    for route_file in _get_route_files():
        source = route_file.read_text()

        # Check if router has global auth dependency
        has_router_level_auth = (
            "dependencies=[Depends(require_admin)]" in source
            or "dependencies=[Depends(get_admin_key)]" in source
        )

        # Find all route decorators with /admin in the path
        admin_routes = re.findall(
            r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*admin[^"\']*)["\']',
            source,
        )

        if not admin_routes:
            continue

        if has_router_level_auth:
            # All routes in this file are protected at router level
            continue

        if route_file.name in FILES_WITH_INLINE_AUTH:
            # These files call require_admin inside function body (not via Depends)
            # Verified by manual security audit
            continue

        # Check each admin route individually
        for method, path in admin_routes:
            if path in INTENTIONAL_PUBLIC_ADMIN_ROUTES:
                continue

            # Find the decorator line, then capture everything up to the
            # closing parenthesis of the function signature (handles multi-line)
            decorator_pattern = rf'@router\.{method}\(\s*["\']({re.escape(path)})["\']'
            dec_match = re.search(decorator_pattern, source)
            if not dec_match:
                continue

            # Get the chunk from decorator to the next function body (colon + newline)
            chunk_after = source[dec_match.start():dec_match.start() + 2000]
            # Find function signature (everything between def ... and the closing ):)
            func_match = re.search(r'(async )?def \w+\((.*?)\)\s*(->\s*\S+\s*)?:', chunk_after, re.DOTALL)
            if func_match:
                func_params = func_match.group(2)
                has_auth = (
                    "require_admin" in func_params
                    or "get_admin_key" in func_params
                )
                if not has_auth:
                    unprotected.append(
                        f"{route_file.name}: {method.upper()} {path}"
                    )

    assert not unprotected, (
        f"Found {len(unprotected)} admin route(s) without authentication:\n"
        + "\n".join(f"  - {r}" for r in unprotected)
    )


def test_bandit_false_positives_suppressed():
    """Verify nosec comments are present on known false positives."""
    checks = [
        ("src/routes/admin.py", "nosec B324"),
        ("src/routes/health_timeline.py", "nosec B324"),
        ("src/services/model_selector.py", "nosec B324"),
        ("src/services/huggingface_hub_service.py", "nosec B615"),
    ]
    for filepath, nosec_tag in checks:
        source = Path(filepath).read_text()
        assert nosec_tag in source, (
            f"{filepath} missing '{nosec_tag}' suppression for Bandit false positive"
        )
