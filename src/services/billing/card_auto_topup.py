"""Card auto top-up: a saved card, charged off-session when the paid balance
cannot cover a request.

Billing order for a request (vault design, decided 2026-11-27): discount
credits, then the paid balance, then the saved card, then refuse. Before
this module the backend could not charge a card for usage: top-ups were
one-off Stripe Checkout payments that saved nothing.

How it works:

* **Save a card.** ``create_setup_session`` opens Stripe Checkout in setup
  mode for the account's Stripe customer (created on first use and stored
  in ``users.stripe_customer_id``). When the customer finishes, the app calls
  ``confirm_setup_session`` (and the ``checkout.session.completed`` webhook
  does the same, idempotently): the payment method id and a display summary
  (brand, last 4, expiry) are stored on the user.
* **Top up.** When a pre-check finds that the paid balance, after discount
  credits, cannot cover a request, :func:`auto_topup_async` charges the saved
  card off-session for the account's top-up amount (not the request's cost)
  and credits the paid balance, then the pre-check runs again.
* **Bounded.** One attempt per account at a time; at most the daily limit per
  UTC day (``CARD_AUTO_TOPUP_DAILY_LIMIT_CENTS``, the account may choose
  less); a cooldown after a non-card failure; and no charge when the top-up
  amount would not cover the shortfall anyway.
* **Idempotent.** Each attempt has a UUID that is the Stripe idempotency
  key, and its credits are added under the key the existing
  ``payment_intent.succeeded`` handler uses (``pi:<id>``), so the
  synchronous path and the webhook cannot both credit one charge.
* **Failures.** A declined card, or one the bank wants the customer to
  confirm (SCA, ``authentication_required``), pauses auto top-up for the
  account, refuses the request with the usual 402 and sends one email (the
  discount credits notification mechanism, off by default). The account
  resumes by saving a card or switching auto top-up on again. A Stripe
  outage or timeout is not the card's fault: no pause, a cooldown, and the
  webhook settles any charge that did go through.
* **Off by default.** With ``CARD_AUTO_TOPUP_ENABLED`` off, or the account
  outside ``CARD_AUTO_TOPUP_USER_IDS``, nothing here touches the database or
  Stripe, and the pre-checks behave exactly as before.

Never logs card details or Stripe secrets; card numbers never reach this
service (Stripe Checkout collects them).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.config.config import Config

logger = logging.getLogger(__name__)

SOURCE = "auto_topup"
SETUP_PURPOSE = "gatewayz_save_card"

# Outcomes of one attempt (auto_topup_attempts.status).
SUCCEEDED = "succeeded"
REQUIRES_ACTION = "requires_action"
DECLINED = "declined"
FAILED = "failed"
UNKNOWN = "unknown"
# Pause reason when the saved card can no longer be used at all.
CARD_UNUSABLE = "card_unusable"

@dataclass(frozen=True)
class TopupResult:
    status: str  # succeeded | refused | requires_action | declined | failed | unknown
    credited_usd: Decimal = Decimal(0)
    reason: str | None = None


# ==================== Switches and amounts (config only) ====================


def feature_enabled_for(user_id: Any) -> bool:
    """CARD_AUTO_TOPUP_ENABLED and the account list together. Config only --
    never a DB call."""
    if not Config.CARD_AUTO_TOPUP_ENABLED:
        return False
    if Config.CARD_AUTO_TOPUP_ALL_ACCOUNTS:
        return True
    return (
        isinstance(user_id, int)
        and not isinstance(user_id, bool)
        and user_id in Config.CARD_AUTO_TOPUP_USER_IDS
    )


def amount_bounds_cents() -> tuple[int, int]:
    low = max(int(Config.CARD_AUTO_TOPUP_MIN_AMOUNT_CENTS), 50)  # Stripe's minimum
    high = max(int(Config.CARD_AUTO_TOPUP_MAX_AMOUNT_CENTS), low)
    return low, high


def server_daily_limit_cents() -> int:
    return max(int(Config.CARD_AUTO_TOPUP_DAILY_LIMIT_CENTS), 0)


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def effective_amount_cents(user: dict[str, Any]) -> int:
    """The account's top-up amount, or the default, within the allowed range."""
    low, high = amount_bounds_cents()
    chosen = _int_or_none(user.get("auto_topup_amount_cents"))
    amount = chosen if chosen is not None else int(Config.CARD_AUTO_TOPUP_DEFAULT_AMOUNT_CENTS)
    return min(max(amount, low), high)


