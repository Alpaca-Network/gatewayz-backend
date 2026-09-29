"""
Every POST/PUT/PATCH/DELETE route must carry an auth dependency, or be on the
reviewed allowlist below (with a reason). Fails when a new unauthenticated
mutating route is added.
"""

from fastapi.routing import APIRoute

from src.main import app

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Dependency callables that enforce authentication (optional-auth deps do NOT count).
AUTH_DEPS = {
    "get_api_key",
    "get_current_user",
    "get_admin_key",
    "require_admin",
    "require_superadmin",
    "require_admin_or_env_key",
    "get_node",  # GPU node token auth
    "_get_current_user_dependency",  # payments.py wrapper around security.deps.get_current_user
}

# (METHOD, path) -> reason. Each entry was reviewed by hand.
ALLOWLIST = {
    # Anonymous / optional-auth inference (rate limited, anonymous tier by design)
    ("POST", "/v1/chat/completions"): "anonymous chat, optional key",
    ("POST", "/v1/messages"): "anonymous chat (anthropic format), optional key",
    ("POST", "/v1/completions"): "optional key",
    # Login / registration / credential flows (cannot require an existing session)
    ("POST", "/auth"): "Privy login",
    ("POST", "/auth/register"): "registration",
    ("POST", "/auth/password-reset"): "password reset request",
    ("POST", "/auth/reset-password"): "password reset with emailed token",
    ("POST", "/auth/wallet/nonce"): "wallet login nonce",
    ("POST", "/auth/wallet/verify"): "wallet login signature verification",
    # Auth is enforced inline (roles._require_admin_dependency) - verified by hand
    ("POST", "/admin/roles/update"): "inline require_admin",
    # Read-only compute that uses POST
    ("POST", "/v1/models/batch-compare"): "read-only comparison",
    ("POST", "/catalog/models/batch-compare"): "read-only comparison",
    # Sentry tunnel: forwards envelopes, validated by DSN allowlist
    ("POST", "/monitoring"): "sentry tunnel",
    # Inline signature verification
    ("POST", "/api/stripe/webhook"): "stripe signature check",
}


def _dep_names(dependant, out: set[str]) -> set[str]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            out.add(getattr(sub.call, "__name__", type(sub.call).__name__))
        _dep_names(sub, out)
    return out


def _mutating_routes():
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in sorted(r.methods & MUTATING):
                yield m, r.path, _dep_names(r.dependant, set())


def test_no_unreviewed_unauthenticated_mutating_routes():
    offenders = [
        f"{m} {p}"
        for m, p, deps in _mutating_routes()
        if not (deps & AUTH_DEPS) and (m, p) not in ALLOWLIST
    ]
    assert not offenders, "Mutating routes without auth dependency:\n" + "\n".join(offenders)


def test_allowlist_has_no_stale_entries_that_are_now_authenticated():
    authed = {(m, p) for m, p, deps in _mutating_routes() if deps & AUTH_DEPS}
    stale = sorted(k for k in ALLOWLIST if k in authed)
    assert not stale, f"Remove from allowlist (now authenticated): {stale}"
