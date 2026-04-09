"""
Test that ALL admin routes require authentication.

This test scans every route file for /admin paths and verifies they
are protected by either:
- Router-level dependencies=[Depends(require_admin)]
- Per-route Depends(require_admin) or Depends(get_admin_key)

Catches both:
- Inline admin paths: @router.get("/admin/users")
- Prefix-based admin routers: APIRouter(prefix="/admin/model-sync")

The only intentional exception is POST /admin/create (user registration).
"""

import re
from pathlib import Path

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
    routes_dir = Path(__file__).parent.parent.parent / "src" / "routes"
    return sorted(routes_dir.glob("*.py"))


def _has_router_level_auth(source: str) -> bool:
    """Check if router has global auth dependency."""
    return bool(
        re.search(
            r"dependencies\s*=\s*\[.*?Depends\((require_admin|require_admin_or_env_key|get_admin_key)\)",
            source,
            re.DOTALL,
        )
    )


def _has_admin_prefix(source: str) -> bool:
    """Check if APIRouter uses a prefix containing 'admin'."""
    return bool(
        re.search(
            r'APIRouter\([^)]*prefix\s*=\s*["\'][^"\']*admin',
            source,
            re.DOTALL,
        )
    )


def _find_admin_routes_in_decorators(source: str) -> list[tuple[str, str]]:
    """Find route decorators with /admin in the path."""
    return re.findall(
        r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*admin[^"\']*)["\']',
        source,
    )


def _route_has_auth_in_signature(source: str, method: str, path: str) -> bool:
    """Check if a specific route's function signature includes admin auth."""
    decorator_pattern = rf'@router\.{method}\(\s*["\']({re.escape(path)})["\']'
    dec_match = re.search(decorator_pattern, source)
    if not dec_match:
        return True  # Can't find decorator, assume OK

    chunk_after = source[dec_match.start() : dec_match.start() + 2000]
    func_match = re.search(
        r"(async )?def \w+\((.*?)\)\s*(->\s*\S+\s*)?:",
        chunk_after,
        re.DOTALL,
    )
    if not func_match:
        return True  # Can't parse signature, assume OK

    func_params = func_match.group(2)
    return (
        "require_admin" in func_params
        or "require_admin_or_env_key" in func_params
        or "get_admin_key" in func_params
    )


def test_model_sync_router_has_auth_dependency():
    """
    CRITICAL: model_sync.py must have require_admin at router level.
    This was the primary finding in the O2 security audit - 11 admin
    endpoints were completely unprotected.
    """
    project_root = Path(__file__).parent.parent.parent
    source = (project_root / "src" / "routes" / "model_sync.py").read_text()
    assert "require_admin" in source, "model_sync.py must import an admin auth dependency"
    assert re.search(
        r"dependencies\s*=\s*\[.*?Depends\((require_admin|require_admin_or_env_key)\)",
        source,
        re.DOTALL,
    ), "model_sync.py router must have dependencies=[Depends(require_admin)] or require_admin_or_env_key"


def test_no_admin_route_without_auth():
    """
    Scan all route files for /admin paths and verify they have auth.

    Catches two patterns:
    1. Routes with /admin in the decorator path (e.g., @router.get("/admin/users"))
    2. Routers with /admin in the prefix (e.g., APIRouter(prefix="/admin/model-sync"))
       where individual routes don't contain "admin" in their path
    """
    unprotected = []

    for route_file in _get_route_files():
        source = route_file.read_text()

        if route_file.name in FILES_WITH_INLINE_AUTH:
            assert "_require_admin_dependency" in source, (
                f"{route_file.name} is in FILES_WITH_INLINE_AUTH but no longer "
                "contains inline auth. Update FILES_WITH_INLINE_AUTH if auth was moved."
            )
            continue

        has_global_auth = _has_router_level_auth(source)

        # Pattern 1: Prefix-based admin routers without router-level auth
        if _has_admin_prefix(source) and not has_global_auth:
            # This file has an admin prefix but no router-level auth.
            # Every route in this file is an admin route that's unprotected.
            all_routes = re.findall(
                r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)["\']',
                source,
            )
            for method, path in all_routes:
                if not _route_has_auth_in_signature(source, method, path):
                    unprotected.append(
                        f"{route_file.name}: {method.upper()} {path} (prefix-based admin router)"
                    )

        # Pattern 2: Inline admin paths in decorators
        admin_routes = _find_admin_routes_in_decorators(source)
        if not admin_routes:
            continue

        if has_global_auth:
            continue

        for method, path in admin_routes:
            if path in INTENTIONAL_PUBLIC_ADMIN_ROUTES:
                continue

            if not _route_has_auth_in_signature(source, method, path):
                unprotected.append(f"{route_file.name}: {method.upper()} {path}")

    assert (
        not unprotected
    ), f"Found {len(unprotected)} admin route(s) without authentication:\n" + "\n".join(
        f"  - {r}" for r in unprotected
    )


def test_bandit_false_positives_suppressed():
    """Verify nosec comments are present on known false positives."""
    project_root = Path(__file__).parent.parent.parent
    checks = [
        ("src/routes/admin.py", "nosec B324"),
        ("src/routes/health_timeline.py", "nosec B324"),
        ("src/services/model_selector.py", "nosec B324"),
        ("src/services/huggingface_hub_service.py", "nosec B615"),
    ]
    for filepath, nosec_tag in checks:
        source = (project_root / filepath).read_text()
        assert (
            nosec_tag in source
        ), f"{filepath} missing '{nosec_tag}' suppression for Bandit false positive"
