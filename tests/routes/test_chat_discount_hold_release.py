"""/v1/chat/completions frees a discount credits hold on every exit that
never reaches billing (src/routes/chat.py::chat_completions).

The route places the hold at its credit pre-check, keyed by the billing ref
(request_id). Later checks can still refuse the request (credit pre-check,
daily limit, pricing, rate limit) and the dispatch can fail; all of those
leave through the endpoint's two exception handlers, which must release the
hold. Like tests/routes/test_chat_client_ip_regression.py this is checked on
the source (the endpoint needs live collaborators to run end to end); the
release itself is unit-tested in tests/handlers/.
"""

from __future__ import annotations

import ast
import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

CHAT_PY = Path(__file__).resolve().parents[2] / "src" / "routes" / "chat.py"


def _endpoint() -> ast.AsyncFunctionDef:
    tree = ast.parse(CHAT_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_completions":
            return node
    raise AssertionError("chat_completions not found")


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def test_the_hold_owner_is_bound_before_the_try():
    fn = _endpoint()
    outer_try = next(n for n in fn.body if isinstance(n, ast.Try))
    before = fn.body[: fn.body.index(outer_try)]
    assert any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "discount_hold_user_id" for t in n.targets)
        for n in before
    ), "discount_hold_user_id must be bound before the try so both handlers can read it"


def test_the_hold_owner_is_recorded_before_the_hold_is_placed():
    source = ast.get_source_segment(CHAT_PY.read_text(), _endpoint())
    assert source.index("discount_hold_user_id = user.get(") < source.index(
        "await discount_aware_credits_async("
    )


def test_both_exception_handlers_release_the_hold():
    fn = _endpoint()
    outer_try = next(n for n in fn.body if isinstance(n, ast.Try))
    handlers = outer_try.handlers
    assert len(handlers) == 2  # HTTPException, Exception
    for handler in handlers:
        releases = _calls(handler, "release_for_request_async")
        assert releases, ast.dump(handler.type)
        args = [ast.unparse(a) for a in releases[0].args]
        assert args == ["discount_hold_user_id", "request_id"]


def test_credit_handler_releases_the_hold_when_the_cost_is_zero():
    """handle_credits_and_usage with nothing to charge: deduct_credits
    returns at once, so the hold is released here."""
    from src.services.billing import credit_handler

    ref = "6f9b1f0e-3c2a-4d5e-9b8a-1c2d3e4f5a6b"
    with (
        patch("src.services.pricing.calculate_cost_async", AsyncMock(return_value=0.0)),
        patch("src.db.users.deduct_credits"),
        patch("src.db.users.record_usage"),
        patch("src.db.rate_limits.update_rate_limit_usage"),
        patch.object(credit_handler, "_record_credit_metrics"),
        patch(
            "src.services.discount_credits.billing.release_for_request_async",
            AsyncMock(return_value=Decimal("0.99")),
        ) as release,
    ):
        asyncio.run(
            credit_handler.handle_credits_and_usage(
                api_key="k",
                user={"id": 7, "tier": "pro"},
                model="m",
                trial={"is_trial": False},
                total_tokens=0,
                prompt_tokens=0,
                completion_tokens=0,
                elapsed_ms=1,
                request_id=ref,
            )
        )
    release.assert_awaited_once_with(7, ref)
