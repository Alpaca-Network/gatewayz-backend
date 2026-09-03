"""WAYZ earnings accrual for verified community-GPU work
(gatewayz-backend#2266; m4/spec.md §5).

model_class_for() maps a model id to the payout-rate class seeded into
provider_payout_rates (spec §2: 'small' <=13B, 'medium' <=34B, 'large'
>34B) by parsing the parameter count out of the model id itself (e.g.
"community/llama-3.1-8b-instruct" -> 8B -> small). This is a heuristic,
not a lookup against a real model catalog -- documented in
docs/gpu/VERIFICATION_AND_PAYOUTS.md as a known limitation; a model id
with no parseable size defaults to 'medium' (logged) rather than silently
under- or over-paying by picking an extreme.
"""

from __future__ import annotations

import logging
import re

from src.db.gpu_payouts import create_earning, get_payout_rate_wei_per_1k

logger = logging.getLogger(__name__)

_PARAM_COUNT_RE = re.compile(r"(\d+(?:\.\d+)?)b(?:[-_]|$)", re.IGNORECASE)

_SMALL_MAX_PARAMS_B = 13
_MEDIUM_MAX_PARAMS_B = 34


def model_class_for(model_id: str) -> str:
    """'small' | 'medium' | 'large' for a (possibly community/-prefixed) model id."""
    bare = model_id.split("/")[-1]
    match = _PARAM_COUNT_RE.search(bare)
    if not match:
        logger.warning(
            "model_class_for: couldn't parse a parameter count from %r; defaulting to 'medium'",
            model_id,
        )
        return "medium"

    params_b = float(match.group(1))
    if params_b <= _SMALL_MAX_PARAMS_B:
        return "small"
    if params_b <= _MEDIUM_MAX_PARAMS_B:
        return "medium"
    return "large"


def compute_amount_wei(prompt_tokens: int, completion_tokens: int, rate_wei_per_1k: int) -> int:
    """Integer wei math -- (prompt_tokens + completion_tokens) * rate / 1000,
    floor division. Never floats: rate_wei_per_1k is wei-scaled (numeric(78,0)
    in provider_payout_rates), and a float division at this magnitude
    silently loses precision."""
    total_tokens = prompt_tokens + completion_tokens
    return (total_tokens * rate_wei_per_1k) // 1000


def record_earning_for_verified_work(work: dict) -> dict | None:
    """Accrue a provider_earnings row for a provider_work row that just
    passed verification. Returns the inserted row, or None if the rate is
    unseeded, the model class is unparseable in a way that still resolves
    to a class (never happens -- model_class_for always returns one) --
    practically, None means either the rate lookup failed/is unseeded, or
    create_earning found a duplicate work_id (already recorded; not an
    error, see src/db/gpu_payouts.py).
    """
    model_class = model_class_for(work["model"])
    rate_wei_per_1k = get_payout_rate_wei_per_1k(model_class)
    if rate_wei_per_1k is None:
        logger.warning(
            "record_earning_for_verified_work: no payout rate seeded for class %r "
            "(work_id=%s, model=%r); skipping accrual",
            model_class,
            work.get("id"),
            work.get("model"),
        )
        return None

    amount_wei = compute_amount_wei(
        work.get("prompt_tokens", 0) or 0, work.get("completion_tokens", 0) or 0, rate_wei_per_1k
    )
    return create_earning(work["provider_id"], work["id"], amount_wei)
