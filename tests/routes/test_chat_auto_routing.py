"""Tests for auto-routing wiring into the chat request flow (gatewayz-backend#2215).

`resolve_auto_routed_model` is tested directly (no live app/DB), matching the
`dispatch_non_streaming`-direct-call convention used elsewhere in tests/routes/
for pieces of `chat.py` that don't have a dedicated test module yet. A separate
AST-structural test guards the ordering invariant this whole phase depends on:
resolution must run before `enforce_model_pricing_gate`, which 400s on
unresolved aliases.
"""

import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.routes.chat_routing import _is_router_model, resolve_auto_routed_model
from src.schemas.proxy import Message
from src.services.model_selector import ModelSelection
from src.services.task_classifier import TaskClassification

CHAT_PY = Path(__file__).resolve().parents[2] / "src" / "routes" / "chat.py"


def _req(
    model="auto",
    content="write a fibonacci function",
    tools=None,
    response_format=None,
    auto_routing=None,
):
    return SimpleNamespace(
        model=model,
        messages=[Message(role="user", content=content)],
        tools=tools,
        response_format=response_format,
        auto_routing=auto_routing,
    )


@contextmanager
def _enabled():
    """Enable the global kill-switch, a not-disabled per-key policy, and the
    default (mode="auto", industry="general") routing preferences — the
    baseline every test exercising the actual engine needs (default is OFF)."""
    with (
        patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", True),
        patch("src.db.routing_policies.is_auto_routing_disabled_for_key", return_value=False),
        patch(
            "src.services.routing_preferences.get_routing_preferences_for_key",
            return_value=("auto", "general"),
        ),
    ):
        yield


def _passthrough_to_thread():
    """asyncio.to_thread that runs the target function inline (no real thread),
    while still letting tests assert it was actually used for each blocking call."""

    async def _run(func, *args, **kwargs):
        return func(*args, **kwargs)

    return AsyncMock(side_effect=_run)


# --------------------------------------------------------------------------- #
# Structural ordering invariant
# --------------------------------------------------------------------------- #
def test_resolve_auto_routed_model_runs_before_pricing_gate():
    tree = ast.parse(CHAT_PY.read_text())
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "chat_completions"
    )

    def _call_lineno(func_name):
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == func_name
            ):
                return node.lineno
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == func_name
            ):
                return node.lineno
        raise AssertionError(f"no call to {func_name!r} found in chat_completions")

    resolve_line = _call_lineno("resolve_auto_routed_model")
    pricing_gate_line = _call_lineno("enforce_model_pricing_gate")

    assert resolve_line < pricing_gate_line, (
        "resolve_auto_routed_model must run BEFORE enforce_model_pricing_gate — "
        "that gate 400s on unresolved aliases like 'auto' since they have no "
        "catalog/pricing row."
    )


# --------------------------------------------------------------------------- #
# Rollout-safety gates (gatewayz-backend#2216) — each must independently be
# able to no-op the engine, regardless of the other three saying yes.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_noop_when_global_flag_is_off_by_default():
    """AUTO_ROUTING_ENABLED defaults to False — the engine must stay fully
    inert with no patching at all, exactly like before Phase 4 existed."""
    req = _req(model="auto")

    with patch("src.services.task_classifier.classify_task") as mock_classify:
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "auto"
    mock_classify.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_request_explicitly_opts_out():
    req = _req(model="auto", auto_routing=False)

    with (
        _enabled(),
        patch("src.services.task_classifier.classify_task") as mock_classify,
    ):
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "auto"
    mock_classify.assert_not_called()


@pytest.mark.asyncio
async def test_request_auto_routing_true_does_not_override_global_flag_off():
    """auto_routing can only narrow access, never widen it past the global flag."""
    req = _req(model="auto", auto_routing=True)

    with patch("src.services.task_classifier.classify_task") as mock_classify:
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "auto"
    mock_classify.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_per_key_policy_disables_it():
    req = _req(model="auto")

    with (
        patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", True),
        patch(
            "src.db.routing_policies.is_auto_routing_disabled_for_key", return_value=True
        ) as mock_policy,
        patch("src.services.task_classifier.classify_task") as mock_classify,
    ):
        await resolve_auto_routed_model(req, is_anonymous=False, api_key="gw_live_abc123")

    assert req.model == "auto"
    mock_classify.assert_not_called()
    mock_policy.assert_called_once_with("gw_live_abc123")


# --------------------------------------------------------------------------- #
# resolve_auto_routed_model behavior
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_noop_for_anonymous_requests():
    req = _req(model="auto")

    with _enabled(), patch("src.services.task_classifier.classify_task") as mock_classify:
        await resolve_auto_routed_model(req, is_anonymous=True)

    assert req.model == "auto"
    mock_classify.assert_not_called()


