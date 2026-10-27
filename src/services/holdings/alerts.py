"""Ops alerts for the holdings observation sweep.

The sweep fails closed: a wallet it cannot fully measure is skipped, never
recorded as smaller. That is right for the money and invisible to everyone
else -- on 2026-10-08 prod had recorded zero wallets for three days because
polygon-rpc.com began answering 401, every wallet had one unreadable chain,
and the only trace was a WARNING per wallet. These two checks make that loud:

* **recorded nothing** -- a sweep that considered eligible wallets and
  recorded a sweep row for none of them alerts straight away
  (:func:`alert_if_sweep_recorded_nothing`, from the sweep job wrapper);
* **stale** -- an hourly watchdog alerts when no sweep has recorded any wallet
  for ``HOLDINGS_SWEEP_STALE_HOURS`` while eligible wallets exist
  (:func:`check_sweep_staleness`). This is the backstop for a sweep that is
  not running at all, which the first check cannot see.

Each condition alerts at most once per ``HOLDINGS_ALERT_COOLDOWN_HOURS``,
claimed with a Redis ``SET NX EX`` so several API instances send one email
between them, with an in-process fallback when Redis is down. Delivery reuses
the provider-alert path (``resolve_ops_recipients`` + ``send_email``), and
every alert is also logged at ERROR, which Sentry's logging integration
captures -- so with no recipient configured the alert is still one ERROR,
never silence.

Alerts carry counts, chain ids and timestamps only: never a wallet address,
an RPC URL or a key.
"""

from __future__ import annotations

import html
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.config import Config
from src.config.redis_config import get_redis_client
from src.services.holdings.chains import CHAIN_NAMES

logger = logging.getLogger(__name__)

CONDITION_RECORDED_NOTHING = "sweep_recorded_nothing"
CONDITION_STALE = "sweep_stale"

_DEDUPE_KEY_PREFIX = "ops:alert:holdings:"

# condition -> unix time its in-process claim expires. Used only when Redis is
# unavailable; per-instance, like every other fallback store in this codebase.
_fallback_claims: dict[str, float] = {}

# When this process first ran the staleness check. A registry that has never
# recorded a wallet is measured from here rather than from "forever", so a
# freshly enabled feature does not alert before its first sweep had a chance.
_watch_started_at: datetime | None = None


def _cooldown_seconds() -> int:
    return max(60, int(float(Config.HOLDINGS_ALERT_COOLDOWN_HOURS) * 3600))


def _claim(condition: str) -> bool:
    """True if this caller won the right to send ``condition`` now."""
    ttl = _cooldown_seconds()
    client = None
    try:
        client = get_redis_client()
    except Exception as e:  # noqa: BLE001 - fall back to the in-process store
        logger.debug("holdings alerts: Redis client unavailable: %s", e)

    if client is not None:
        try:
            return bool(client.set(f"{_DEDUPE_KEY_PREFIX}{condition}", "1", nx=True, ex=ttl))
        except Exception as e:  # noqa: BLE001
            logger.warning("holdings alerts: Redis dedupe failed, using in-process: %s", e)

    now = time.time()
    if _fallback_claims.get(condition, 0.0) > now:
        return False
    _fallback_claims[condition] = now + ttl
    return True


def _release(condition: str) -> None:
    """Give the claim back so the next occurrence can retry delivery."""
    _fallback_claims.pop(condition, None)
    try:
        client = get_redis_client()
        if client is not None:
            client.delete(f"{_DEDUPE_KEY_PREFIX}{condition}")
    except Exception as e:  # noqa: BLE001
        logger.debug("holdings alerts: Redis release failed: %s", e)


def _send(condition: str, subject: str, lines: list[str]) -> bool:
    """Deliver one deduped ops alert. True if it was sent (or logged with no
    recipient); False if suppressed by the cooldown. Never raises."""
    try:
        if not _claim(condition):
            logger.debug("holdings alerts: %s suppressed by cooldown", condition)
            return False

        text = "\n".join(lines)
        # Always one ERROR: it is the Sentry event, and the only signal at all
        # when no recipient is configured.
        logger.error("HOLDINGS OPS ALERT [%s] %s | %s", condition, subject, " | ".join(lines))

        from src.services.provider_alerting import resolve_ops_recipients

        recipients, _source = resolve_ops_recipients()
        if not recipients:
            logger.error(
                "HOLDINGS OPS ALERT [%s] has no recipient: set OPS_ALERT_EMAIL on the api "
                "service (or add an active admin/superadmin)",
                condition,
            )
            return True

        from src.services.email import send_email

        body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
        any_sent = False
        for to in recipients:
            result = send_email(to=to, subject=subject, html=body, text=text)
            any_sent = any_sent or bool(result.sent)
        if not any_sent:
            logger.error("HOLDINGS OPS ALERT [%s] email delivery failed", condition)
            _release(condition)
        return True
    except Exception as e:  # noqa: BLE001 - alerting must never break the job
        logger.error("holdings alerts: failed to send %s: %s", condition, e)
        return False


def _chain_label(chain_id: Any) -> str:
    try:
        cid = int(chain_id)
    except (TypeError, ValueError):
        return str(chain_id)
    return f"{CHAIN_NAMES.get(cid, 'unknown')} ({cid})"


def sweep_recorded_nothing(summary: dict[str, Any] | None) -> bool:
    """True when a sweep looked at eligible wallets and recorded none."""
    if not summary or summary.get("skipped") in ("disabled", "no_tokens"):
        return False
    considered = int(summary.get("wallets_considered") or 0)
    return considered > 0 and int(summary.get("sweeps_recorded") or 0) == 0


