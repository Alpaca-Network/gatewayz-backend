"""Task classifier eval harness (gatewayz-backend#2216).

The old (deleted) D2 auto-router never had a way to catch classifier
regressions before merge -- the deletion commit (e94e095c) left no eval
trail behind it to learn from. This is that safety net for the new
classifier (`src/services/task_classifier.py`), built as two tiers:

1. DETERMINISTIC (always runs in CI): each fixture's expected label is fed
   back as a mocked LLM response, and the test asserts `classify_task`'s
   parsing/validation pipeline reconstructs it correctly. This is a
   regression net for the harness's own plumbing (TASK_TYPES validation,
   reasoning-flag threading, capability merging) across a wide variety of
   shapes -- it does NOT tell you whether the real gpt-4o-mini still
   classifies well, only that the code around it still works.

2. LIVE (opt-in, skipped by default): the same fixtures against the REAL
   classifier, no mocking. This is the actual drift check, and it is
   deliberately NOT wired into the default CI run: this repo has a written
   policy of mocking all external LLM calls in tests
   (tests/documentation/TESTING_BEST_PRACTICES.md), and no CI job anywhere
   requires a real API key today (every secret has a fake fallback in
   ci.yml). Making this tier mandatory would be a first-of-its-kind
   exception to that policy -- run it manually via:
       RUN_LIVE_CLASSIFIER_EVAL=1 pytest tests/eval/test_classifier_eval_harness.py -k live
   with a real OPENAI_API_KEY configured, e.g. before/after changing the
   classifier's prompt or model.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.services.quality_inference import TASK_TYPES
from src.services.task_classifier import classify_task

# One fixture per TASK_TYPES category the classifier can realistically be
# asked to distinguish (mirrors the deleted D2 cluster's taxonomy checklist --
# code/math/translation/summarization/data/reasoning/creative/short-QA --
# mapped onto quality_inference.TASK_TYPES, not resurrecting its regex engine).
FIXTURES = [
    {
        "id": "code_generation",
        "prompt": "Write a Python function that returns the nth Fibonacci number.",
        "expected_task_type": "code_generation",
        "expected_needs_reasoning": False,
    },
    {
        "id": "code_review",
        "prompt": "Review this function for bugs: def add(a, b): return a - b",
        "expected_task_type": "code_review",
        "expected_needs_reasoning": False,
    },
    {
        "id": "translation",
        "prompt": "Translate 'Good morning, how are you?' into French.",
        "expected_task_type": "translation",
        "expected_needs_reasoning": False,
    },
    {
        "id": "summarization",
        "prompt": "Summarize the plot of Romeo and Juliet in three sentences.",
        "expected_task_type": "summarization",
        "expected_needs_reasoning": False,
    },
    {
        "id": "simple_qa",
        "prompt": "What is the capital of France?",
        "expected_task_type": "simple_qa",
        "expected_needs_reasoning": False,
    },
    {
        "id": "math_calculation",
        "prompt": (
            "A train leaves station A at 60mph and another leaves station B, "
            "300 miles away, at 40mph heading toward it. How long until they meet?"
        ),
        "expected_task_type": "math_calculation",
        "expected_needs_reasoning": True,
    },
    {
        "id": "data_analysis",
        "prompt": "Given monthly sales figures for 2025, which month had the highest growth rate?",
        "expected_task_type": "data_analysis",
        "expected_needs_reasoning": True,
    },
    {
        "id": "creative_writing",
        "prompt": "Write a short story about a lighthouse keeper who finds a message in a bottle.",
        "expected_task_type": "creative_writing",
        "expected_needs_reasoning": False,
    },
    {
        "id": "conversation",
        "prompt": "hey, what's up?",
        "expected_task_type": "conversation",
        "expected_needs_reasoning": False,
    },
    {
        "id": "complex_reasoning",
        "prompt": (
            "All bloops are razzles. All razzles are lazzles. Are all bloops lazzles? "
            "Explain your reasoning step by step."
        ),
        "expected_task_type": "complex_reasoning",
        "expected_needs_reasoning": True,
    },
]


def test_fixture_task_types_are_all_valid():
    """Guard the fixture set itself: every expected_task_type must be a real
    TASK_TYPES member, or the fixtures would silently test nothing meaningful."""
    valid = set(TASK_TYPES)
    for fixture in FIXTURES:
        assert fixture["expected_task_type"] in valid, (
            f"fixture {fixture['id']!r} expects an invalid task_type "
            f"{fixture['expected_task_type']!r} -- not in quality_inference.TASK_TYPES"
        )


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["id"] for f in FIXTURES])
def test_classifier_pipeline_reconstructs_expected_label(fixture):
    """Deterministic tier: mocks the LLM to return exactly the expected label,
    then asserts classify_task's parsing/validation round-trips it correctly."""
    mocked_content = json.dumps(
        {
            "task_type": fixture["expected_task_type"],
            "needs_reasoning": fixture["expected_needs_reasoning"],
            "confidence": 0.9,
        }
    )
    mocked_response = MagicMock(choices=[MagicMock(message=MagicMock(content=mocked_content))])

    with patch("src.services.task_classifier.make_openai_request", return_value=mocked_response):
        result = classify_task(messages=[{"role": "user", "content": fixture["prompt"]}])

    assert result.task_type == fixture["expected_task_type"]
    assert ("reasoning" in result.capability_names) == fixture["expected_needs_reasoning"]
    assert result.error is None


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_CLASSIFIER_EVAL"),
    reason=(
        "opt-in live drift check -- set RUN_LIVE_CLASSIFIER_EVAL=1 and a real "
        "OPENAI_API_KEY to run this against the actual classifier model. Not "
        "part of the default CI run (see module docstring)."
    ),
)
def test_live_classifier_matches_expected_labels_above_threshold():
    """Live tier: calls the REAL classify_task, no mocking. Manual-run only."""
    mismatches = []
    for fixture in FIXTURES:
        result = classify_task(messages=[{"role": "user", "content": fixture["prompt"]}])
        if result.task_type != fixture["expected_task_type"]:
            mismatches.append(
                f"{fixture['id']}: expected {fixture['expected_task_type']!r}, "
                f"got {result.task_type!r} (error={result.error})"
            )

    accuracy = (len(FIXTURES) - len(mismatches)) / len(FIXTURES)
    assert (
        accuracy >= 0.8
    ), f"live classifier accuracy {accuracy:.0%} below 80% threshold. Mismatches:\n" + "\n".join(
        mismatches
    )
