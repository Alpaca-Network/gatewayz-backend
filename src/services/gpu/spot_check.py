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
    node_verification_stats_since,
    set_verification,
    void_earning_for_work,
)
from src.services.gpu.earnings import record_earning_for_verified_work

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
    """Lazily import W-A2's per-node adapter getter. Expected contract:
    `get_node_adapter(node: dict) -> ProviderAdapter` (object exposing
    `.request(messages, model, **params) -> raw`, see
    src/services/providers/base.py), built from the node's decrypted
    endpoint key (m4/spec.md §4). Returns None (logged) on ImportError --
    W-A2 not merged yet -- or any construction failure; callers must treat
    None as "can't replay this run", not crash the job."""
    try:
        from src.services.providers.community_adapter import (  # type: ignore[import-not-found]
            get_node_adapter,
        )
    except ImportError:
        logger.info(
            "src.services.providers.community_adapter.get_node_adapter not available yet "
            "(W-A2 not merged) -- spot-check replay skipped this run"
        )
        return None
    try:
        return get_node_adapter(node)
    except Exception as e:
        logger.warning("get_node_adapter failed for node %s: %s", node.get("id"), e)
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


def _apply_sampled_outcome(work: dict, outcome: str) -> None:
    work_id = work["id"]
    node_id = work.get("node_id")
    set_verification(work_id, outcome)

    if outcome == "verified":
        record_earning_for_verified_work(work)
        return
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


async def run_spot_check_verification() -> dict[str, int]:
    """The scheduled job (every COMMUNITY_SPOTCHECK_INTERVAL_MINUTES,
    default 10). Resolves sampled rows via replay and ages out unresolved
    rows past 24h. Never raises -- a per-row failure is caught inside
    _verify_sampled_row/_replay and treated as 'skipped' for that row."""
    now = datetime.now(UTC)
    sampled_since = (now - timedelta(hours=_SAMPLED_LOOKBACK_HOURS)).isoformat()
    agable_before = (now - timedelta(hours=_UNSAMPLED_AGE_HOURS)).isoformat()

    stats = {"verified": 0, "failed": 0, "skipped": 0}

    for work in list_sampled_pending_work(sampled_since):
        try:
            outcome = await _verify_sampled_row(work)
        except Exception as e:
            logger.warning("spot-check: unexpected error verifying work %s: %s", work.get("id"), e)
            outcome = "skipped"
        if outcome == "skipped":
            stats["skipped"] += 1
            continue
        _apply_sampled_outcome(work, outcome)
        stats[outcome] += 1

    for work in list_agable_pending_work(agable_before):
        try:
            outcome = _resolve_aged_row(work)
        except Exception as e:
            logger.warning("spot-check: unexpected error aging work %s: %s", work.get("id"), e)
            outcome = "skipped"
        set_verification(work["id"], outcome)
        if outcome == "verified":
            record_earning_for_verified_work(work)
        stats[outcome] += 1

    logger.info(
        "spot-check verification run complete | verified=%s failed=%s skipped=%s",
        stats["verified"],
        stats["failed"],
        stats["skipped"],
    )
    return stats
