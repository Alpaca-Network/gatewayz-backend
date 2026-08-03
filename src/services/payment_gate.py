"""Payment gate for live API key issuance.

The 42K signup number was inflated by credit-farming bots. Two of the three
doors are already shut -- signup grants zero credits
(``TRIAL_CREDITS_AMOUNT = 0.0``) and referrals no longer pay out -- but minting
a live API key still costs an attacker nothing, so the key table remains
farmable and every "accounts" metric derived from it remains unusable in
diligence.

The gate: a **live** key requires a payment signal (a completed top-up, or a
non-zero credit balance from an admin grant / coupon). Evaluation keys are
still free but land in the rate-limited ``test`` environment, which is enough
to try the product and not enough to farm.

This is deliberately a payment check rather than a captcha or an email-domain
heuristic. Payment is the only signal a bot cannot cheaply manufacture, and it
has the useful side effect that every account in the payer metrics is, by
construction, a real payer.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Environments that require a payment signal. "test" stays open so evaluation
# does not need a card.
GATED_ENVIRONMENTS = frozenset({"live", "staging", "production"})

# Minimum lifetime spend, in USD, that counts as a payment signal.
#
# Defaults to the smallest amount Stripe checkout will actually accept
# (CreateCheckoutSessionRequest rejects anything under $5.00). Setting this
# lower would produce a gate that tells the user to top up $1 and a checkout
# that then refuses to take $1 — a dead end at the exact moment they were
# willing to pay.
MIN_TOPUP_USD = float(os.getenv("MIN_TOPUP_USD", "5.0"))


def is_gate_enabled() -> bool:
    """Kill switch. Enabled by default -- the gate is the bot fix."""
    return os.getenv("REQUIRE_PAYMENT_FOR_LIVE_KEYS", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


def _payment_amount_usd(payment: dict) -> float:
    """Normalise a payment row to dollars.

    The payments table carries ``amount_usd`` (dollars) on newer rows and
    Stripe's ``amount`` (cents) on older ones. Reading whichever is present and
    guessing the unit would silently under- or over-count by 100x, so the
    dollar column is preferred and cents are only used as a fallback.
    """
    usd = payment.get("amount_usd")
    if usd is not None:
        try:
            return float(usd)
        except (TypeError, ValueError):
            return 0.0
    cents = payment.get("amount")
    try:
        return float(cents) / 100.0 if cents is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def has_payment_signal(user: dict) -> tuple[bool, str]:
    """Whether ``user`` has demonstrated they are a real, paying account.

    Returns ``(allowed, reason)``. The reason is logged and returned to the
    caller so a legitimate user who hits this knows exactly what to do next --
    a gate that says only "denied" generates support tickets.
    """
    user_id = user.get("id")

    # 1. Any completed payment is the strongest signal.
    try:
        from src.db.payments import get_user_payments

        payments = get_user_payments(user_id) or []
        paid_total = sum(
            _payment_amount_usd(p)
            for p in payments
            if str(p.get("status", "")).lower() in ("succeeded", "completed", "paid")
        )
        if paid_total >= MIN_TOPUP_USD:
            return True, f"lifetime_payments=${paid_total:.2f}"
    except Exception as e:
        # A payments lookup failure must not lock out paying customers. Fall
        # through to the balance check rather than denying on infrastructure
        # trouble.
        logger.warning("Payment lookup failed for user %s: %s", user_id, e)

    # 2. A non-zero credit balance covers admin grants, coupons and partner
    #    trials -- all of which are human-gated already.
    try:
        credits = float(user.get("credits") or 0)
        if credits > 0:
            return True, f"credit_balance=${credits:.2f}"
    except (TypeError, ValueError):
        pass

    return False, "no_payment_signal"


def check_live_key_allowed(user: dict, environment_tag: str) -> tuple[bool, str]:
    """Whether ``user`` may mint a key in ``environment_tag``.

    Returns ``(allowed, reason)``.
    """
    if not is_gate_enabled():
        return True, "gate_disabled"

    if (environment_tag or "").strip().lower() not in GATED_ENVIRONMENTS:
        return True, "ungated_environment"

    allowed, reason = has_payment_signal(user)
    if not allowed:
        logger.info("Blocked live key issuance for user %s (%s)", user.get("id"), reason)
    return allowed, reason


def resolve_key_environment(user: dict, requested: str | None) -> tuple[str, bool]:
    """Pick the environment a key may actually be issued in.

    Used by the signup path, which auto-creates a key rather than going through
    ``POST /user/api-keys``. Guarding only the explicit endpoint left the whole
    gate bypassable: register, receive a live key, farm credits — the exact hole
    the gate exists to close.

    Signup cannot simply 402 the way the explicit endpoint does, because
    refusing to complete registration over a key environment would break the
    sign-up flow entirely. So an ungated key is issued instead and the caller is
    told it was downgraded. The user can still evaluate the API; they top up to
    unlock live.

    Returns ``(environment_tag, was_downgraded)``.
    """
    requested_tag = (requested or "live").strip().lower()

    allowed, _ = check_live_key_allowed(user, requested_tag)
    if allowed:
        return requested_tag, False

    logger.info(
        "Downgrading auto-created key for user %s from '%s' to 'test' (no payment signal)",
        user.get("id"),
        requested_tag,
    )
    return "test", True


def downgrade_notice(requested: str) -> str:
    """Human-readable explanation for a downgraded key."""
    return (
        f"A '{requested}' API key requires credits on the account, so a free "
        f"rate-limited 'test' key was issued instead. Top up at least "
        f"${MIN_TOPUP_USD:.2f} to unlock live keys."
    )


def gate_error_detail(environment_tag: str) -> dict:
    """The 402 body. Tells the user precisely how to unblock themselves."""
    return {
        "error": "payment_required",
        "message": (
            f"A '{environment_tag}' API key requires a payment method on file. "
            f"Add ${MIN_TOPUP_USD:.2f} or more in credits to unlock live keys."
        ),
        "how_to_resolve": [
            f"Top up at least ${MIN_TOPUP_USD:.2f} of credits, then retry.",
            "Or create a 'test' environment key, which is free and rate limited.",
        ],
        "free_alternative_environment": "test",
    }
