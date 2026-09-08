"""WAYZ earnings accrual for verified community-GPU work
(gatewayz-backend#2266; m4/spec.md §5; PR #2288 review fix round 1).

**Log sliding-scale payout tiers (m4/spec.md §5 follow-up):** on top of
the model-class rate below, a provider's payout is scaled by a basis-points
multiplier keyed off their own trailing-7-day VERIFIED token volume
(`provider_payout_tiers`, seeded testnet placeholders: 0.05x at 0 tokens/7d
up to 1.5x at 100M tokens/7d -- see the migration
`20260909000000_provider_payout_tiers.sql` and
`docs/gpu/VERIFICATION_AND_PAYOUTS.md`). Product intent: a big, sustained
provider earns far more per token than a one-off "bot" node. Volume is
looked up per PROVIDER (payout wallet), never per node
(`src/db/gpu_payouts.py`'s `get_provider_verified_volume_7d`) -- splitting
traffic across many small node registrations under one provider does not
lower anyone's tier, closing the obvious sybil incentive a per-node volume
count would create.

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
from src.db.gpu_payouts import (
    create_earning,
    get_payout_rate_wei_per_1k,
    get_payout_tiers,
    get_provider_verified_volume_7d,
)
from src.services.gpu.model_classes import known_model_class

_BPS_DENOMINATOR = 10000  # 100.00% == 10000 bps -- see provider_payout_tiers.multiplier_bps
_FULL_MULTIPLIER_BPS = 10000  # 1.0x -- used when no tiers are configured yet

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


def tier_multiplier_bps(volume_7d: int, tiers: list[dict]) -> int:
    """Basis-points payout multiplier for a trailing-7d verified-token
    volume, given the tier rows from get_payout_tiers(). Picks the
    largest tier whose min_tokens_7d is <= volume_7d -- a log sliding
    scale, so bigger sustained volume always means a bigger multiplier.
    Pure function over whatever list it's handed -- does not assume the
    caller's list is sorted.

    An empty tier list means "tiers not configured yet" and pays the full
    1.0x (10000 bps) rate rather than zeroing out every payout. If tiers
    ARE configured but somehow none apply (e.g. the 0-floor row was
    deleted) this returns 0 -- a misconfiguration should never silently
    overpay -- and logs at ERROR (PR #2295 review round 1, Important #2)
    so a deleted floor row is an operator-visible incident, not a silent
    $0 payout indistinguishable from "this genuinely is the bottom tier."
    See also src/db/gpu_payouts.py's check_payout_tiers_seeded(), a
    startup-time check for the same misconfiguration."""
    if not tiers:
        return _FULL_MULTIPLIER_BPS
    applicable = [t for t in tiers if int(t.get("min_tokens_7d", 0)) <= volume_7d]
    if not applicable:
        logger.error(
            "payout_tiers_misconfigured: no tier covers volume_7d=%s; paying 0 bps "
            "(tiers=%r) -- provider_payout_tiers is likely missing its min_tokens_7d=0 "
            "floor row",
            volume_7d,
            tiers,
        )
        return 0
    best = max(applicable, key=lambda t: int(t.get("min_tokens_7d", 0)))
    return int(best.get("multiplier_bps", 0))


def next_tier_min_tokens_7d(volume_7d: int, tiers: list[dict]) -> int | None:
    """The min_tokens_7d of the next tier above volume_7d's current tier,
    or None when volume_7d is already at (or above) the top tier, or when
    no tiers are configured. Used by GET /gpu/providers/me/earnings so an
    operator can see how much more trailing-7d volume gets them to a
    better multiplier."""
    thresholds = sorted(int(t.get("min_tokens_7d", 0)) for t in tiers)
    for threshold in thresholds:
        if threshold > volume_7d:
            return threshold
    return None


def compute_amount_wei(
    prompt_tokens: int,
    completion_tokens: int,
    rate_wei_per_1k: int,
    multiplier_bps: int = _FULL_MULTIPLIER_BPS,
) -> int:
    """Integer wei math -- (prompt_tokens + completion_tokens) * rate / 1000,
    floor division, then scaled by the sliding-scale tier multiplier (also
    floor division). Never floats: rate_wei_per_1k is wei-scaled
    (numeric(78,0) in provider_payout_rates), and a float division at this
    magnitude silently loses precision. multiplier_bps defaults to 1.0x
    (10000) so callers that don't pass one get the pre-tier behavior
    unchanged."""
    total_tokens = prompt_tokens + completion_tokens
    base_amount_wei = (total_tokens * rate_wei_per_1k) // 1000
    return (base_amount_wei * multiplier_bps) // _BPS_DENOMINATOR


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

    prompt_tokens = work.get("prompt_tokens", 0) or 0
    completion_tokens = work.get("completion_tokens", 0) or 0
    work_tokens = prompt_tokens + completion_tokens

    # Trailing-7d verified volume for the sliding-scale tier lookup, scoped
    # to provider_id (never node_id -- see this module's docstring on the
    # sybil note). get_provider_verified_volume_7d excludes this row (it
    # runs BEFORE provider_work.verification is flipped to 'verified' for
    # this row on the common path -- see spot_check.py's
    # _apply_sampled_outcome -- and is already included in the DB on the
    # reconciliation-retry path), so this row's own tokens are added back
    # in here exactly once either way.
    volume_7d = (
        get_provider_verified_volume_7d(work["provider_id"], exclude_work_id=work["id"])
        + work_tokens
    )
    multiplier_bps = tier_multiplier_bps(volume_7d, get_payout_tiers())

    amount_wei = compute_amount_wei(
        prompt_tokens, completion_tokens, rate_wei_per_1k, multiplier_bps
    )
    earning, outcome = create_earning(
        work["provider_id"],
        work["id"],
        amount_wei,
        multiplier_bps=multiplier_bps,
        volume_7d_at_accrual=volume_7d,
    )
    return EarningResult(earning=earning, outcome=outcome)