@pytest.mark.asyncio
async def test_noop_for_explicit_model():
    req = _req(model="openai/gpt-4o-mini")

    with patch("src.services.task_classifier.classify_task") as mock_classify:
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "openai/gpt-4o-mini"
    mock_classify.assert_not_called()


def test_gatewayz_router_is_a_recognized_alias():
    """`gatewayz-router` is the branded name for this feature -- same
    resolution path as `auto`, just a friendlier model id."""
    assert _is_router_model("gatewayz-router") is True
    assert _is_router_model("GATEWAYZ-ROUTER") is True  # case-insensitive, matches _is_router_model


@pytest.mark.asyncio
async def test_gatewayz_router_alias_resolves_like_auto():
    req = _req(model="gatewayz-router")
    classification = TaskClassification(
        task_type="code_generation", capability_names=frozenset(), confidence=0.9
    )
    selection = ModelSelection(model_id="openai/gpt-4o-mini", reason="top_scorer")

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=[{"id": "m"}]),
        patch("src.services.task_classifier.classify_task", return_value=classification),
        patch("src.services.model_selector.select_model", return_value=selection),
    ):
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_successful_resolution_mutates_req_model_in_place():
    req = _req(model="auto")
    classification = TaskClassification(
        task_type="code_generation", capability_names=frozenset({"tools"}), confidence=0.9
    )
    selection = ModelSelection(model_id="openai/gpt-4o-mini", reason="top_scorer", score=80.0)

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()) as mock_to_thread,
        patch(
            "src.db.models_catalog_db.get_candidate_models",
            return_value=[{"id": "openai/gpt-4o-mini"}],
        ),
        patch("src.services.task_classifier.classify_task", return_value=classification),
        patch("src.services.model_selector.select_model", return_value=selection),
    ):
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "openai/gpt-4o-mini"
    # is_auto_routing_disabled_for_key, get_routing_preferences_for_key,
    # classify_task, get_candidate_models, select_model — all five blocking
    # calls must go through asyncio.to_thread, never called directly.
    assert mock_to_thread.call_count == 5


@pytest.mark.asyncio
async def test_successful_resolution_against_real_select_model_with_raw_catalog_row():
    """End-to-end regression: `get_candidate_models` returns raw `models` table
    rows (integer `id` PK, string `provider_model_id`, no `canonical_id`) --
    exactly gatewayz-backend's live production shape. `select_model` itself is
    NOT mocked here (only its own DB fetches are), so this exercises the real
    `_quality_score`/`_display_model_id` path end-to-end. Before the fix, this
    raised `AttributeError` on the first candidate (`.lower()` on an int) and
    every real auto-routing request crashed instead of routing.
    """
    req = _req(model="auto")
    classification = TaskClassification(
        task_type="code_generation", capability_names=frozenset(), confidence=0.9
    )
    raw_row = {
        "id": 2298,
        "provider_model_id": "openai/gpt-4o-mini",
        "canonical_id": None,
        "supports_function_calling": True,
    }

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=[raw_row]),
        patch("src.services.task_classifier.classify_task", return_value=classification),
        patch("src.services.model_selector._fetch_pricing", return_value={}),
        patch("src.services.model_selector._fetch_quality_priors", return_value={}),
    ):
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_resolution_prefers_stronger_model_via_inference_fallback_end_to_end():
    """End-to-end proof of the "route to the best model for the task" fix:
    with zero manual `model_quality_scores` data (the live default -- that
    table only covers 15 legacy models) and identical pricing so cost cannot
    explain the outcome, a reasoning-heavy request must resolve to the larger
    reasoning-flagged candidate, not tie-break arbitrarily.
    """
    req = _req(model="auto")
    classification = TaskClassification(
        task_type="complex_reasoning", capability_names=frozenset(), confidence=0.9
    )
    candidates = [
        {"id": 1, "provider_model_id": "vendor/flagship-400b-reasoner", "is_reasoning": True},
        {"id": 2, "provider_model_id": "vendor/tiny-3b-chat", "is_reasoning": False},
    ]
    identical_pricing = {1: {"in": 0.001, "out": 0.001}, 2: {"in": 0.001, "out": 0.001}}

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=candidates),
        patch("src.services.task_classifier.classify_task", return_value=classification),
        patch("src.services.model_selector._fetch_pricing", return_value=identical_pricing),
        patch("src.services.model_selector._fetch_quality_priors", return_value={}),
    ):
        await resolve_auto_routed_model(req, is_anonymous=False)

    assert req.model == "vendor/flagship-400b-reasoner"


@pytest.mark.asyncio
async def test_classification_error_raises_400_and_leaves_model_unchanged():
    req = _req(model="auto")
    failed_classification = TaskClassification(error="timeout")

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.services.task_classifier.classify_task", return_value=failed_classification),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await resolve_auto_routed_model(req, is_anonymous=False)

    assert exc_info.value.status_code == 400
    assert req.model == "auto"


