"""Spot-check verification of community-GPU work (gatewayz-backend#2265;
m4/spec.md §5).

We never store prompt/response content (threat model G3 -- see
provider_work's schema comment in m4/spec.md §2), so verifying a sampled
request means replaying it. That requires the prompt, which we don't have
after the fact -- so sampling happens BEFORE the request is forwarded
(pre-sampling), and the prompt is stashed in Redis for a short TTL just
for the rows selected. See `maybe_stash` for the exact call-site contract
W-A2's community adapter needs to follow.

Comparison strategy (spec §5 names one check, "same-node determinism",
without fully specifying its comparison partner -- decided here, recorded
in docs/gpu/VERIFICATION_AND_PAYOUTS.md): replay happens on (a) the same
node always, and (b) COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER's provider
only if configured. The similarity check only runs when (b) exists --
without a configured reference there is nothing truthful to diff the
node's replay against, so an unreferenced verification falls back to the
token-count-plausibility and non-empty checks alone. This is intentionally
weaker than a referenced verification; it's why non-attested nodes (which
skew towards testnet's common "no reference configured" case) get double
the sampling rate (see `should_spot_check`).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.config import Config
from src.config.redis_config import get_redis_client
from src.db.gpu_payouts import (
    adjust_health_score,
    disable_node,
    get_node,
    list_agable_pending_work,
    list_sampled_pending_work,
    list_verified_work_since,
    node_verification_stats_since,
    set_verification,
    void_earning_for_work,
)
from src.services.gpu.earnings import record_earning_for_verified_work
from src.services.providers.community_adapter import adapter_for_node

logger = logging.getLogger(__name__)

_STASH_KEY_PREFIX = "gpu_spotcheck:"
_STASH_TTL_SECONDS = 1200  # 20 min (spec §5)
_SAMPLED_LOOKBACK_HOURS = 1  # "last hour" per spec §5
_UNSAMPLED_AGE_HOURS = 24
_UNSAMPLED_MAX_DAILY_FAILURE_RATE = 0.05  # spec §5

_REPLAY_MAX_TOKENS_CAP = 64
_SIMILARITY_THRESHOLD = 0.8
_TOKEN_COUNT_TOLERANCE = 0.25
_MAX_FAILS_BEFORE_DISABLE = 3
_HEALTH_PENALTY_ON_FAILURE = 20


# ---------------------------------------------------------------------------
# Pre-sampling + stash (called from the request path, before forwarding)
# ---------------------------------------------------------------------------


def should_spot_check(billing_ref: str, attested_expected: bool, rng: Any = None) -> bool:
    """True with probability COMMUNITY_SPOTCHECK_RATE, doubled (capped at
    1.0) when the node has no attestation history. `rng` defaults to the
    stdlib `random` module (any object exposing `.random() -> float` in
    [0, 1) works, e.g. `random.Random(seed)` in tests) -- billing_ref is
    accepted for logging/future deterministic-seeding use but isn't used
    to seed the draw today."""
    import random as _random_module

    rng = rng or _random_module
    rate = Config.COMMUNITY_SPOTCHECK_RATE
    if not attested_expected:
        rate *= 2
    rate = min(rate, 1.0)
    return rng.random() < rate


def _stash_key(billing_ref: str) -> str:
    return f"{_STASH_KEY_PREFIX}{billing_ref}"


def stash_prompt_for_spot_check(billing_ref: str, messages: list[dict], model: str) -> bool:
    """Redis SETEX gpu_spotcheck:{billing_ref} -> {"messages", "model"}, TTL
    _STASH_TTL_SECONDS. Returns False (logged) on any Redis failure --
    never raises, matching every other Redis-touching call site in this
    codebase (e.g. src/routes/faucet.py's nonce store)."""
    redis_client = get_redis_client()
    if redis_client is None:
        logger.warning(
            "stash_prompt_for_spot_check: Redis unavailable, %s will fall through to the "
            "24h aging path unresolved by replay",
            billing_ref,
        )
        return False
    try:
        payload = json.dumps({"messages": messages, "model": model})
        redis_client.setex(_stash_key(billing_ref), _STASH_TTL_SECONDS, payload)
        return True
    except Exception as e:
        logger.warning("stash_prompt_for_spot_check failed for %s: %s", billing_ref, e)
        return False


def get_stashed_prompt(billing_ref: str) -> dict | None:
    redis_client = get_redis_client()
    if redis_client is None:
        return None
    try:
        raw = redis_client.get(_stash_key(billing_ref))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    except Exception as e:
        logger.warning("get_stashed_prompt failed for %s: %s", billing_ref, e)
        return None


def maybe_stash(billing_ref: str, messages: list[dict], model: str, node: dict | None) -> bool:
    """Call-site contract for W-A2's community adapter: call this ONCE per
    community request, BEFORE forwarding to the node, with `node` the dict
    describing the target node (or None if unavailable -- treated as "no
    attestation history", the higher-sampling-rate branch). Returns True
    iff this request was selected AND successfully stashed.

    This function does NOT write provider_work.verification -- the
    caller's record_work() must set verification='sampled' when this
    returns True (leaving it at the 'pending' default otherwise). The
    verifier job below only ever queries by that verification value, never
    by stash presence, so: stash-without-'sampled' just orphans an unread
    stash (harmless, expires in 20 min); 'sampled'-without-stash makes the
    row fall through to the 24h aging path instead of being replayed this
    run (see list_agable_pending_work).
    """
    attested_expected = bool(node.get("attested_heartbeat")) if node else False
    if not should_spot_check(billing_ref, attested_expected=attested_expected):
        return False
    return stash_prompt_for_spot_check(billing_ref, messages, model)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _extract_reply(raw: Any) -> tuple[str, int]:
    """(text, completion_tokens) from an OpenAI-SDK-shaped response object
    -- the ProviderAdapter contract documented in
    src/services/providers/base.py (raw.choices[0].message.content,
    raw.usage.completion_tokens), which both W-A2's per-node adapter
    (object-form) and PROVIDER_ROUTING's function-form entries satisfy."""
    text = raw.choices[0].message.content or ""
    completion_tokens = raw.usage.completion_tokens if raw.usage else 0
    return text, completion_tokens


def _get_node_adapter(node: dict) -> Any:
    """The per-node `OpenAICompatAdapter` for replay (object exposing
    `.request(messages, model, **params) -> raw`, see
    src/services/providers/base.py), built by W-A2's
    community_adapter.adapter_for_node (merged gatewayz-backend#2287) from
    the node's decrypted endpoint key. Cached per node id by that module;
    returns None (logged) on any construction failure -- callers must
    treat None as "can't replay this run", not crash the job."""
    try:
        return adapter_for_node(node)
    except Exception as e:
        logger.warning("adapter_for_node failed for node %s: %s", node.get("id"), e)
        return None


def _get_reference_request_fn() -> Any:
    """The `request` callable for COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER,
    or None if unset/unregistered/unavailable."""
    slug = Config.COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER
    if not slug:
        return None
    try:
        from src.handlers.provider_registry import PROVIDER_ROUTING
    except ImportError as e:
        logger.warning("provider_registry unavailable, skipping reference cross-check: %s", e)
        return None
    routing = PROVIDER_ROUTING.get(slug)
    if not routing or not routing.get("request"):
        logger.warning(
            "COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER=%r has no registered request function", slug
        )
        return None
    return routing["request"]


async def _replay(
    request_fn: Any, model: str, messages: list[dict], max_tokens: int
) -> tuple[str, int] | None:
    try:
        raw = await asyncio.to_thread(
            request_fn, messages=messages, model=model, temperature=0, max_tokens=max_tokens
        )
        return _extract_reply(raw)
    except Exception as e:
        logger.warning("spot-check replay failed: %s", e)
        return None


def _first_n_tokens(text: str, n: int) -> str:
    return " ".join(text.split()[:n])


def _within_tolerance(actual: int, claimed: int, tolerance: float) -> bool:
    if claimed <= 0:
        return True  # nothing plausible to compare against; don't fail on this alone
    return abs(actual - claimed) <= claimed * tolerance


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


async def _verify_sampled_row(work: dict) -> str:
    """'verified' | 'failed' | 'skipped' for one sampled provider_work row.
    'skipped' means "couldn't be resolved this run" (missing stash, node,
    or replay infra) -- the row stays 'sampled' and is picked up again
    later, either next run or via the 24h aging path once stale enough."""
    billing_ref = work["billing_ref"]
    stash = get_stashed_prompt(billing_ref)
    if stash is None:
        logger.info(
            "spot-check: no stash for billing_ref=%s (expired or never written); "
            "deferring to the 24h aging path",
            billing_ref,
        )
        return "skipped"

    node = get_node(work.get("node_id"))
    if node is None:
        logger.warning(
            "spot-check: node %s not found for work %s", work.get("node_id"), work.get("id")
        )
        return "skipped"

    adapter = _get_node_adapter(node)
    if adapter is None or not hasattr(adapter, "request"):
        return "skipped"

    claimed_tokens = work.get("completion_tokens") or 0
    max_tokens = (
        min(_REPLAY_MAX_TOKENS_CAP, claimed_tokens)
        if claimed_tokens > 0
        else _REPLAY_MAX_TOKENS_CAP
    )
    messages = stash["messages"]
    model = stash.get("model") or work.get("model")

    node_reply = await _replay(adapter.request, model, messages, max_tokens)
    if node_reply is None:
        return "skipped"
    node_text, node_completion_tokens = node_reply

    if not node_text.strip():
        return "failed"
    if not _within_tolerance(node_completion_tokens, claimed_tokens, _TOKEN_COUNT_TOLERANCE):
        return "failed"

    reference_fn = _get_reference_request_fn()
    if reference_fn is not None:
        reference_reply = await _replay(reference_fn, model, messages, max_tokens)
        if reference_reply is not None:
            reference_text, _ = reference_reply
            similarity = difflib.SequenceMatcher(
                None,
                _first_n_tokens(node_text, _REPLAY_MAX_TOKENS_CAP),
                _first_n_tokens(reference_text, _REPLAY_MAX_TOKENS_CAP),
            ).ratio()
            if similarity < _SIMILARITY_THRESHOLD:
                return "failed"

    return "verified"


def _apply_sampled_outcome(work: dict, outcome: str) -> str:
    """Persist `outcome` for a sampled row and its side effects. Returns
    the FINAL outcome actually written -- may differ from the `outcome`
    argument when a 'verified' row turns out not payable (PR #2288 review
    C1: an unknown/unlisted model id): that case is written as 'skipped',
    not 'verified'. Callers must use the RETURN VALUE for stats, not the
    argument."""
    work_id = work["id"]
    node_id = work.get("node_id")

    if outcome == "verified":
        result = record_earning_for_verified_work(work)
        if result.outcome == "not_payable":
            set_verification(work_id, "skipped")
            logger.info(
                "spot-check: work %s verified but not payable (model %r not on the payout "
                "allow-list); marked skipped instead",
                work_id,
                work.get("model"),
            )
            return "skipped"
        set_verification(work_id, "verified")
        return "verified"

    set_verification(work_id, outcome)
    if outcome == "failed":
        # Defensive: a 'failed' sampled row never had an earning created
        # (accrual only happens on verified, per record_earning_for_verified_work),
        # so this is a no-op today -- kept per spec §5's explicit "fail ->
        # ... provider_earnings.status='void'" wording, and cheap insurance
        # against a future re-verification path that might re-flip an
        # already-verified row.
        void_earning_for_work(work_id)
        if node_id is not None:
            adjust_health_score(node_id, -_HEALTH_PENALTY_ON_FAILURE)
            since = (datetime.now(UTC) - timedelta(hours=_UNSAMPLED_AGE_HOURS)).isoformat()
            failed_count, _total = node_verification_stats_since(node_id, since)
            if failed_count >= _MAX_FAILS_BEFORE_DISABLE:
                disable_node(node_id)
                logger.warning(
                    "spot-check: node %s disabled after %s failures in the last 24h",
                    node_id,
                    failed_count,
                )
    return outcome


def _resolve_aged_row(work: dict) -> str:
    """'verified' | 'skipped' for a provider_work row that aged past 24h
    without ever being replayed (never sampled, or sampled but never
    resolved). Verified (and paid) iff the node's daily failure rate is
    below threshold; otherwise skipped (unpaid) -- never 'failed', since
    we have no evidence this specific row was bad, only that the node's
    recent track record is poor."""
    node_id = work.get("node_id")
    since = (datetime.now(UTC) - timedelta(hours=_UNSAMPLED_AGE_HOURS)).isoformat()
    failed, total = node_verification_stats_since(node_id, since) if node_id is not None else (0, 0)
    failure_rate = (failed / total) if total > 0 else 0.0
    return "verified" if failure_rate < _UNSAMPLED_MAX_DAILY_FAILURE_RATE else "skipped"


def _resolve_verified_aged_row_outcome(work: dict) -> str:
    """For an aged row _resolve_aged_row already decided is 'verified':
    apply the same C1 payability check the sampled path applies, so an
    unknown-model row never gets marked 'verified' just because its
    node's failure rate happens to be low. Returns 'verified' or 'skipped'."""
    result = record_earning_for_verified_work(work)
    if result.outcome == "not_payable":
        logger.info(
            "spot-check: aged work %s would verify but isn't payable (model %r not on the "
            "payout allow-list); marked skipped instead",
            work.get("id"),
            work.get("model"),
        )
        return "skipped"
    return "verified"


def _reconcile_missing_earnings() -> int:
    """Idempotent retry of earnings accrual for recently-verified work
    (PR #2288 review I1): a work item can be 'verified' with no
    provider_earnings row if its first accrual attempt failed for a
    non-duplicate DB reason (create_earning's 'db_error' outcome).
    record_earning_for_verified_work is safe to re-attempt -- the UNIQUE
    (work_id) constraint makes an already-paid row's re-attempt a cheap
    'duplicate' no-op -- so this simply re-runs it for every row verified
    in the last COMMUNITY_EARNINGS_RECONCILE_LOOKBACK_HOURS (default 48h,
    double the 24h aging window) instead of tracking which rows failed.
    Returns the count of earnings actually created this pass (0 is the
    normal case -- it only creates something when a prior attempt failed
    non-duplicately)."""
    since = (
        datetime.now(UTC) - timedelta(hours=Config.COMMUNITY_EARNINGS_RECONCILE_LOOKBACK_HOURS)
    ).isoformat()
    created = 0
    for work in list_verified_work_since(since):
        result = record_earning_for_verified_work(work)
        if result.outcome == "created":
            created += 1
            logger.warning(
                "spot-check: reconciled a missing earning for previously-verified work %s "
                "(its first accrual attempt must have failed non-duplicately)",
                work.get("id"),
            )
    return created


async def run_spot_check_verification() -> dict[str, int]:
    """The scheduled job (every COMMUNITY_SPOTCHECK_INTERVAL_MINUTES,
    default 10). Resolves sampled rows via replay (bounded by
    COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_RUN and a per-node
    COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_NODE_PER_RUN cap, sequential with
    a small delay between replays -- PR #2288 review I2, so the job can't
    run unbounded or hammer a single busy node), ages out unresolved rows
    past 24h, then reconciles any 'verified' work missing an earnings row
    (I1). Never raises -- a per-row failure is caught inside
    _verify_sampled_row/_replay and treated as 'skipped' for that row."""
    now = datetime.now(UTC)
    sampled_since = (now - timedelta(hours=_SAMPLED_LOOKBACK_HOURS)).isoformat()
    agable_before = (now - timedelta(hours=_UNSAMPLED_AGE_HOURS)).isoformat()

    stats = {"verified": 0, "failed": 0, "skipped": 0}

    replays_this_run = 0
    replays_by_node: dict[int, int] = {}
    max_replays_per_run = Config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_RUN
    max_replays_per_node = Config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_NODE_PER_RUN

    for work in list_sampled_pending_work(sampled_since):
        if replays_this_run >= max_replays_per_run:
            logger.info(
                "spot-check: per-run replay cap (%s) reached; remaining sampled rows deferred "
                "to a future run",
                max_replays_per_run,
            )
            break

        node_id = work.get("node_id")
        if node_id is not None and replays_by_node.get(node_id, 0) >= max_replays_per_node:
            stats["skipped"] += 1
            continue

        try:
            outcome = await _verify_sampled_row(work)
        except Exception as e:
            logger.warning("spot-check: unexpected error verifying work %s: %s", work.get("id"), e)
            outcome = "skipped"

        replays_this_run += 1
        if node_id is not None:
            replays_by_node[node_id] = replays_by_node.get(node_id, 0) + 1

        if outcome == "skipped":
            stats["skipped"] += 1
        else:
            final_outcome = _apply_sampled_outcome(work, outcome)
            stats[final_outcome] += 1

        await asyncio.sleep(Config.COMMUNITY_SPOTCHECK_REPLAY_DELAY_SECONDS)

    for work in list_agable_pending_work(agable_before):
        try:
            outcome = _resolve_aged_row(work)
        except Exception as e:
            logger.warning("spot-check: unexpected error aging work %s: %s", work.get("id"), e)
            outcome = "skipped"
        if outcome == "verified":
            outcome = _resolve_verified_aged_row_outcome(work)
        set_verification(work["id"], outcome)
        stats[outcome] += 1

    reconciled = _reconcile_missing_earnings()

    logger.info(
        "spot-check verification run complete | verified=%s failed=%s skipped=%s reconciled=%s",
        stats["verified"],
        stats["failed"],
        stats["skipped"],
        reconciled,
    )
    return stats