def alert_if_sweep_recorded_nothing(summary: dict[str, Any]) -> bool:
    """Alert when a sweep considered wallets but recorded none of them.

    Returns True when an alert went out (False when there was nothing to
    alert on, the feature is off, or the cooldown suppressed it).
    """
    if not Config.HOLDINGS_REWARDS_ENABLED or not sweep_recorded_nothing(summary):
        return False

    skipped = summary.get("skipped") or {}
    failed_chains = summary.get("failed_chains") or {}
    lines = [
        f"The holdings sweep at {summary.get('taken_at')} considered "
        f"{summary.get('wallets_considered')} eligible wallet(s) and recorded none. "
        "Nobody can earn holdings rewards for a day without recorded sweeps.",
        "Skipped: " + (", ".join(f"{k}={v}" for k, v in sorted(skipped.items()) if v) or "none"),
        "Chains that failed to read (wallets affected): "
        + (
            ", ".join(f"{_chain_label(cid)}={n}" for cid, n in sorted(failed_chains.items()))
            or "none"
        ),
        "An incomplete read skips the whole wallet. Check the RPC for each failed chain "
        "(<CHAIN>_RPC_URL / ALCHEMY_API_KEY on Railway service api) and the price feed.",
    ]
    return _send(
        CONDITION_RECORDED_NOTHING,
        "[Gatewayz] Holdings sweep recorded no wallets",
        lines,
    )


def sweep_staleness_status(now: datetime | None = None) -> dict[str, Any]:
    """Whether the sweep has gone quiet. Read-only: never alerts.

    ``stale`` is None when it could not be determined (a failed lookup), so
    an unreachable DB is reported as unknown rather than as an outage of the
    sweep itself.
    """
    global _watch_started_at
    now = now or datetime.now(UTC)
    if _watch_started_at is None:
        _watch_started_at = now

    threshold_hours = float(Config.HOLDINGS_SWEEP_STALE_HOURS)
    status: dict[str, Any] = {
        "enabled": bool(Config.HOLDINGS_REWARDS_ENABLED),
        "threshold_hours": threshold_hours,
        "last_recorded_at": None,
        "hours_since_last_recorded": None,
        "eligible_wallets": None,
        "stale": False,
    }
    if not Config.HOLDINGS_REWARDS_ENABLED:
        return status

    from src.db.holdings import HoldingsLookupError, get_latest_sweep_taken_at
    from src.db.user_wallets import count_wallets_linked_before

    try:
        last_recorded = get_latest_sweep_taken_at()
    except HoldingsLookupError as e:
        logger.warning("holdings sweep watchdog: %s", e)
        status["stale"] = None
        status["error"] = "sweep lookup failed"
        return status

    cutoff = now - timedelta(days=Config.HOLDINGS_MIN_WALLET_AGE_DAYS)
    eligible = count_wallets_linked_before(cutoff)
    status["eligible_wallets"] = eligible
    if eligible is None:
        status["stale"] = None
        status["error"] = "wallet count failed"
        return status

    since = last_recorded or _watch_started_at
    if last_recorded is not None:
        status["last_recorded_at"] = last_recorded.isoformat()
    hours_since = (now - since).total_seconds() / 3600
    status["hours_since_last_recorded"] = round(hours_since, 2)
    status["stale"] = eligible > 0 and hours_since > threshold_hours
    return status


def check_sweep_staleness(now: datetime | None = None) -> dict[str, Any]:
    """The hourly watchdog: compute :func:`sweep_staleness_status` and alert
    when stale. Returns the status, plus ``alerted``."""
    status = sweep_staleness_status(now)
    status["alerted"] = False
    if status.get("stale"):
        last = status.get("last_recorded_at") or "never (since this instance started)"
        status["alerted"] = _send(
            CONDITION_STALE,
            "[Gatewayz] Holdings sweep has not recorded a wallet for "
            f"{status['hours_since_last_recorded']}h",
            [
                f"No holdings sweep has recorded any wallet for "
                f"{status['hours_since_last_recorded']}h (threshold "
                f"{status['threshold_hours']}h) while {status['eligible_wallets']} eligible "
                "wallet(s) exist.",
                f"Last recorded sweep: {last}.",
                "Check the holdings_snapshots job on GET /admin/status: if it is running, "
                "its summary lists the skip reasons and failed chains; if it is not, the "
                "scheduler is down.",
            ],
        )
    return status


def holdings_sweep_health() -> dict[str, Any]:
    """For GET /admin/status: staleness plus the last sweep's coverage."""
    from src.services.ops.job_runs import get_job_runs

    status = sweep_staleness_status()
    record = get_job_runs(["holdings_snapshots"]).get("holdings_snapshots") or {}
    summary = record.get("summary") or {}
    status["last_sweep"] = {
        "ran_at": record.get("ran_at"),
        "wallets_considered": summary.get("wallets_considered"),
        "sweeps_recorded": summary.get("sweeps_recorded"),
        "skipped": summary.get("skipped"),
        "failed_chains": summary.get("failed_chains"),
        "recorded_nothing": sweep_recorded_nothing(summary),
    }
    status["degraded"] = bool(status.get("stale")) or status["last_sweep"]["recorded_nothing"]
    return status


def _reset_for_tests() -> None:
    global _watch_started_at
    _fallback_claims.clear()
    _watch_started_at = None
