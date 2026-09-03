"""WAYZ earnings accrual for verified community-GPU work
(gatewayz-backend#2266; m4/spec.md §5; PR #2288 review fix round 1).

**C1 fix (payout inflation, Critical):** model class is now resolved via
an exact-match allow-list (`src/services/gpu/model_classes.py`), never by
parsing the free-text model id a node self-reports -- that was an
exploitable payout-inflation vector (a node could claim
`community/definitely-a-70b-model`, collect the `large` rate, and run
whatever it wanted underneath). An unknown model id is simply not
payable (see `model_class_for`).

**Testnet safety cap:** even for a known model, `medium`/`large` rates
only apply when the work item carries a valid attestation AND
`COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER` is configured (i.e. the
strongest verification path is actually active for this request) -- see
`effective_model_class`. Everything else pays the `small` rate
regardless of the model's real class. This bounds the blast radius of
C1's residual risk (a node can still lie about output quality within
`small`'s rate, but can no longer collect a 5x multiplier for it) until
model-capability verification is stronger than a spot-check heuristic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config.config import Config
from src.db.gpu_payouts import create_earning, get_payout_rate_wei_per_1k
from src.services.gpu.model_classes import known_model_class

logger = logging.getLogger(__name__)


def model_class_for(model_id: str) -> str | None:
    """The allow-listed class for model_id, or None if it isn't on the
    list. Thin wrapper over model_classes.known_model_class -- kept as a
    separate name here since callers of this module import earnings-domain
    functions, not model_classes directly."""
    return known_model_class(model_id)


def effective_model_class(
    model_id: str, attested: bool, reference_provider_configured: bool
) -> str | None:
    """The class actually used for payout, after the testnet safety cap.
    None means "not payable" (unknown model id) -- distinct from a known
    model capped down to 'small'."""
    declared_class = model_class_for(model_id)
    if declared_class is None:
        return None
    if declared_class == "small":
        return "small"
    if attested and reference_provider_configured:
        return declared_class
    return "small"


def compute_amount_wei(prompt_tokens: int, completion_tokens: int, rate_wei_per_1k: int) -> int:
    """Integer wei math -- (prompt_tokens + completion_tokens) * rate / 1000,
    floor division. Never floats: rate_wei_per_1k is wei-scaled (numeric(78,0)
    in provider_payout_rates), and a float division at this magnitude
    silently loses precision."""
    total_tokens = prompt_tokens + completion_tokens
    return (total_tokens * rate_wei_per_1k) // 1000


@dataclass
class EarningResult:
    """outcome:
    'created'       -- a new provider_earnings row was inserted (earning is set).
    'duplicate'      -- work_id already had an earning (UNIQUE violation) --
                        a genuine no-op, not an error (e.g. a re-run after a crash).
    'not_payable'    -- model_id isn't in the allow-list; the caller must
                        NOT leave provider_work.verification='verified'
                        for this row (see src/services/gpu/spot_check.py).
    'rate_unseeded'  -- the resolved class has no seeded provider_payout_rates row.
    'db_error'       -- the insert failed for a reason OTHER than a
                        duplicate (network blip, RLS, malformed payload...);
                        logged at WARNING (not INFO like 'duplicate') so
                        it's visible to operators, and the caller should
                        still leave verification='verified' -- the
                        reconciliation pass (run_spot_check_verification's
                        _reconcile_missing_earnings) retries this on a
                        later run rather than losing the payout silently.
    """

    earning: dict | None
    outcome: str


def record_earning_for_verified_work(work: dict) -> EarningResult:
    """Accrue a provider_earnings row for a provider_work row that just
    passed verification, applying the C1 allow-list + testnet safety cap."""
    reference_provider_configured = bool(Config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER)
    attested = bool(work.get("attested"))
    effective_class = effective_model_class(
        work.get("model", ""), attested, reference_provider_configured
    )
    if effective_class is None:
        logger.warning(
            "record_earning_for_verified_work: model %r is not on the payout allow-list "
            "(work_id=%s) -- not payable",
            work.get("model"),
            work.get("id"),
        )
        return EarningResult(earning=None, outcome="not_payable")

    rate_wei_per_1k = get_payout_rate_wei_per_1k(effective_class)
    if rate_wei_per_1k is None:
        logger.warning(
            "record_earning_for_verified_work: no payout rate seeded for class %r "
            "(work_id=%s, model=%r); skipping accrual",
            effective_class,
            work.get("id"),
            work.get("model"),
        )
        return EarningResult(earning=None, outcome="rate_unseeded")

    amount_wei = compute_amount_wei(
        work.get("prompt_tokens", 0) or 0, work.get("completion_tokens", 0) or 0, rate_wei_per_1k
    )
    earning, outcome = create_earning(work["provider_id"], work["id"], amount_wei)
    return EarningResult(earning=earning, outcome=outcome)
