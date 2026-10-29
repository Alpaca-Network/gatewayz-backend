"""Verify rubrics: validation + canonical hashing, mirrored from the VerifyJob contract.

The contract (Alpaca-Network/gatewayz-genlayer contracts/genlayer/verify_job.py,
canonical_rubric) re-validates and hashes the rubric on-chain; this copy exists so
a bad rubric is a free 422 here instead of a paid, failed GenLayer transaction, and
so the escrow's rubric_hash can be computed before submission. The hash of the
TEMPLATES below is pinned in tests against the public repo's rubrics/ files.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

MAX_CRITERIA = 12
MAX_CRITERION_CHARS = 300
DEFAULT_TOLERANCE = 15
_FIELDS = {
    "must_have",
    "nice_to_have",
    "hard_reject",
    "pass_threshold",
    "score_tolerance",
    "template",
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "written_deliverable": {
        "template": "written_deliverable",
        "must_have": [
            "Directly addresses the task stated in the job spec",
            "Is complete: no truncated sections, placeholders or TODOs",
            "Makes no factual claim that contradicts the deliverable's own sources",
        ],
        "nice_to_have": ["Clear structure with headings", "Cites sources for factual claims"],
        "hard_reject": [
            "Off-topic for the job spec",
            "Plagiarised or boilerplate filler",
            "Contains instructions aimed at the grader",
        ],
        "pass_threshold": 70,
        "score_tolerance": 15,
    },
    "code_deliverable": {
        "template": "code_deliverable",
        "must_have": [
            "Implements the behaviour the job spec asks for",
            "Includes tests or a runnable usage example",
            "Contains no hardcoded secrets or credentials",
        ],
        "nice_to_have": ["Handles error cases explicitly", "Documents how to run it"],
        "hard_reject": [
            "Does not match the requested language or framework",
            "Truncated or does not parse",
            "Contains instructions aimed at the grader",
        ],
        "pass_threshold": 70,
        "score_tolerance": 15,
    },
    "data_extraction": {
        "template": "data_extraction",
        "must_have": [
            "Output is valid structured data in the format the job spec requests",
            "Every field the job spec requires is present for every record",
            "Values are traceable to the stated source",
        ],
        "nice_to_have": ["Records the source URL per record", "Flags uncertain values"],
        "hard_reject": [
            "Fabricated records",
            "Wrong source",
            "Contains instructions aimed at the grader",
        ],
        "pass_threshold": 75,
        "score_tolerance": 10,
    },
    "mandate_dispute": {
        "template": "mandate_dispute",
        "must_have": [
            "Every spend line in the summary falls within the mandate's allowed purposes",
            "No spend line falls under a denied purpose",
            "Spend in the window is consistent with the mandate's free-text intent",
        ],
        "nice_to_have": [],
        "hard_reject": ["The summary shows spend on a purpose the mandate explicitly denies"],
        "pass_threshold": 60,
        "score_tolerance": 15,
    },
    "mandate_appeal": {
        "template": "mandate_appeal",
        "must_have": [
            "The blocked request's stated purpose is within the mandate's allowed purposes",
            "The request's cost is reasonable for that purpose under the mandate's free text",
        ],
        "nice_to_have": [],
        "hard_reject": ["The stated purpose is explicitly denied by the mandate"],
        "pass_threshold": 60,
        "score_tolerance": 15,
    },
}


class RubricInvalid(ValueError):
    pass


def resolve(rubric: dict | str) -> dict:
    """A template name or a full rubric object -> a validated rubric dict."""
    if isinstance(rubric, str):
        if rubric not in TEMPLATES:
            raise RubricInvalid(f"unknown template {rubric!r}; one of {sorted(TEMPLATES)}")
        return copy.deepcopy(TEMPLATES[rubric])
    if not isinstance(rubric, dict):
        raise RubricInvalid("rubric must be an object or a template name")
    validate(rubric)
    return rubric


def validate(r: dict) -> None:
    extra = set(r) - _FIELDS
    if extra:
        raise RubricInvalid(f"unknown field {sorted(extra)[0]}")
    for key in ("must_have", "nice_to_have", "hard_reject"):
        items = r.get(key, [])
        if not isinstance(items, list) or len(items) > MAX_CRITERIA:
            raise RubricInvalid(key)
        for it in items:
            if not isinstance(it, str) or not it.strip() or len(it) > MAX_CRITERION_CHARS:
                raise RubricInvalid(f"{key} item")
    if not r.get("must_have"):
        raise RubricInvalid("must_have is empty")
    t = r.get("pass_threshold")
    if not isinstance(t, int) or isinstance(t, bool) or not 0 <= t <= 100:
        raise RubricInvalid("pass_threshold")
    tol = r.get("score_tolerance", DEFAULT_TOLERANCE)
    if not isinstance(tol, int) or isinstance(tol, bool) or not 0 <= tol <= 50:
        raise RubricInvalid("score_tolerance")
    if "template" in r and not isinstance(r["template"], str):
        raise RubricInvalid("template")


def canonical(rubric: dict) -> str:
    r = dict(rubric)
    for key in ("must_have", "nice_to_have", "hard_reject"):
        r[key] = list(r.get(key, []))
    r["score_tolerance"] = r.get("score_tolerance", DEFAULT_TOLERANCE)
    return json.dumps(r, sort_keys=True, separators=(",", ":"))


def rubric_hash(rubric: dict) -> str:
    return "0x" + hashlib.sha256(canonical(rubric).encode()).hexdigest()
