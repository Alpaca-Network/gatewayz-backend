#!/usr/bin/env python3
"""Make provider credit exhaustion visible to operators.

``is_provider_budget_error()`` (``src/utils/errors.py``) detects an unfunded provider
account precisely and then deliberately replaces it with ``PROVIDER_CAPACITY_MESSAGE``.
Masking our own billing state from customers is correct and stays. This module adds the
signal that was missing on the *other* side: a durable, operator-facing record that says
which provider ran out of money, why, since when, and how often -- surfaced at
``GET /admin/status``.

Two properties matter more than anything else here.

**Recording can never break inference.** ``record_provider_budget_error`` swallows every
exception, the durable write happens on a dedicated thread the request never waits on,
and nothing in the request path acts on a return value. Each of the three call sites
additionally wraps the call, which covers the failures that guarantee cannot reach -- an
ImportError, a circular import, a module that failed to load. A monitoring side-effect
that can fail a chat completion would be a strictly worse bug than the invisibility it
fixes.

``provider_budget_status`` is the exception and deliberately raises: it runs on the admin
read path, where a swallowed failure would render as "every provider is funded".

**Nothing derived from upstream error text is persisted.** The only thing taken from the
provider's message is which of the fixed ``PROVIDER_BUDGET_REASONS`` constants it matched
(see ``classify_provider_budget_error``); unrecognized values are coerced to ``unknown``
before they reach the database. Upstream budget errors embed key ids and dashboard URLs,
so the admin surface gets specificity from fields the gateway itself owns -- provider,
reason, model id, counts, timestamps -- never from a slice of the upstream string.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from src.utils.errors import PROVIDER_BUDGET_REASONS, classify_provider_budget_error

logger = logging.getLogger(__name__)

# How long a (provider, reason) pair must go without a durable write before the next
# detection triggers one. Detection happens on a per-request failure path; at the scale of
# the 2026-09-16 incident (57 of ~65 models down) an unthrottled writer would issue a
# Supabase round trip per failed request. Occurrences seen inside the interval are
# counted in-process and handed to the next flush, so throttling costs latency on the
# alert, not accuracy of its count.
FLUSH_INTERVAL_SECONDS = 60.0

# Default recency window for the /admin/status block. Rows are never deleted; this is a
# read-time filter that decides what still counts as "current". It is also handed to the
# writer, which uses it as the incident boundary -- so "no longer reported" and "a later
# failure is a new incident rather than a continuation" are the same threshold by
# construction rather than by two constants agreeing.
DEFAULT_WINDOW_HOURS = 24

# Marker set on an exception once it has been recorded, so a single upstream failure that
# passes through more than one detection site (map_provider_error() and then
# ChatHandler's own budget branch) is counted once.
_RECORDED_ATTR = "_gatewayz_budget_event_recorded"

# One worker: writes are throttled to at most one per (provider, reason) per interval, so
# a single thread is ample, and a dedicated pool means a slow Supabase can never queue
# behind -- or starve -- the shared DB executor used by catalog refresh and model sync.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-budget")


@dataclass
class _Pending:
    """Occurrences accumulated in-process since this key's last durable write."""

    count: int = 0
    sample_model: str | None = None
    last_flush_at: float = 0.0


_pending: dict[tuple[str, str], _Pending] = {}
_lock = threading.Lock()


def _normalize_reason(reason: str | None) -> str:
    """Coerce anything outside the closed vocabulary to "unknown".

    This is the guarantee that raw upstream text cannot reach the database even if the
    classifier is later changed to return something unexpected. The migration deliberately
    carries no CHECK constraint, so this function is the enforcement point.
    """
    if reason in PROVIDER_BUDGET_REASONS:
        return str(reason)
    if reason is not None:
        logger.warning("Unrecognized provider budget reason %r; recording as 'unknown'", reason)
    return "unknown"


def _submit(fn: Any, *args: Any) -> None:
    """Hand work to the background writer. Indirection exists so tests can run the flush
    synchronously without patching ThreadPoolExecutor internals."""
    _executor.submit(fn, *args)


