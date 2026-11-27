"""Email the account once when billing falls back to the paid balance.

When a billed request draws less discount than its full share
(trunc(price x d, 6)), billing charges the rest to the paid balance, as it
always does. That happens for one of three reasons, read from the balance row
after the draw:

* **credits_depleted**: the discount credits balance is empty;
* **daily_cap_reached**: today's use reached the account's daily cap;
* neither: other in-flight requests hold what is left. Not an event.

For the first two the account gets one email ("You are out of discount
credits" / "You reached today's discount limit"). The app shows the same
state in-app from ``GET /discount-credits``.

Rules:

* **Off by default.** With VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED off nothing
  here runs: no Redis, no DB call, no email.
* **Never slows or fails a request.** Billing only calls
  :func:`notify_after_draw`, which returns at once; the balance read and the
  send happen on a background thread, and every error is logged and dropped.
* **At most once per event per account per UTC day**, deduplicated in Redis
  (``SET NX`` with a two-day expiry, so it holds across workers). An empty
  balance is also emailed only once per depletion: a second key, tied to the
  account's lifetime granted total, is set too, so an account that stays
  empty is not emailed again every day; a new grant that runs out is a new
  event. No Redis means no email (never risk a flood).
* No email when the account set its cap to 0 (it chose not to use discount
  credits), when it was never granted any, or when it has no deliverable
  address.
"""

from __future__ import annotations

import html
import logging
import os
import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config.config import Config

logger = logging.getLogger(__name__)

EVENT_DEPLETED = "credits_depleted"
EVENT_DAILY_CAP = "daily_cap_reached"
EVENTS = (EVENT_DEPLETED, EVENT_DAILY_CAP)

_KEY_PREFIX = "discount_credits:notify"
# Day keys carry the UTC date; they only need to outlive that day.
_DAY_KEY_TTL_SECONDS = 2 * 24 * 3600
# One email per depletion: long enough to outlast any batch of credits
# (about 180 days of expiry).
_DEPLETION_KEY_TTL_SECONDS = 200 * 24 * 3600

# Process-local memo: user_id -> time.monotonic() until which this worker
# does not check the account again. Set for a short while when a check
# starts (so a burst of short draws starts one check, not one per request),
# and until the end of the UTC day once that day's event is handled. Keeps
# an account that sits at zero, or at a cap of 0, from costing a thread, a
# Redis call and a DB read on every request.
_RECHECK_SECONDS = 60.0
_quiet_until: dict[int, float] = {}
_memo_lock = threading.Lock()
_MEMO_MAX = 10_000


def notify_enabled() -> bool:
    """VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED. Config only -- never a DB call."""
    return bool(Config.VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED)


def _today() -> date:
    return datetime.now(UTC).date()


def _decimal(value: Any) -> Decimal:
    try:
        d = Decimal(str(value)) if value is not None else Decimal(0)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)
    return d if d.is_finite() else Decimal(0)


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def classify(row: dict[str, Any] | None, today: date | None = None) -> str | None:
    """Which event a short draw means, from the balance row read after it:
    EVENT_DEPLETED, EVENT_DAILY_CAP, or None. An empty balance wins over the
    cap (it is the one the user can act on beyond waiting a day)."""
    if not isinstance(row, dict):
        return None
    today = today or _today()
    if _decimal(row.get("lifetime_granted_usd")) <= 0:
        return None
    if _decimal(row.get("balance_usd")) <= 0:
        return EVENT_DEPLETED
    cap = _decimal(row.get("daily_cap_usd"))
    if cap <= 0:
        return None
    used = _decimal(row.get("cap_used_usd")) if _as_date(row.get("cap_day")) == today else 0
    if cap - used <= 0:
        return EVENT_DAILY_CAP
    return None


def _day_key(event: str, user_id: int, day: date) -> str:
    return f"{_KEY_PREFIX}:{event}:{user_id}:{day.isoformat()}"


def _depletion_key(user_id: int, row: dict[str, Any]) -> str:
    granted = format(_decimal(row.get("lifetime_granted_usd")).normalize(), "f")
    return f"{_KEY_PREFIX}:{EVENT_DEPLETED}:{user_id}:granted:{granted}"


def _claim(redis_client: Any, key: str, ttl: int) -> bool:
    """True when this call set the key (first time). Raises on Redis errors."""
    return bool(redis_client.set(key, "1", nx=True, ex=ttl))


def _deliverable(email: Any) -> bool:
    if not isinstance(email, str) or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    if "." not in domain:
        return False
    # Placeholders stored for accounts that signed up without an email.
    return not (domain == "privy.user" or domain.endswith("privy.placeholder"))


