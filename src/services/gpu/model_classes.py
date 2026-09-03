"""Exact-match allow-list of open-weight model ids -> payout class
(gatewayz-backend#2265, #2266; PR #2288 review fix round 1, C1).

**Why an allow-list and not a regex on the model id (the original design):**
`gpu_nodes.models` and `provider_work.model` are provider-declared,
free-text strings (spec §2) -- a dishonest node could register/report a
model id like `community/definitely-a-70b-model`, have that string parse
as a 70B "large"-class model (5x the `small` rate), and actually run a
tiny model underneath while returning plausible-length filler. The old
`model_class_for()` in `src/services/gpu/earnings.py` regexed a parameter
count straight out of that string, so it paid whatever class the node
claimed. This module replaces that with an exact-match lookup against a
curated list of real open-weight model ids -- a model id that isn't on
this list is simply not payable at all (see `earnings.py`'s
`model_class_for`/`effective_model_class`), not defaulted to a guessed
class.

**Node registration should reject/warn on unknown model ids too** (spec
§3, W-A1's `POST /gpu/nodes`) -- `is_known_model_id()` below is the
intended call site for that check once `src/routes/gpu.py` exists in this
branch tree; not wired here since that route isn't owned by this
workstream (W-B) and doesn't exist in this worktree yet. Flagged in
`docs/gpu/VERIFICATION_AND_PAYOUTS.md` as a W-A1 follow-up.

Seeded with a representative set of well-known open-weight instruct
models per spec §2's size classes (small <=13B, medium <=34B, large
>34B). Not exhaustive -- extend `_BUILTIN_MODEL_CLASSES` as real
community catalog entries are approved, or set
`COMMUNITY_MODEL_CLASS_OVERRIDES` (a JSON object string, e.g.
`{"some-new-model-id": "medium"}`) to add/override entries without a code
deploy.
"""

from __future__ import annotations

import json
import logging

from src.config.config import Config

logger = logging.getLogger(__name__)

_VALID_CLASSES = {"small", "medium", "large"}

# Keys are bare model ids (no "community/" prefix), lower-cased.
_BUILTIN_MODEL_CLASSES: dict[str, str] = {
    # small (<=13B)
    "llama-3.1-8b-instruct": "small",
    "llama-3-8b-instruct": "small",
    "mistral-7b-instruct": "small",
    "mistral-7b-instruct-v0.3": "small",
    "qwen2.5-7b-instruct": "small",
    "gemma-2-9b-it": "small",
    # medium (<=34B)
    "qwen2.5-32b-instruct": "medium",
    "yi-34b-chat": "medium",
    "gemma-2-27b-it": "medium",
    "mixtral-8x7b-instruct-v0.1": "medium",
    # large (>34B)
    "llama-3.1-70b-instruct": "large",
    "llama-3-70b-instruct": "large",
    "qwen2.5-72b-instruct": "large",
    "mixtral-8x22b-instruct-v0.1": "large",
}


def _load_overrides() -> dict[str, str]:
    """Parse COMMUNITY_MODEL_CLASS_OVERRIDES (JSON object string, bare
    lower-cased model id -> class). Malformed JSON or an invalid class
    value logs a warning and is ignored entirely (fail closed to the
    built-in list rather than partially apply a broken override)."""
    raw = Config.COMMUNITY_MODEL_CLASS_OVERRIDES
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("must be a JSON object")
        overrides = {}
        for key, value in parsed.items():
            if value not in _VALID_CLASSES:
                raise ValueError(f"invalid class {value!r} for {key!r}")
            overrides[str(key).strip().lower()] = value
        return overrides
    except Exception as e:
        logger.warning(
            "COMMUNITY_MODEL_CLASS_OVERRIDES is set but failed to parse, ignoring it entirely: %s", e
        )
        return {}


def _model_class_map() -> dict[str, str]:
    """Built-in allow-list with COMMUNITY_MODEL_CLASS_OVERRIDES layered on
    top. Recomputed per call (cheap, small dict) rather than cached at
    import time, so an override change via env var takes effect on the
    next lookup without a restart-order dependency."""
    merged = dict(_BUILTIN_MODEL_CLASSES)
    merged.update(_load_overrides())
    return merged


def known_model_class(model_id: str) -> str | None:
    """The allow-listed class for model_id (after stripping a
    'community/'-style prefix and lower-casing), or None if it isn't on
    the list -- 'unknown' means 'not payable', never a guessed default."""
    bare = model_id.split("/")[-1].strip().lower()
    return _model_class_map().get(bare)


def is_known_model_id(model_id: str) -> bool:
    return known_model_class(model_id) is not None