@pytest.mark.asyncio
async def test_no_candidate_selected_raises_400():
    req = _req(model="router:general")
    classification = TaskClassification(task_type="unknown", capability_names=frozenset())
    empty_selection = ModelSelection(model_id=None, reason="no_candidates")

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=[]),
        patch("src.services.task_classifier.classify_task", return_value=classification),
        patch("src.services.model_selector.select_model", return_value=empty_selection),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await resolve_auto_routed_model(req, is_anonymous=False)

    assert exc_info.value.status_code == 400
    assert req.model == "router:general"


@pytest.mark.asyncio
async def test_conversation_id_threaded_through_to_select_model():
    req = _req(model="auto")
    classification = TaskClassification(task_type="code_generation", capability_names=frozenset())
    selection = ModelSelection(model_id="openai/gpt-4o-mini", reason="top_scorer")

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=[{"id": "m"}]),
        patch("src.services.task_classifier.classify_task", return_value=classification),
        patch("src.services.model_selector.select_model", return_value=selection) as mock_select,
    ):
        await resolve_auto_routed_model(req, is_anonymous=False, conversation_id="42")

    assert mock_select.call_args.kwargs["conversation_id"] == "42"


@pytest.mark.asyncio
async def test_routing_preferences_industry_threaded_into_classify_task():
    """The key's `routing_industry` preference must reach `classify_task`'s
    `industry` kwarg (which folds it into the classifier's own prompt --
    see task_classifier._system_prompt_for)."""
    req = _req(model="auto")
    classification = TaskClassification(task_type="code_generation", capability_names=frozenset())
    selection = ModelSelection(model_id="openai/gpt-4o-mini", reason="top_scorer")

    with (
        patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", True),
        patch("src.db.routing_policies.is_auto_routing_disabled_for_key", return_value=False),
        patch(
            "src.services.routing_preferences.get_routing_preferences_for_key",
            return_value=("price", "legal"),
        ),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=[{"id": "m"}]),
        patch(
            "src.services.task_classifier.classify_task", return_value=classification
        ) as mock_classify,
        patch(
            "src.services.model_selector.select_model", return_value=selection
        ) as mock_select,
    ):
        await resolve_auto_routed_model(req, is_anonymous=False, api_key="gw_live_abc123")

    assert mock_classify.call_args.kwargs["industry"] == "legal"
    # explicit mode="price" passes straight through -- not adaptive-resolved.
    assert mock_select.call_args.kwargs["mode"] == "price"


@pytest.mark.asyncio
async def test_routing_preferences_auto_mode_resolves_via_adaptive_mapping():
    """mode="auto" ("let Gatewayz decide") must resolve to a concrete mode
    from the classified task_type via `resolve_adaptive_mode`, not pass
    "auto" straight through to `select_model`."""
    req = _req(model="auto")
    classification = TaskClassification(task_type="code_generation", capability_names=frozenset())
    selection = ModelSelection(model_id="openai/gpt-4o-mini", reason="top_scorer")

    with (
        patch("src.routes.chat_routing.Config.AUTO_ROUTING_ENABLED", True),
        patch("src.db.routing_policies.is_auto_routing_disabled_for_key", return_value=False),
        patch(
            "src.services.routing_preferences.get_routing_preferences_for_key",
            return_value=("auto", "general"),
        ),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=[{"id": "m"}]),
        patch("src.services.task_classifier.classify_task", return_value=classification),
        patch(
            "src.services.model_selector.select_model", return_value=selection
        ) as mock_select,
    ):
        await resolve_auto_routed_model(req, is_anonymous=False)

    # code_generation -> "quality" per resolve_adaptive_mode.
    assert mock_select.call_args.kwargs["mode"] == "quality"


@pytest.mark.asyncio
async def test_messages_are_converted_to_dicts_before_classification():
    req = _req(model="auto", content="hello there")
    classification = TaskClassification(task_type="conversation", capability_names=frozenset())
    selection = ModelSelection(model_id="openai/gpt-4o-mini", reason="top_scorer")

    with (
        _enabled(),
        patch("asyncio.to_thread", new=_passthrough_to_thread()),
        patch("src.db.models_catalog_db.get_candidate_models", return_value=[{"id": "m"}]),
        patch(
            "src.services.task_classifier.classify_task", return_value=classification
        ) as mock_classify,
        patch("src.services.model_selector.select_model", return_value=selection),
    ):
        await resolve_auto_routed_model(req, is_anonymous=False)

    sent_messages = mock_classify.call_args.args[0]
    assert sent_messages == [Message(role="user", content="hello there").model_dump()]
    assert isinstance(sent_messages[0], dict)