def _flush(provider: str, reason: str, sample_model: str | None, count: int) -> None:
    """Durable write, off the request path. Never raises into anything that matters."""
    from src.db.provider_budget_events import record_budget_event

    try:
        record_budget_event(
            provider=provider,
            reason=reason,
            sample_model=sample_model,
            increment=count,
            # Same window /admin/status reads with: a row that has dropped out of the
            # report has stopped being a current incident, so the next occurrence starts
            # a new one instead of inheriting a months-old first_seen.
            stale_after_hours=DEFAULT_WINDOW_HOURS,
        )
        logger.warning(
            "Provider budget exhausted: provider=%s reason=%s model=%s occurrences=%d "
            "(recorded; see GET /admin/status)",
            provider,
            reason,
            sample_model,
            count,
        )
    except Exception as e:
        # Put the occurrences back so the next detection retries them instead of losing
        # the count to a transient Supabase failure. Also reset last_flush_at so the next
        # detection is not throttled away.
        logger.warning(
            "Failed to persist provider budget event (provider=%s reason=%s); "
            "re-queuing %d occurrence(s): %s",
            provider,
            reason,
            count,
            e,
        )
        try:
            with _lock:
                entry = _pending.setdefault((provider, reason), _Pending())
                entry.count += count
                entry.sample_model = entry.sample_model or sample_model
                entry.last_flush_at = 0.0
        except Exception:  # pragma: no cover - defensive; a lock failure is unreachable
            logger.warning("Failed to re-queue provider budget event", exc_info=True)


def record_provider_budget_error(
    provider: str | None,
    model: str | None,
    raw_error: str | None,
    exc: BaseException | None = None,
) -> bool:
    """Record that ``provider`` hit a budget/credit limit. Returns True if it counted.

    Safe to call from any detection site with anything: the entire body is guarded, and a
    False return means "not recorded", never "your request failed". Callers must not act
    on the return value -- it exists for tests.
    """
    try:
        if exc is not None and getattr(exc, _RECORDED_ATTR, False):
            return False

        reason = classify_provider_budget_error(raw_error)
        if reason is None:
            # The caller decided this was a budget error on evidence the text does not
            # carry (an httpx 402 with an empty body, say). Record it as unknown rather
            # than dropping a real outage on a classification miss.
            reason = "unknown"
        reason = _normalize_reason(reason)

        provider_name = str(provider) if provider else "unknown"
        sample_model = str(model) if model else None

        if exc is not None:
            try:
                setattr(exc, _RECORDED_ATTR, True)
            except Exception:
                # Exceptions with __slots__ cannot carry the latch. Worst case the event
                # is counted twice; that is strictly better than not counting it.
                pass

        key = (provider_name, reason)
        now = time.monotonic()
        with _lock:
            entry = _pending.setdefault(key, _Pending())
            entry.count += 1
            if sample_model:
                entry.sample_model = sample_model
            if entry.last_flush_at and (now - entry.last_flush_at) < FLUSH_INTERVAL_SECONDS:
                return True
            due_count = entry.count
            due_model = entry.sample_model
            entry.count = 0
            entry.last_flush_at = now

        _submit(_flush, provider_name, reason, due_model, due_count)
        return True
    except Exception:
        # Deliberately total. This runs inside an `except` block on the inference path;
        # anything escaping here would replace a provider error the caller already knows
        # how to report with a monitoring bug.
        logger.warning("Failed to record provider budget event", exc_info=True)
        return False


def provider_budget_status(within_hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """The ``provider_budget`` block of ``GET /admin/status``.

    Raises if the read fails, so ``_safe_block`` reports ``{"error": ...}``. Returning an
    empty list on a broken query would render as "every provider is funded" -- the calm,
    healthy-looking failure mode this repo has been burned by before.

    There is no resolution signal, so the window is how an entry clears, and the honest
    cost is that topping an account up leaves it reading "degraded" until the window
    rolls off. ``last_seen`` is in every entry for exactly that reason: it is what
    separates "still failing" from "failed this morning". The window is long (a day)
    rather than short on purpose -- a provider whose circuit breaker has opened stops
    generating events while remaining just as unfunded, and a stale "degraded" is a much
    cheaper mistake than a premature "ok".
    """
    from src.db.provider_budget_events import list_recent_budget_events

    rows = list_recent_budget_events(within_hours=within_hours)

    exhausted = [
        {
            "provider": row.get("provider"),
            "reason": _normalize_reason(row.get("reason")),
            "first_seen": row.get("first_seen_at"),
            "last_seen": row.get("last_seen_at"),
            "occurrences": row.get("occurrences"),
            "sample_model": row.get("sample_model"),
        }
        for row in rows
    ]

    return {
        # Any budget event inside the window is worth a human's attention: every member
        # of PROVIDER_BUDGET_REASONS means "an account needs money", which does not clear
        # on its own. No threshold, deliberately.
        "status": "degraded" if exhausted else "ok",
        "window_hours": within_hours,
        "exhausted": exhausted,
    }


def _reset_state_for_tests() -> None:
    """Clear the in-process coalescing ledger. Module-level state, so tests that exercise
    throttling must not leak into each other."""
    with _lock:
        _pending.clear()
