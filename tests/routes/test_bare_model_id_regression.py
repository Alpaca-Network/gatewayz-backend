"""Regression cover for the bare-model-id outage (2026-09-08).

Bug: `claude-sonnet-4-6` — the id every Anthropic SDK sends, and the one
FlashyOS's `@flashyos/agent` scaffold generates — was rejected on both
/v1/chat/completions and /v1/messages with **503 service_unavailable /
pricing_not_configured / "Please contact support"**, while
`anthropic/claude-sonnet-4-6` served normally. Bare OpenAI ids already
cleared admission, so the behavior was inconsistent by vendor and the worse
status code went to the more reasonable caller.

Two invariants are guarded here.

1. Behavioral: the admission gate resolves a bare id against a real catalog
   index and admits it under its canonical id.
2. Structural: `chat_completions` must ASSIGN the gate's return value back to
   `req.model`. The gate returning the canonical id is useless if the caller
   throws it away — routing and billing would silently keep using the
   unresolved string. That is a one-character refactor away at all times, so
   it is asserted against the AST rather than trusted.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.security import inference_gates
from src.services import model_resolution
import src.services.pricing  # noqa: F401 -- put the PACKAGE in sys.modules

# HARNESS TRAP: `src/services/pricing/` is a package that CONTAINS a
# `pricing.py`, so the usual string form —
#     monkeypatch.setattr("src.services.pricing.model_has_pricing", fake)
# — resolves to the SUBMODULE and leaves the PACKAGE attribute untouched. The
# package attribute is the one `from src.services.pricing import
# model_has_pricing` actually reads, so that patch silently does nothing and
# the test then passes (or fails) against real pricing data. Patch the package
# object out of sys.modules instead.
_PRICING = sys.modules["src.services.pricing"]

CHAT_PY = Path(__file__).resolve().parents[2] / "src" / "routes" / "chat.py"

# A realistic slice of the live catalog (verified against GET /v1/models).
CATALOG = [
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-sonnet-5",
    "anthropic/claude-opus-5",
    "anthropic/claude-haiku-4-5-20251001",
    "anthropic/claude-sonnet-4-5-20250929",
    "openai/gpt-4o-mini",
    "openai/gpt-5",
]


@pytest.fixture(autouse=True)
def _real_resolver_over_a_stub_catalog(monkeypatch):
    """Exercise the REAL resolver — only its data sources are stubbed."""
    from src.config import Config

    monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", True, raising=False)
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: list(CATALOG))
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    monkeypatch.setattr(_PRICING, "model_has_pricing", lambda m: m in CATALOG)
    monkeypatch.setattr(
        "src.services.cache.model_capabilities_cache.is_free_model", lambda _m: False
    )
    model_resolution.invalidate_resolution_index()
    yield
    model_resolution.invalidate_resolution_index()


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("claude-sonnet-4-6", "anthropic/claude-sonnet-4-6"),
        ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
        ("claude-opus-5", "anthropic/claude-opus-5"),
        ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4-5-20251001"),
        ("gpt-4o-mini", "openai/gpt-4o-mini"),
        # Fully-qualified ids keep working, unchanged.
        ("anthropic/claude-sonnet-4-6", "anthropic/claude-sonnet-4-6"),
    ],
)
async def test_vendor_native_ids_are_admitted(sent, expected):
    assert await inference_gates.enforce_model_pricing_gate(sent) == expected


async def test_the_exact_id_the_flashyos_scaffold_generates():
    # `flashyos init --inference gatewayz` writes this literal default.
    admitted = await inference_gates.enforce_model_pricing_gate("claude-sonnet-4-6")
    assert admitted == "anthropic/claude-sonnet-4-6"


async def test_a_genuinely_unknown_model_is_400_and_does_not_blame_us():
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("nope-9000")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_not_found"
    assert "contact support" not in exc.value.detail["error"]["message"].lower()


async def test_a_bare_id_with_no_unambiguous_target_stays_a_400():
    # The catalog carries claude-sonnet-4-5 only as ...-20250929, so the bare
    # form has no single target and must NOT be guessed at.
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("claude-sonnet-4-5")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_not_found"


def _find_function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in chat.py")


def test_chat_completions_assigns_the_gates_return_to_req_model():
    tree = ast.parse(CHAT_PY.read_text())
    fn = _find_function(tree, "chat_completions")

    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if isinstance(call, ast.Await):
            call = call.value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "enforce_model_pricing_gate"
        ):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "model"
                and isinstance(target.value, ast.Name)
                and target.value.id == "req"
            ):
                return
    raise AssertionError(
        "chat_completions must assign enforce_model_pricing_gate's return value to "
        "req.model -- discarding it silently reverts routing and billing to the "
        "caller's unresolved model id."
    )