def effective_daily_limit_cents(user: dict[str, Any]) -> int:
    """The account's daily limit: its own choice, never above the server's."""
    ceiling = server_daily_limit_cents()
    chosen = _int_or_none(user.get("auto_topup_daily_limit_cents"))
    if chosen is None:
        return ceiling
    return min(max(chosen, 0), ceiling)


def credits_for_amount(amount_cents: int) -> Decimal:
    """Paid credits (USD) a top-up of amount_cents adds: the amount less the
    top-up fee, exactly as a manual top-up (StripeService._apply_topup_fee)."""
    from src.services.billing.payments import StripeService

    _, _, granted = StripeService._apply_topup_fee(amount_cents / 100)
    return Decimal(str(granted))


def has_saved_card(user: dict[str, Any] | None) -> bool:
    return (
        isinstance(user, dict)
        and bool(user.get("stripe_customer_id"))
        and bool(user.get("stripe_default_payment_method_id"))
    )


def can_charge(user: dict[str, Any] | None) -> bool:
    """The account could be topped up now, as far as its row says: the
    feature is on for it, it switched auto top-up on, has a saved card and is
    not paused. Reads the dict only."""
    if not isinstance(user, dict):
        return False
    return (
        feature_enabled_for(user.get("id"))
        and user.get("auto_topup_on") is True
        and has_saved_card(user)
        and not user.get("auto_topup_paused_reason")
    )


def needs_topup(check_result: dict[str, Any]) -> bool:
    """A pre-check result that cannot cover the request in full: refused, or
    allowed only by cutting max_tokens."""
    if not check_result.get("allowed"):
        return True
    return check_result.get("capped_max_tokens") is not None


