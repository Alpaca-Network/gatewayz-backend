"""Tests for chat.py's use of RequestIdentity (gatewayz-backend#2254).

`chat_completions` used to derive `is_anonymous = api_key is None` locally.
It now takes `identity: RequestIdentity = Depends(get_request_identity)` and
reads `identity.is_anonymous` instead -- one source of truth shared with any
other route that adopts it. These tests exercise the real dependency chain
through the app (no full provider dispatch -- that's exactly what makes a
direct TestClient hit of this endpoint impractical elsewhere in this test
suite too; see tests/routes/test_chat_dispatch_non_streaming_failover.py's
docstring for the same convention).
"""

import ast
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import src.security.deps as deps_module
from src.main import app
from src.security.deps import get_optional_api_key
from src.security.identity import (
    ANONYMOUS,
    RequestIdentity,
    get_request_identity,
    get_request_identity_strict,
)

CHAT_PY = Path(__file__).resolve().parents[2] / "src" / "routes" / "chat.py"

client = TestClient(app)


# --- Structural guard: is_anonymous must come from `identity`, not a local
# re-derivation off `api_key`. Mirrors the AST-based convention already used
# for chat.py invariants (see test_chat_client_ip_regression.py). -----------


def _find_function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in chat.py")


def test_chat_completions_depends_on_get_request_identity():
    tree = ast.parse(CHAT_PY.read_text())
    fn = _find_function(tree, "chat_completions")

    arg_names = [a.arg for a in fn.args.args]
    assert "identity" in arg_names, (
        "chat_completions must take `identity: RequestIdentity = "
        "Depends(get_request_identity)` -- see src/security/identity.py"
    )


def test_is_anonymous_derives_from_identity_not_api_key():
    tree = ast.parse(CHAT_PY.read_text())
    fn = _find_function(tree, "chat_completions")

    assigns_is_anonymous = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "is_anonymous" for t in n.targets)
    ]
    assert assigns_is_anonymous, "expected an `is_anonymous = ...` assignment in chat_completions"

    # Must read identity.is_anonymous, not re-derive `api_key is None`.
    (assignment,) = assigns_is_anonymous
    value = assignment.value
    is_identity_attr = (
        isinstance(value, ast.Attribute)
        and value.attr == "is_anonymous"
        and isinstance(value.value, ast.Name)
        and value.value.id == "identity"
    )
    assert is_identity_attr, (
        "is_anonymous must be derived from `identity.is_anonymous` " f"(found: {ast.dump(value)})"
    )


# --- Functional: the real dependency chain, through the app ----------------


def test_anonymous_request_hits_the_anonymous_gate():
    """No Authorization header -> identity resolves ANONYMOUS -> the
    anonymous gate (enforce_anonymous_gate) fires exactly like it did when
    `is_anonymous` was computed locally from `api_key is None`.
    """
    with patch("src.security.inference_gates.Config.ANONYMOUS_ENABLED", False):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "missing_api_key"


# Caches the chat request path loads lazily, with the module-level globals each one
# writes. Their loaders flip `_cache_loaded`, so these are restored afterwards: leaving
# an empty-but-"loaded" cache behind would silently change the behaviour of every later
# test sharing the worker process.
_LAZY_CACHES = {
    "src.services.cache.model_mappings_cache": (
        "_aliases",
        "_provider_mappings",
        "_routing_rules",
        "_provider_native_values",
        "_cache_loaded",
        "_cache_loaded_at",
    ),
    "src.services.cache.model_capabilities_cache": (
        "_max_tokens",
        "_has_json_mode",
        "_is_reasoning",
        "_free_models",
        "_latency_tier",
        "_quality_priors",
        "_cache_loaded",
        "_cache_loaded_at",
    ),
}


@contextmanager
def _no_database():
    """Make the request path's database calls fail instantly instead of over the wire.

    This module's docstring says downstream lookup is "stubbed to fail fast and
    deterministically instead of hitting a real database". That was true of the user
    lookup and false of everything else: `chat_completions` lazily loads a model-mappings
    cache on the way to `model_has_pricing`, and a model-capabilities cache on the way to
    `is_free_model`, and both go through PostgREST.

    With no database reachable, httpx does not fail fast -- it walks its connection-retry
    ladder, sleeping between attempts (`httpcore._sync.connection._connect` ->
    `_network_backend.sleep`). That cost **31.85s of pure time.sleep** against this file's
    30s timeout. It was deterministic rather than flaky, and it was not DNS: pointing
    SUPABASE_URL at a connection-refused port cost the same. Whether this test passed came
    down to which machine ran it, and it failed 5/5 here on an unmodified tree.

    Raising the timeout would have left a real network call inside a unit test and moved
    the cliff rather than removing it. Failing the client at the boundary takes the test
    to **~4.6s**, and unlike stubbing individual tables it also covers whatever the
    request path starts loading next. Same stub `tests/routes/test_admin_status.py` uses,
    for the same reason.

    **Do not delete this as redundant.** Nothing else stubs these calls, and without it
    the only symptom is a slow test that passes on some machines and not others.
    """
    import importlib

    saved = {}
    for module_path, names in _LAZY_CACHES.items():
        module = importlib.import_module(module_path)
        saved[module] = {name: getattr(module, name) for name in names}

    try:
        with patch(
            "src.config.supabase_config.get_supabase_client",
            side_effect=RuntimeError("no database in this test"),
        ):
            yield
    finally:
        for module, values in saved.items():
            for name, value in values.items():
                setattr(module, name, value)