def _usd(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'))}"


def build_email(event: str, row: dict[str, Any]) -> tuple[str, str, str]:
    """(subject, html, text) for one event."""
    app_url = os.environ.get("APP_URL", "https://gatewayz.ai").rstrip("/")
    settings_url = f"{app_url}/settings/credits"
    link = html.escape(settings_url, quote=True)
    if event == EVENT_DEPLETED:
        subject = "You are out of discount credits"
        lines = [
            "Your Gatewayz account has used all of its discount credits.",
            "Your requests keep working. From now on they are charged in full to your "
            "paid balance.",
            "You will get more discount credits when the vault issues the next ones.",
        ]
    else:
        cap = _usd(_decimal(row.get("daily_cap_usd")))
        subject = "You reached today's discount limit"
        lines = [
            f"Your Gatewayz account used its daily limit of {cap} of discount credits today.",
            "Your requests keep working. Until 00:00 UTC they are charged in full to your "
            "paid balance, and discount credits apply again after that.",
            "You can change the daily limit in your credits settings.",
        ]
    body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
    html_body = (
        f"<h2>{html.escape(subject)}</h2>{body}"
        f'<p><a href="{link}">See your discount credits</a></p>'
        '<p style="font-size: 12px; color: #666;">You get this email at most once a day '
        "for each of these events.</p>"
    )
    text = "\n\n".join([subject, *lines, f"See your discount credits: {settings_url}"])
    return subject, html_body, text


def process(user_id: int) -> str | None:
    """Read the balance, decide the event, deduplicate, send. Returns the
    event emailed, or None. Never raises."""
    try:
        return _process(user_id)
    except Exception as e:  # noqa: BLE001 - a notification never breaks billing
        logger.warning(
            "discount credits notification failed for user %s: %s", user_id, type(e).__name__
        )
        return None


def _process(user_id: int) -> str | None:
    from src.config.redis_config import get_redis_client
    from src.db.inference_discount import get_balance
    from src.db.users import get_user_by_id
    from src.services.email import send_email

    today = _today()
    redis_client = get_redis_client()
    if redis_client is None:
        logger.info("discount credits notification skipped for user %s: no Redis", user_id)
        return None
    # Both of today's events already handled: nothing to read.
    if all(redis_client.exists(_day_key(e, user_id, today)) for e in EVENTS):
        _quiet(user_id, _seconds_to_utc_midnight())
        return None

    row = get_balance(user_id)
    event = classify(row, today)
    if event is None:
        return None
    # From here today's event is handled one way or another.
    _quiet(user_id, _seconds_to_utc_midnight())
    if not _claim(redis_client, _day_key(event, user_id, today), _DAY_KEY_TTL_SECONDS):
        return None
    if event == EVENT_DEPLETED and not _claim(
        redis_client, _depletion_key(user_id, row), _DEPLETION_KEY_TTL_SECONDS
    ):
        return None

    user = get_user_by_id(user_id) or {}
    email = user.get("email")
    if not _deliverable(email):
        logger.info("discount credits notification for user %s: no deliverable email", user_id)
        return None
    subject, html_body, text = build_email(event, row)
    result = send_email(
        email,
        subject,
        html_body,
        text=text,
        tags=[{"name": "category", "value": f"discount_credits_{event}"}],
    )
    if not result.sent:
        logger.warning(
            "discount credits %s email not sent for user %s: %s", event, user_id, result.error
        )
        return None
    logger.info("discount credits %s email sent for user %s", event, user_id)
    return event


def _seconds_to_utc_midnight() -> float:
    now = datetime.now(UTC)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return max((tomorrow - now).total_seconds(), 1.0)


def _quiet(user_id: int, seconds: float) -> None:
    with _memo_lock:
        if len(_quiet_until) >= _MEMO_MAX:
            now = time.monotonic()
            for uid in [u for u, t in _quiet_until.items() if t <= now]:
                del _quiet_until[uid]
            if len(_quiet_until) >= _MEMO_MAX:
                _quiet_until.clear()
        _quiet_until[user_id] = time.monotonic() + seconds


def _should_check(user_id: int) -> bool:
    """True (and marks the account as being checked) unless this worker
    checked it recently."""
    with _memo_lock:
        if _quiet_until.get(user_id, 0.0) > time.monotonic():
            return False
    _quiet(user_id, _RECHECK_SECONDS)
    return True


def _dispatch(user_id: int) -> None:
    threading.Thread(
        target=process, args=(user_id,), name="discount-credits-notify", daemon=True
    ).start()


def notify_after_draw(user_id: Any, full_share: Decimal, applied: Decimal) -> None:
    """Called by billing after every discount draw. When the draw was short
    of the request's full share, checks in the background whether the account
    just ran out of discount credits or reached its daily cap, and emails it
    once. Returns at once; never raises.

    Each worker checks an account at most once a minute, and not again
    that UTC day once the day's event is handled. The in-app notice does not
    depend on this."""
    try:
        if not notify_enabled():
            return
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            return
        if applied >= full_share:
            return
        if not _should_check(user_id):
            return
        _dispatch(user_id)
    except Exception as e:  # noqa: BLE001 - never breaks billing
        logger.warning("discount credits notification not started: %s", type(e).__name__)