def shortfall_usd(check_result: dict[str, Any]) -> float:
    """How much list-price capacity a refused request lacks for its maximum
    cost; 0 when unknown (allowed with capped max_tokens)."""
    if check_result.get("allowed"):
        return 0.0
    try:
        return max(float(check_result.get("shortfall") or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


# ==================== Stripe helpers ====================


def _stripe():
    import stripe

    return stripe


def _api_key() -> str | None:
    return os.getenv("STRIPE_SECRET_KEY") or None


def _field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError, AttributeError):
        return getattr(obj, key, None)


def _metadata(obj: Any) -> dict[str, Any]:
    meta = _field(obj, "metadata")
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    try:
        return dict(meta)
    except Exception:  # noqa: BLE001
        return {}


def _id_of(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    found = _field(value, "id")
    return found if isinstance(found, str) and found else None


def card_summary(payment_method: Any) -> dict[str, Any] | None:
    card = _field(payment_method, "card")
    if card is None:
        return None
    return {
        "brand": _field(card, "brand"),
        "last4": _field(card, "last4"),
        "exp_month": _field(card, "exp_month"),
        "exp_year": _field(card, "exp_year"),
    }


# ==================== Database ====================


def _client():
    from src.config.supabase_config import get_supabase_client

    return get_supabase_client()


def _rpc_payload(data: Any) -> dict[str, Any] | None:
    from src.db.inference_discount import _rpc_payload as payload

    return payload(data)


def _begin(user_id: int, attempt_id: str, amount_cents: int, limit_cents: int) -> dict | None:
    try:
        result = (
            _client()
            .rpc(
                "auto_topup_begin",
                {
                    "p_user_id": user_id,
                    "p_attempt_id": attempt_id,
                    "p_amount_cents": amount_cents,
                    "p_daily_limit_cents": limit_cents,
                    "p_cooldown_seconds": max(int(Config.CARD_AUTO_TOPUP_COOLDOWN_SECONDS), 0),
                },
            )
            .execute()
        )
        return _rpc_payload(result.data)
    except Exception as e:  # noqa: BLE001 - no attempt -> no charge
        logger.warning("auto_topup_begin failed for user %s: %s", user_id, type(e).__name__)
        return None


def _finish(
    attempt_id: str,
    status: str,
    *,
    payment_intent_id: str | None = None,
    payment_id: int | None = None,
    failure_code: str | None = None,
    pause: bool = False,
) -> dict | None:
    try:
        result = (
            _client()
            .rpc(
                "auto_topup_finish",
                {
                    "p_attempt_id": attempt_id,
                    "p_status": status,
                    "p_payment_intent_id": payment_intent_id,
                    "p_payment_id": payment_id,
                    "p_failure_code": (failure_code or None) and str(failure_code)[:64],
                    "p_pause": pause,
                },
            )
            .execute()
        )
        return _rpc_payload(result.data)
    except Exception as e:  # noqa: BLE001
        logger.error("auto_topup_finish failed for attempt %s: %s", attempt_id, type(e).__name__)
        return None


def _update_user(user_id: int, fields: dict[str, Any]) -> bool:
    from src.db.users import invalidate_user_cache_by_id

    result = _client().table("users").update(fields).eq("id", user_id).execute()
    invalidate_user_cache_by_id(user_id)
    return bool(result.data)


def _fresh_user(user_id: int) -> dict[str, Any] | None:
    from src.db.users import get_user_by_id

    user = get_user_by_id(user_id)
    return user if isinstance(user, dict) else None


def charged_today_cents(user_id: int) -> int:
    """What auto top-up charged (or may have charged) the account today."""
    from datetime import UTC, datetime

    try:
        result = (
            _client()
            .table("auto_topup_attempts")
            .select("amount_cents,status")
            .eq("user_id", user_id)
            .eq("day", datetime.now(UTC).date().isoformat())
            .execute()
        )
        return sum(
            int(r.get("amount_cents") or 0)
            for r in (result.data or [])
            if r.get("status") in ("pending", SUCCEEDED, UNKNOWN)
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("auto top-up usage lookup failed for user %s: %s", user_id, type(e).__name__)
        return 0


# ==================== Saving a card ====================


class CardSetupError(Exception):
    """A card could not be saved. ``code`` is safe to return to the caller."""

    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _deliverable_email(email: Any) -> str | None:
    from src.services.discount_credits.notifications import _deliverable

    return email if _deliverable(email) else None


def ensure_customer(user: dict[str, Any]) -> str:
    """The account's Stripe customer id, creating the customer on first use.
    Stored on the user and always looked up by id afterwards, never by email."""
    existing = user.get("stripe_customer_id")
    if existing:
        return str(existing)
    stripe = _stripe()
    user_id = int(user["id"])
    params: dict[str, Any] = {"metadata": {"user_id": str(user_id)}}
    email = _deliverable_email(user.get("email"))
    if email:
        params["email"] = email
    customer = stripe.Customer.create(
        **params,
        idempotency_key=f"gatewayz-customer-user-{user_id}",
        api_key=_api_key(),
    )
    customer_id = _id_of(customer)
    if not customer_id:
        raise CardSetupError("stripe_unavailable", 502)
    # Only fill an empty column: a concurrent call may have stored one first.
    result = (
        _client()
        .table("users")
        .update({"stripe_customer_id": customer_id})
        .eq("id", user_id)
        .is_("stripe_customer_id", "null")
        .execute()
    )
    from src.db.users import invalidate_user_cache_by_id

    invalidate_user_cache_by_id(user_id)
    if not result.data:
        fresh = _fresh_user(user_id) or {}
        stored = fresh.get("stripe_customer_id")
        if stored:
            return str(stored)
    return customer_id


def _frontend_url() -> str:
    return (os.getenv("FRONTEND_URL") or "https://gatewayz.ai").rstrip("/")


def create_setup_session(user_id: int) -> dict[str, Any]:
    """A Stripe Checkout session in setup mode that saves a card for off-session
    use. Returns {session_id, url}. Return URLs are fixed server-side."""
    if not feature_enabled_for(user_id):
        raise CardSetupError("card_auto_topup_not_enabled", 404)
    user = _fresh_user(user_id)
    if not user:
        raise CardSetupError("account_not_found", 404)
    customer_id = ensure_customer(user)
    base = f"{_frontend_url()}/settings/credits"
    metadata = {"user_id": str(user_id), "purpose": SETUP_PURPOSE}
    session = _stripe().checkout.Session.create(
        mode="setup",
        customer=customer_id,
        payment_method_types=["card"],
        client_reference_id=str(user_id),
        metadata=metadata,
        setup_intent_data={"metadata": metadata},
        success_url=f"{base}?card_setup=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}?card_setup=cancel",
        api_key=_api_key(),
    )
    return {"session_id": _field(session, "id"), "url": _field(session, "url")}


def save_card_from_session(session: Any, expected_user_id: int | None = None) -> dict[str, Any]:
    """Store the card a completed setup session saved. Checks the session is
    ours, for this account and its Stripe customer. Idempotent. Returns the
    card summary. Raises CardSetupError."""
    stripe = _stripe()
    if _field(session, "mode") != "setup":
        raise CardSetupError("not_a_card_setup_session", 404)
    meta = _metadata(session)
    if meta.get("purpose") != SETUP_PURPOSE:
        raise CardSetupError("not_a_card_setup_session", 404)
    try:
        user_id = int(meta.get("user_id"))
    except (TypeError, ValueError):
        raise CardSetupError("not_a_card_setup_session", 404) from None
    if expected_user_id is not None and user_id != expected_user_id:
        # 404, not 403: do not confirm another account's session exists.
        raise CardSetupError("not_a_card_setup_session", 404)
    if not feature_enabled_for(user_id):
        raise CardSetupError("card_auto_topup_not_enabled", 404)
    if _field(session, "status") != "complete":
        raise CardSetupError("card_setup_not_complete", 409)
    user = _fresh_user(user_id)
    if not user:
        raise CardSetupError("account_not_found", 404)
    customer_id = _id_of(_field(session, "customer"))
    if not customer_id or customer_id != user.get("stripe_customer_id"):
        raise CardSetupError("not_a_card_setup_session", 404)

    setup_intent = _field(session, "setup_intent")
    if isinstance(setup_intent, str):
        setup_intent = stripe.SetupIntent.retrieve(setup_intent, api_key=_api_key())
    if _field(setup_intent, "status") != "succeeded":
        raise CardSetupError("card_setup_not_complete", 409)
    pm_id = _id_of(_field(setup_intent, "payment_method"))
    if not pm_id:
        raise CardSetupError("card_setup_not_complete", 409)
    payment_method = stripe.PaymentMethod.retrieve(pm_id, api_key=_api_key())
    pm_customer = _id_of(_field(payment_method, "customer"))
    if pm_customer and pm_customer != customer_id:
        raise CardSetupError("not_a_card_setup_session", 404)
    summary = card_summary(payment_method)
    _update_user(
        user_id,
        {
            "stripe_default_payment_method_id": pm_id,
            "stripe_card_summary": summary,
            # A new card is a fresh start after a decline.
            "auto_topup_paused_reason": None,
            "auto_topup_paused_at": None,
        },
    )
    forget(user_id)
    logger.info("Card saved for auto top-up for user %s", user_id)
    return summary or {}


def confirm_setup_session(user_id: int, session_id: str) -> dict[str, Any]:
    """The app's return from Checkout: verify and store the card."""
    if not feature_enabled_for(user_id):
        raise CardSetupError("card_auto_topup_not_enabled", 404)
    if not isinstance(session_id, str) or not session_id.startswith("cs_"):
        raise CardSetupError("not_a_card_setup_session", 404)
    stripe = _stripe()
    try:
        session = stripe.checkout.Session.retrieve(
            session_id, expand=["setup_intent"], api_key=_api_key()
        )
    except stripe.InvalidRequestError:
        raise CardSetupError("not_a_card_setup_session", 404) from None
    return save_card_from_session(session, expected_user_id=user_id)


def remove_card(user_id: int) -> None:
    """Stop using the saved card: auto top-up off, card cleared. The card is
    also detached from the Stripe customer unless the account has an active
    subscription (which may renew on it)."""
    user = _fresh_user(user_id)
    if not user:
        raise CardSetupError("account_not_found", 404)
    pm_id = user.get("stripe_default_payment_method_id")
    _update_user(
        user_id,
        {
            "stripe_default_payment_method_id": None,
            "stripe_card_summary": None,
            "auto_topup_on": False,
            "auto_topup_paused_reason": None,
            "auto_topup_paused_at": None,
        },
    )
    subscribed = user.get("stripe_subscription_id") and user.get("subscription_status") == "active"
    if pm_id and not subscribed:
        try:
            _stripe().PaymentMethod.detach(pm_id, api_key=_api_key())
        except Exception as e:  # noqa: BLE001 - our side is already cleared
            logger.warning("Detaching saved card failed for user %s: %s", user_id, type(e).__name__)


def set_preferences(
    user_id: int, *, enabled: bool, amount_cents: int | None, daily_limit_cents: int | None
) -> dict[str, Any]:
    """Switch auto top-up on or off and set its amount and daily limit.
    Switching on needs a saved card and clears a pause."""
    if not feature_enabled_for(user_id):
        raise CardSetupError("card_auto_topup_not_enabled", 404)
    user = _fresh_user(user_id)
    if not user:
        raise CardSetupError("account_not_found", 404)
    low, high = amount_bounds_cents()
    fields: dict[str, Any] = {"auto_topup_on": bool(enabled)}
    if amount_cents is not None:
        if not low <= amount_cents <= high:
            raise CardSetupError("invalid_amount", 422)
        fields["auto_topup_amount_cents"] = amount_cents
    if daily_limit_cents is not None:
        if not 0 <= daily_limit_cents <= server_daily_limit_cents():
            raise CardSetupError("invalid_daily_limit", 422)
        fields["auto_topup_daily_limit_cents"] = daily_limit_cents
    if enabled:
        if not has_saved_card(user):
            raise CardSetupError("card_required", 409)
        fields["auto_topup_paused_reason"] = None
        fields["auto_topup_paused_at"] = None
    _update_user(user_id, fields)
    forget(user_id)
    return status_view(user_id)


def status_view(user_id: int) -> dict[str, Any]:
    """What Settings > Credits shows."""
    low, high = amount_bounds_cents()
    enabled = feature_enabled_for(user_id)
    view: dict[str, Any] = {
        "feature_enabled": enabled,
        "card": None,
        "auto_topup": None,
        "limits": {
            "min_amount_cents": low,
            "max_amount_cents": high,
            "max_daily_limit_cents": server_daily_limit_cents(),
            "default_amount_cents": int(Config.CARD_AUTO_TOPUP_DEFAULT_AMOUNT_CENTS),
        },
    }
    if not enabled:
        return view
    user = _fresh_user(user_id) or {}
    if has_saved_card(user):
        summary = user.get("stripe_card_summary") or {}
        view["card"] = {
            "brand": summary.get("brand"),
            "last4": summary.get("last4"),
            "exp_month": summary.get("exp_month"),
            "exp_year": summary.get("exp_year"),
        }
    view["auto_topup"] = {
        "enabled": user.get("auto_topup_on") is True,
        "amount_cents": effective_amount_cents(user),
        "daily_limit_cents": effective_daily_limit_cents(user),
        "paused_reason": user.get("auto_topup_paused_reason"),
        "charged_today_cents": charged_today_cents(user_id),
    }
    return view


# ==================== Charging ====================


def _cancel_quietly(payment_intent_id: str | None) -> None:
    if not payment_intent_id:
        return
    try:
        _stripe().PaymentIntent.cancel(payment_intent_id, api_key=_api_key())
    except Exception as e:  # noqa: BLE001
        logger.info("Cancelling payment intent after a failed top-up: %s", type(e).__name__)


def _credit(
    *, user_id: int, payment: dict[str, Any] | None, payment_intent_id: str, amount_cents: int
) -> Decimal:
    """Add the paid credits for one succeeded charge. Idempotent on the
    payment intent (the same key the payment_intent.succeeded handler uses)."""
    from src.db.payments import update_payment_status
    from src.db.users import add_credits_to_user
    from src.services.billing.payments import StripeService

    amount = amount_cents / 100
    fee_rate, fee, granted = StripeService._apply_topup_fee(amount)
    add_credits_to_user(
        user_id=user_id,
        credits=granted,
        transaction_type="purchase",
        description=f"Auto top-up - ${amount:.2f}" + (f" (-${fee} fee)" if fee else ""),
        payment_id=payment.get("id") if payment else None,
        metadata={
            "source": SOURCE,
            "stripe_payment_intent_id": payment_intent_id,
            "amount_paid": amount,
            "topup_fee_rate": fee_rate,
            "topup_fee": fee,
        },
        request_id=StripeService._grant_idempotency_key(f"pi:{payment_intent_id}"),
    )
    if payment and str(payment.get("status", "")).lower() != "completed":
        update_payment_status(
            payment_id=payment["id"], status="completed", stripe_payment_intent_id=payment_intent_id
        )
    return Decimal(str(granted))


def _notify_failure(user_id: int, attempt_id: str, status: str, amount_cents: int) -> None:
    from src.services.discount_credits.notifications import notify_auto_topup_failed

    notify_auto_topup_failed(user_id, attempt_id, status, amount_cents)


def run_auto_topup(user_id: int, shortfall: float = 0.0) -> TopupResult:
    """Charge the account's saved card once, off-session, for its top-up
    amount, and credit the paid balance. Blocking (Stripe and DB calls).
    Never raises."""
    try:
        return _run(user_id, shortfall)
    except Exception as e:  # noqa: BLE001 - a top-up never breaks a request
        logger.error("auto top-up failed for user %s: %s", user_id, type(e).__name__)
        return TopupResult("failed", reason="error")


def _run(user_id: int, shortfall: float) -> TopupResult:
    if not feature_enabled_for(user_id):
        return TopupResult("refused", reason="not_enabled")
    if not _api_key():
        return TopupResult("refused", reason="stripe_not_configured")
    user = _fresh_user(user_id)
    if not can_charge(user):
        return TopupResult("refused", reason="not_set_up")
    amount_cents = effective_amount_cents(user)
    if shortfall > 0 and Decimal(str(shortfall)) > credits_for_amount(amount_cents):
        # One top-up would not cover this request: charging would not admit it.
        return TopupResult("refused", reason="amount_too_small")

    attempt_id = str(uuid.uuid4())
    begun = _begin(user_id, attempt_id, amount_cents, effective_daily_limit_cents(user))
    if not begun or begun.get("status") != "started":
        reason = (begun or {}).get("reason") or "unavailable"
        logger.info("auto top-up not started for user %s: %s", user_id, reason)
        return TopupResult("refused", reason=reason)
    customer_id = begun.get("customer_id")
    pm_id = begun.get("payment_method_id")

    from src.db.payments import create_payment, update_payment_status

    payment = create_payment(
        user_id=user_id,
        amount=amount_cents / 100,
        currency="usd",
        payment_method="stripe",
        status="pending",
        stripe_customer_id=customer_id,
        metadata={"source": SOURCE, "auto_topup_attempt_id": attempt_id},
    )
    if not payment:
        _finish(attempt_id, FAILED, failure_code="payment_record")
        return TopupResult(FAILED, reason="payment_record")

    stripe = _stripe()
    metadata = {
        "source": SOURCE,
        "user_id": str(user_id),
        "payment_id": str(payment["id"]),
        "auto_topup_attempt_id": attempt_id,
    }
    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="usd",
            customer=customer_id,
            payment_method=pm_id,
            off_session=True,
            confirm=True,
            description="Gatewayz auto top-up",
            metadata=metadata,
            idempotency_key=f"gatewayz-auto-topup-{attempt_id}",
            api_key=_api_key(),
        )
    except stripe.CardError as e:
        error_intent = _field(getattr(e, "error", None), "payment_intent")
        intent_id = _id_of(error_intent)
        code = getattr(e, "code", None) or "card_declined"
        status = REQUIRES_ACTION if code == "authentication_required" else DECLINED
        return _card_failed(user_id, attempt_id, payment, intent_id, status, code, amount_cents)
    except stripe.InvalidRequestError as e:
        # The saved card or customer can no longer be charged (detached,
        # deleted). Nothing was charged.
        update_payment_status(payment_id=payment["id"], status="failed")
        code = getattr(e, "code", None) or "invalid_request"
        finished = _finish(attempt_id, FAILED, failure_code=code)
        _pause_card_unusable(user_id, pm_id)
        if finished and finished.get("changed"):
            _notify_failure(user_id, attempt_id, CARD_UNUSABLE, amount_cents)
        return TopupResult(FAILED, reason=CARD_UNUSABLE)
    except (stripe.APIConnectionError, stripe.APIError) as e:
        # Stripe may or may not have charged. Counted against the daily limit
        # until the webhook says what happened; no pause, a cooldown.
        logger.warning("auto top-up outcome unknown for user %s: %s", user_id, type(e).__name__)
        _finish(attempt_id, UNKNOWN, failure_code=type(e).__name__)
        return TopupResult(UNKNOWN, reason="stripe_unavailable")
    except stripe.StripeError as e:
        # Authentication, permission, rate limit: nothing was charged.
        update_payment_status(payment_id=payment["id"], status="failed")
        _finish(attempt_id, FAILED, failure_code=type(e).__name__)
        return TopupResult(FAILED, reason="stripe_error")

    intent_id = _id_of(intent)
    status = _field(intent, "status")
    if status == "succeeded":
        received = _int_or_none(_field(intent, "amount_received"))
        if received is not None and received != amount_cents:
            logger.error(
                "auto top-up amount mismatch for user %s (attempt %s); not crediting here",
                user_id,
                attempt_id,
            )
            _finish(attempt_id, UNKNOWN, payment_intent_id=intent_id, failure_code="amount")
            return TopupResult(UNKNOWN, reason="amount_mismatch")
        credited = _credit(
            user_id=user_id,
            payment=payment,
            payment_intent_id=intent_id,
            amount_cents=amount_cents,
        )
        _finish(attempt_id, SUCCEEDED, payment_intent_id=intent_id, payment_id=payment["id"])
        logger.info("auto top-up succeeded for user %s (attempt %s)", user_id, attempt_id)
        return TopupResult(SUCCEEDED, credited_usd=credited)
    if status in ("requires_action", "requires_payment_method", "requires_confirmation"):
        code = "authentication_required" if status == "requires_action" else status
        result_status = REQUIRES_ACTION if status == "requires_action" else DECLINED
        return _card_failed(
            user_id, attempt_id, payment, intent_id, result_status, code, amount_cents
        )
    # processing (rare for cards): the webhook settles it.
    update_payment_status(
        payment_id=payment["id"], status="pending", stripe_payment_intent_id=intent_id
    )
    _finish(attempt_id, UNKNOWN, payment_intent_id=intent_id, failure_code=str(status))
    return TopupResult(UNKNOWN, reason="processing")


def _card_failed(
    user_id: int,
    attempt_id: str,
    payment: dict[str, Any],
    intent_id: str | None,
    status: str,
    code: str,
    amount_cents: int,
) -> TopupResult:
    from src.db.payments import update_payment_status

    # Off-session there is no customer to confirm the payment: cancel it, so
    # nothing stays open in Stripe. The customer tops up by hand or saves the
    # card again.
    _cancel_quietly(intent_id)
    update_payment_status(
        payment_id=payment["id"], status="failed", stripe_payment_intent_id=intent_id
    )
    finished = _finish(
        attempt_id,
        status,
        payment_intent_id=intent_id,
        payment_id=payment["id"],
        failure_code=code,
        pause=True,
    )
    logger.info("auto top-up %s for user %s (attempt %s)", status, user_id, attempt_id)
    if finished and finished.get("changed"):
        _notify_failure(user_id, attempt_id, status, amount_cents)
    return TopupResult(status, reason=code)


def _pause_card_unusable(user_id: int, pm_id: str | None) -> None:
    try:
        (
            _client()
            .table("users")
            .update({"auto_topup_paused_reason": CARD_UNUSABLE})
            .eq("id", user_id)
            .eq("stripe_default_payment_method_id", pm_id)
            .execute()
        )
        from src.db.users import invalidate_user_cache_by_id

        invalidate_user_cache_by_id(user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Pausing auto top-up failed for user %s: %s", user_id, type(e).__name__)


# Process-local memo: user_id -> time.monotonic() until which this worker does
# not try the account again after a refusal or failure, so an account at its
# daily limit (or paused, while its cached row is stale) does not cost a
# thread and a database call on every refused request.
_quiet_until: dict[int, float] = {}
_memo_lock = threading.Lock()
_MEMO_MAX = 10_000
_RETRY_SECONDS = 30.0
_FAILED_SECONDS = 300.0


def _seconds_to_utc_midnight() -> float:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return max((tomorrow - now).total_seconds(), 1.0)


def _quiet(user_id: int, seconds: float) -> None:
    with _memo_lock:
        if len(_quiet_until) >= _MEMO_MAX:
            _quiet_until.clear()
        _quiet_until[user_id] = time.monotonic() + seconds


def _is_quiet(user_id: int) -> bool:
    with _memo_lock:
        return _quiet_until.get(user_id, 0.0) > time.monotonic()


def forget(user_id: int) -> None:
    """Drop the memo for an account (its card or settings just changed)."""
    with _memo_lock:
        _quiet_until.pop(user_id, None)


def _remember(user_id: int, result: TopupResult) -> None:
    if result.status == SUCCEEDED:
        return
    if result.reason == "daily_limit":
        _quiet(user_id, _seconds_to_utc_midnight())
    elif result.status in (DECLINED, REQUIRES_ACTION) or result.reason == CARD_UNUSABLE:
        _quiet(user_id, _FAILED_SECONDS)
    elif result.reason == "amount_too_small":
        return  # depends on the request, not the account
    else:
        _quiet(user_id, _RETRY_SECONDS)


async def auto_topup_async(user: dict[str, Any] | None, shortfall: float = 0.0) -> Decimal:
    """For request pre-checks: top up the account's paid balance from its
    saved card and return the USD credited (0 when nothing was credited).

    Returns 0 at once -- no thread hop, no DB or Stripe call -- unless the
    feature is on for the account and its row shows auto top-up switched on
    with a saved card and no pause. Waits at most
    CARD_AUTO_TOPUP_TIMEOUT_SECONDS; the charge still completes after that and
    the webhook credits it. Never raises."""
    if not can_charge(user):
        return Decimal(0)
    user_id = user.get("id")
    if _is_quiet(user_id):
        return Decimal(0)
    try:
        timeout = max(int(Config.CARD_AUTO_TOPUP_TIMEOUT_SECONDS), 1)
        result = await asyncio.wait_for(
            asyncio.to_thread(run_auto_topup, user_id, shortfall), timeout=timeout
        )
    except TimeoutError:
        logger.warning("auto top-up for user %s did not finish in time", user_id)
        _quiet(user_id, _RETRY_SECONDS)
        return Decimal(0)
    except Exception as e:  # noqa: BLE001
        logger.warning("auto top-up for user %s failed: %s", user_id, type(e).__name__)
        _quiet(user_id, _RETRY_SECONDS)
        return Decimal(0)
    _remember(user_id, result)
    return result.credited_usd if result.status == SUCCEEDED else Decimal(0)


# ==================== Webhooks ====================


def is_auto_topup_intent(payment_intent: Any) -> bool:
    return _metadata(payment_intent).get("source") == SOURCE and bool(
        _metadata(payment_intent).get("auto_topup_attempt_id")
    )


def is_card_setup_session(session: Any) -> bool:
    return (
        _field(session, "mode") == "setup" and _metadata(session).get("purpose") == SETUP_PURPOSE
    )


def _attempt(attempt_id: str) -> dict[str, Any] | None:
    result = (
        _client().table("auto_topup_attempts").select("*").eq("id", attempt_id).limit(1).execute()
    )
    return (result.data or [None])[0]


def handle_intent_succeeded(payment_intent: Any) -> None:
    """payment_intent.succeeded for an auto top-up charge: credit it if the
    synchronous path did not (idempotent). Raises on failure so Stripe
    retries the event."""
    from src.db.payments import get_payment

    meta = _metadata(payment_intent)
    attempt_id = str(meta.get("auto_topup_attempt_id"))
    intent_id = _id_of(payment_intent)
    attempt = _attempt(attempt_id)
    if not attempt or not intent_id:
        raise ValueError("auto top-up attempt not found for payment intent")
    user_id = int(attempt["user_id"])
    if str(meta.get("user_id")) != str(user_id):
        raise ValueError("auto top-up payment intent does not match its attempt")
    received = _int_or_none(_field(payment_intent, "amount_received"))
    if received != int(attempt["amount_cents"]):
        raise ValueError("auto top-up amount does not match its attempt")
    payment = None
    payment_id = _int_or_none(meta.get("payment_id"))
    if payment_id is not None:
        payment = get_payment(payment_id)
        if payment and int(payment.get("user_id")) != user_id:
            raise ValueError("auto top-up payment record does not match its attempt")
    _credit(
        user_id=user_id,
        payment=payment,
        payment_intent_id=intent_id,
        amount_cents=int(attempt["amount_cents"]),
    )
    _finish(attempt_id, SUCCEEDED, payment_intent_id=intent_id, payment_id=payment_id)


def handle_intent_failed(payment_intent: Any) -> None:
    """payment_intent.payment_failed for an auto top-up charge. Usually the
    synchronous path already recorded it; when it did not (timeout, crash),
    record the decline, pause and notify once."""
    from src.db.payments import get_payment_by_stripe_intent, update_payment_status

    meta = _metadata(payment_intent)
    attempt_id = str(meta.get("auto_topup_attempt_id"))
    intent_id = _id_of(payment_intent)
    error = _field(payment_intent, "last_payment_error")
    code = _field(error, "code") or "card_declined"
    status = REQUIRES_ACTION if code == "authentication_required" else DECLINED
    finished = _finish(
        attempt_id, status, payment_intent_id=intent_id, failure_code=code, pause=True
    )
    payment = get_payment_by_stripe_intent(intent_id) if intent_id else None
    if payment and str(payment.get("status", "")).lower() == "pending":
        update_payment_status(payment_id=payment["id"], status="failed")
    if finished and finished.get("changed"):
        _cancel_quietly(intent_id)
        _notify_failure(
            int(finished.get("user_id")), attempt_id, status, int(finished.get("amount_cents") or 0)
        )


def handle_setup_session_completed(session: Any) -> None:
    """checkout.session.completed for a card setup session: store the card
    (idempotent; the app's confirm call usually got there first)."""
    try:
        save_card_from_session(session)
    except CardSetupError as e:
        logger.warning("Card setup session not stored: %s", e.code)


def handle_payment_method_detached(payment_method: Any) -> None:
    """payment_method.detached: a saved card removed in Stripe stops being
    used. Matched by payment method id."""
    pm_id = _id_of(payment_method)
    if not pm_id:
        return
    result = (
        _client()
        .table("users")
        .update(
            {
                "stripe_default_payment_method_id": None,
                "stripe_card_summary": None,
                "auto_topup_on": False,
            }
        )
        .eq("stripe_default_payment_method_id", pm_id)
        .execute()
    )
    from src.db.users import invalidate_user_cache_by_id

    for row in result.data or []:
        if row.get("id") is not None:
            invalidate_user_cache_by_id(row["id"])