def test_overriding_identity_bypasses_the_anonymous_gate():
    """Proof that chat.py reads `identity`, not a local api_key re-check:
    override just the identity dependency to a non-anonymous one (leaving no
    Authorization header at all) and confirm the anonymous gate does NOT
    fire. Downstream user lookup is stubbed to fail fast and deterministically
    instead of hitting a real database.
    """
    fake_identity = RequestIdentity(
        kind="api_key",
        user_id=99,
        api_key="fake-key",
        auth_method="email",
        is_guest=False,
        wallet_addresses=(),
    )
    # chat.py depends on the STRICT identity chain (a supplied-but-invalid key
    # is rejected rather than demoted to anonymous), so the override has to
    # target that dependency — overriding the lenient one silently does nothing.
    app.dependency_overrides[get_request_identity_strict] = lambda: fake_identity

    try:
        with (
            patch("src.security.inference_gates.Config.ANONYMOUS_ENABLED", False),
            _no_database(),
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            )
    finally:
        del app.dependency_overrides[get_request_identity_strict]

    # Whatever happens next (missing user -> invalid_api_key, etc.), it must
    # NOT be the anonymous-gate rejection.
    body = resp.json()
    error_code = body.get("error", {}).get("code") if isinstance(body.get("error"), dict) else None
    assert error_code != "missing_api_key", body


def test_dependency_chain_validates_key_and_looks_up_user_exactly_once():
    """Regression for a fix-round-1 review finding: `chat_completions`
    declares BOTH `api_key: str | None = Depends(get_optional_api_key)` and
    `identity: RequestIdentity = Depends(get_request_identity)` -- exactly
    mirrored here. `get_request_identity` used to also depend on
    `get_optional_user`, which independently re-ran `get_api_key()` ->
    `validate_api_key_security()` (a second Supabase read + `last_used_at`
    write) instead of reusing the key `get_optional_api_key` already
    validated, and it duplicated the `get_user()` lookup chat.py did itself.
    This asserts FastAPI's per-request dependency cache resolves
    `get_optional_api_key` exactly once (shared between the two `Depends`)
    and that `get_user` is called exactly once.

    Uses a minimal router with chat_completions' exact dependency signature
    rather than posting to the real `/v1/chat/completions` -- that endpoint's
    business logic needs a live database/Redis beyond the auth layer, which
    this sandbox doesn't have, and hitting it for real only adds ~25s of
    unrelated connection-retry latency without changing what this test needs
    to prove about the dependency graph.
    """
    probe_app = FastAPI()

    @probe_app.post("/_test/identity_dedup_probe")
    async def _probe(
        api_key: str | None = Depends(get_optional_api_key),
        identity: RequestIdentity = Depends(get_request_identity),
    ):
        return {"api_key": api_key, "identity_api_key": identity.api_key}

    probe_client = TestClient(probe_app)

    user = {"id": 1, "auth_method": "email"}
    real_validate = deps_module.validate_api_key_security

    with (
        patch("src.security.deps.validate_api_key_security", wraps=real_validate) as mock_validate,
        patch("src.security.identity.get_user", return_value=user) as mock_get_user,
    ):
        resp = probe_client.post(
            "/_test/identity_dedup_probe", headers={"Authorization": "Bearer test-key"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key"] == body["identity_api_key"] == "test-key"
    assert mock_validate.call_count == 1
    assert mock_get_user.call_count == 1


def test_anonymous_constant_is_the_no_header_identity():
    """Sanity: get_request_identity resolves to the ANONYMOUS singleton (not
    just an equal-by-value instance) when there's no Authorization header,
    matching src/security/identity.py's contract.
    """

    captured = {}

    @app.get("/_test/chat_identity_probe")
    async def _probe(identity: RequestIdentity = Depends(get_request_identity)):
        captured["identity"] = identity
        return {"kind": identity.kind}

    try:
        resp = client.get("/_test/chat_identity_probe")
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/_test/chat_identity_probe"
        ]

    assert resp.status_code == 200
    assert captured["identity"] is ANONYMOUS
