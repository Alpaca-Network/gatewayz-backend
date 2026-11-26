"""Credit pre-check wiring in the chat route (src/routes/chat.py), the
auto-routing classifier gate (src/routes/chat_routing.py,
src/services/task_classifier.py) and the /v1/messages input estimate
(src/routes/messages.py).

Like tests/routes/test_chat_discount_hold_release.py, the endpoint's wiring is
checked on its source (it needs live collaborators to run end to end); the
pieces it calls are unit-tested here and in tests/billing/.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.routes.chat_routing import enforce_balance_before_auto_routing
from src.services.task_classifier import CLASSIFIER_MAX_INPUT_CHARS, classify_task

CHAT_PY = Path(__file__).resolve().parents[2] / "src" / "routes" / "chat.py"


def _endpoint() -> ast.AsyncFunctionDef:
    tree = ast.parse(CHAT_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_completions":
            return node
    raise AssertionError("chat_completions not found")


def _source() -> str:
    return ast.get_source_segment(CHAT_PY.read_text(), _endpoint())


def test_the_route_precheck_counts_tools_tool_choice_and_the_original_body():
    source = _source()
    block = source[source.index("_estimate_inputs = {") :]
    block = block[: block.index("}")]
    assert '"tools": req.tools' in block
    assert '"tool_choice": req.tool_choice' in block
    assert '"input_tokens_floor": input_tokens_floor(request)' in block
    checks = [
        n
        for n in ast.walk(_endpoint())
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "estimate_and_check_credits"
    ]
    assert len(checks) == 1
    spread = [ast.unparse(k.value) for k in checks[0].keywords if k.arg is None]
    assert spread == ["_estimate_inputs"]
    # Tool calls are kept on the messages the estimate sees.
    assert "_msgs = [m.model_dump(exclude_none=True) for m in req.messages]" in source


def test_the_route_refuses_an_unpaid_balance_before_holding():
    source = _source()
    assert source.index("credit_holds.has_unpaid_debt_async(") < source.index(
        "credit_holds.place_hold_async("
    )
    assert source.index("credit_hold_user_id = user.get(") < source.index(
        "credit_holds.place_hold_async("
    )


def test_both_exception_handlers_release_the_credit_hold():
    fn = _endpoint()
    outer_try = next(n for n in fn.body if isinstance(n, ast.Try))
    for handler in outer_try.handlers:
        calls = [
            n
            for n in ast.walk(handler)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "release_hold_async"
        ]
        assert calls, ast.dump(handler.type)
        assert [ast.unparse(a) for a in calls[0].args] == ["credit_hold_user_id", "request_id"]


def test_the_balance_gate_runs_before_the_classifier():
    source = _source()
    assert source.index("enforce_balance_before_auto_routing(") < source.index(
        "await resolve_auto_routed_model("
    )


@pytest.mark.parametrize("allowance,purchased", [(0, 0), (None, None), (0, -1)])
def test_an_auto_routed_request_from_an_empty_account_is_refused(allowance, purchased):
    user = {"id": 1, "subscription_allowance": allowance, "purchased_credits": purchased}
    with patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", True):
        with pytest.raises(HTTPException) as exc:
            enforce_balance_before_auto_routing("auto", False, user)
    assert exc.value.status_code == 402


def test_the_classifier_is_not_called_for_a_zero_balance_key():
    """The route runs the gate first (ordering test above); the gate refuses
    before any classifier call."""
    user = {"id": 1, "subscription_allowance": 0, "purchased_credits": 0}
    with (
        patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", True),
        patch("src.services.task_classifier.make_openai_request") as classifier_call,
    ):
        with pytest.raises(HTTPException):
            enforce_balance_before_auto_routing("auto", False, user)
    classifier_call.assert_not_called()


def test_the_balance_gate_is_a_no_op_unless_the_classifier_would_run():
    empty = {"id": 1, "subscription_allowance": 0, "purchased_credits": 0}
    with patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", False):
        enforce_balance_before_auto_routing("auto", False, empty)
    with patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", True):
        enforce_balance_before_auto_routing("openai/gpt-4o", False, empty)
        enforce_balance_before_auto_routing("auto", True, None)
        enforce_balance_before_auto_routing(
            "auto", False, {"subscription_allowance": 0, "purchased_credits": 0.5}
        )


def test_the_classifier_input_is_cut_to_the_maximum():
    with patch("src.services.task_classifier.make_openai_request") as request:
        request.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"task_type": "chat"}'))]
        )
        classify_task(messages=[{"role": "user", "content": "x" * 50_000}])
    sent = request.call_args.kwargs.get("messages") or request.call_args.args[0]
    user_text = next(m["content"] for m in sent if m["role"] == "user")
    assert len(user_text) == CLASSIFIER_MAX_INPUT_CHARS


def test_messages_route_estimates_the_original_body_for_the_precheck():
    from src.routes import messages as messages_route
    from src.services.billing.credit_precheck import INPUT_TOKENS_FLOOR_STATE

    source = Path(messages_route.__file__).read_text()
    create = source[source.index("async def create_message(") :]
    estimate = create.index("estimate_request_input_tokens(")
    assert create.index("setattr(") < estimate < create.index("proxy_request = ProxyRequest(")
    assert "INPUT_TOKENS_FLOOR_STATE" in create[:estimate]
    assert "req.messages, req.tools, req.tool_choice, system=req.system" in create
    assert INPUT_TOKENS_FLOOR_STATE == "gatewayz_input_tokens_floor"


def test_input_tokens_floor_reads_only_a_positive_int():
    from types import SimpleNamespace

    from src.services.billing.credit_precheck import input_tokens_floor

    assert input_tokens_floor(None) is None
    assert input_tokens_floor(SimpleNamespace(state=SimpleNamespace())) is None
    state = SimpleNamespace(gatewayz_input_tokens_floor=4200)
    assert input_tokens_floor(SimpleNamespace(state=state)) == 4200
    state = SimpleNamespace(gatewayz_input_tokens_floor=True)
    assert input_tokens_floor(SimpleNamespace(state=state)) is None
