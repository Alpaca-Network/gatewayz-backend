"""Per-tag spend budgets, and the one-shot webhook that says the line was crossed.

The partner attribution plan asks for budgets "so an initiative can have a
compute budget an agent cannot silently blow". Silently is the word that
matters: an autonomous agent with no feedback loop spends until a human reads
a bill.

Three properties this module exists to hold:

  ONE SHOT   The notification fires when the budget is CROSSED, not while it is
             exceeded. A webhook on every subsequent call is how an alert
             becomes noise and then becomes ignored.
  ADVISORY   Crossing a budget notifies; it does not block the call. Silently
             failing a customer's production traffic because a soft limit was
             passed is a worse outcome than the overspend, and a hard stop is
             what request caps are for.
  NEVER FATAL  Any failure here -- a dead webhook, a slow DNS, a missing table
             -- is swallowed. Billing telemetry must not take down inference.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import UTC, datetime
from typing import Any

from src.services.webhook_target import InvalidWebhookTarget, validate_webhook_url

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 5


def _post_json(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "gatewayz-webhook/1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SECONDS):
        return


def notify_budget_crossed(
    *,
    webhook_url: str,
    user_id: int,
    tag: str,
    limit_usd: float,
    spent_usd: float,
) -> bool:
    """Deliver the crossing notice. True if it was accepted.

    The URL is re-validated HERE, not only at registration. DNS can change
    between the two, which is precisely what a rebinding attack is: a hostname
    that resolved publicly when the budget was created and resolves to
    169.254.169.254 by the time we send.
    """
    try:
        validate_webhook_url(webhook_url)
    except InvalidWebhookTarget as e:
        logger.warning("budget webhook refused for user=%s tag=%s: %s", user_id, tag, e)
        return False

    payload = {
        "event": "usage.budget.crossed",
        "tag": tag,
        "limit_usd": round(float(limit_usd), 6),
        "spent_usd": round(float(spent_usd), 6),
        "occurred_at": datetime.now(UTC).isoformat(),
        # Said explicitly so a consumer never reads this as a stop signal.
        "enforcement": "advisory",
    }
    try:
        _post_json(webhook_url, payload)
        return True
    except Exception as e:  # noqa: BLE001 - a customer's endpoint being down is not our failure
        logger.warning("budget webhook delivery failed for user=%s tag=%s: %s", user_id, tag, e)
        return False
