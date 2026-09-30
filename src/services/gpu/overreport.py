"""Track community nodes whose self-reported completion tokens exceed the gateway's
own count (the min(reported, 1.1x gateway count) cap in community_adapter).

A single overshoot can be a tokenizer mismatch; repeated overshoots in 24h mean the
node is inflating usage. Flagged nodes take a health penalty and lose the 24h-aging
auto-verification in spot_check (their unsampled work is skipped, i.e. unpaid, until
the counter expires). State lives in Redis with a 24h TTL; if Redis is unavailable
nothing is recorded and nothing is flagged (fail-open on flagging only -- the payout
cap itself is unconditional).
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.config import Config
from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "gpu_overreport:"
_WINDOW_SECONDS = 24 * 3600
_HEALTH_PENALTY = 20


def _key(node_id: Any) -> str:
    return f"{_KEY_PREFIX}{node_id}"


def record_overreport(node_id: Any) -> int:
    """Count one over-report for *node_id*. Returns the running 24h count (0 if
    unrecorded). Applies a health penalty exactly when the flag threshold is hit."""
    if node_id is None:
        return 0
    client = get_redis_client()
    if client is None:
        return 0
    try:
        count = int(client.incr(_key(node_id)))
        if count == 1:
            client.expire(_key(node_id), _WINDOW_SECONDS)
    except Exception as e:
        logger.warning("record_overreport(%s) failed: %s", node_id, e)
        return 0
    if count == Config.COMMUNITY_OVERREPORT_FLAG_THRESHOLD:
        logger.warning(
            "community node %s flagged: %s over-reported completion counts in 24h", node_id, count
        )
        try:
            from src.db.gpu_payouts import adjust_health_score

            adjust_health_score(node_id, -_HEALTH_PENALTY)
        except Exception as e:
            logger.warning("overreport health penalty failed for %s: %s", node_id, e)
    return count


def is_overreport_flagged(node_id: Any) -> bool:
    if node_id is None:
        return False
    client = get_redis_client()
    if client is None:
        return False
    try:
        raw = client.get(_key(node_id))
        if raw is None:
            return False
        if isinstance(raw, bytes):
            raw = raw.decode()
        return int(raw) >= Config.COMMUNITY_OVERREPORT_FLAG_THRESHOLD
    except Exception as e:
        logger.warning("is_overreport_flagged(%s) failed: %s", node_id, e)
        return False
