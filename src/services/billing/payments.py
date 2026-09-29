#!/usr/bin/env python3
"""
Stripe Service
Handles all Stripe payment operations
"""

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import stripe

from src.db.payments import (
    create_payment,
    get_payment,
    get_payment_by_stripe_intent,
    update_payment_metadata,
    update_payment_status,
)
from src.db.subscription_products import get_tier_from_product_id
from src.db.users import add_credits_to_user, get_user_by_id
from src.db.webhook_events import claim_event, release_event
from src.schemas.payments import (
    CancelSubscriptionRequest,
    CheckoutSessionResponse,
    CreateCheckoutSessionRequest,
    CreatePaymentIntentRequest,
    CreateRefundRequest,
    CreateSubscriptionCheckoutRequest,
    CreditPackage,
    CreditPackagesResponse,
    CurrentSubscriptionResponse,
    DowngradeSubscriptionRequest,
    PaymentIntentResponse,
    PaymentStatus,
    RefundResponse,
    StripeCurrency,
    SubscriptionCheckoutResponse,
    SubscriptionManagementResponse,
    UpgradeSubscriptionRequest,
    WebhookProcessingResult,
)
from src.utils.sentry_context import capture_payment_error

# Import Stripe SDK with alias to avoid conflict with schema module


logger = logging.getLogger(__name__)


class StripeService:
    """Service class for handling Stripe payment operations"""

    def __init__(self):
        """Initialize Stripe with API key from environment"""
        self.api_key = os.getenv("STRIPE_SECRET_KEY")
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        self.publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY")

        if not self.api_key:
            raise ValueError("STRIPE_SECRET_KEY not found in environment variables")

        # Validate webhook secret is configured for security
        if not self.webhook_secret:
            logger.warning(
                "STRIPE_WEBHOOK_SECRET not configured - webhook signature validation will fail"
            )

        # Set Stripe API key
        stripe.api_key = self.api_key

        # Pin the Stripe API version so webhook payload shapes are deterministic
        # from the code side rather than riding on the account default (which
        # Stripe can advance under us). This module's helpers are written for the
        # "Basil" generation (2025-08-27.basil+), where period bounds and the
        # invoice→subscription link moved onto sub-objects. Override via
        # STRIPE_API_VERSION only after confirming the account + code agree.
        self.api_version = os.getenv("STRIPE_API_VERSION", "2025-08-27.basil")
        stripe.api_version = self.api_version

        # Configuration
        self.default_currency = StripeCurrency.USD
        self.min_amount = 50  # $0.50 minimum
        self.max_amount = 99999999  # ~$1M maximum
        self.frontend_url = os.getenv("FRONTEND_URL", "https://gatewayz.ai")

        logger.info("Stripe service initialized")

    @staticmethod
    def _get_session_value(session_obj: Any, field: str):
        """Safely extract a field from a Stripe session object or dict."""
        if isinstance(session_obj, dict):
            return session_obj.get(field)
        return getattr(session_obj, field, None)

    @staticmethod
    def _metadata_to_dict(metadata: Any) -> dict[str, Any]:
        """Convert Stripe metadata object into a plain dictionary."""
        if metadata is None:
            return {}
        if isinstance(metadata, dict):
            return metadata
        to_dict = getattr(metadata, "to_dict", None)
        if callable(to_dict):
            try:
                return to_dict()
            except Exception:
                pass
        to_dict_recursive = getattr(metadata, "to_dict_recursive", None)
        if callable(to_dict_recursive):
            try:
                return to_dict_recursive()
            except Exception:
                pass
        try:
            return dict(metadata)
        except Exception:
            return {}

    # Stable namespace for deriving credit-grant idempotency keys. The
    # credit_transactions.request_id column is UUID-typed with a partial unique
    # index, but Stripe identifiers (evt_/pi_/cs_) are not UUIDs — so we map each
    # source id to a deterministic UUIDv5. The same Stripe id always yields the
    # same key, making a grant idempotent across webhook retries / duplicate
    # deliveries. Never change this namespace value or existing keys shift.
    _GRANT_ID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

    @classmethod
    def _grant_idempotency_key(cls, source: str) -> str:
        """Derive a deterministic UUID idempotency key for a credit grant.

        ``source`` is a namespaced Stripe identifier, e.g. ``"pi:pi_123"`` or
        ``"cs:cs_456"``. Returns a string UUID suitable for the
        credit_transactions.request_id unique index.
        """
        return str(uuid.uuid5(cls._GRANT_ID_NAMESPACE, f"grant:{source}"))

    # ==================== Server-authoritative credit / tier helpers ====================

    # Metadata keys the server owns. Client-supplied metadata may never set or
    # override these (they drive credit grants and tier assignment in webhooks).
    RESERVED_METADATA_KEYS = frozenset(
        {
            "user_id",
            "payment_id",
            "credits_cents",
            "credits",
            "tier",
            "product_id",
            "price_id",
            "allowance_handled_at",
            "allowance_handled_by",
            "allowance_period_start",
            "allowance_hw",
        }
    )

    # Server-side promo config: amount charged (cents) -> credits granted (cents).
    # Any amount NOT listed here is granted 1:1. The client-supplied
    # ``credit_value`` is never trusted; it can only ever select one of these.
    # Mirrors the frontend package tiers (settings/credits) and get_credit_packages().
    CREDIT_PACKAGE_CREDITS_CENTS: dict[int, int] = {
        900: 1000,  # $9   -> $10  (Starter, 10% off)
        4500: 5000,  # $45  -> $50  (Professional pack, 10% off)
        7500: 10000,  # $75  -> $100 (Growth, 25% off)
        17500: 25000,  # $175 -> $250 (Scale, 30% off)
    }

    @classmethod
    def _sanitize_client_metadata(cls, metadata: dict[str, Any] | None) -> dict[str, Any]:
        """Drop server-owned keys from client-supplied metadata."""
        return {k: v for k, v in (metadata or {}).items() if k not in cls.RESERVED_METADATA_KEYS}

    @classmethod
    def _credits_for_charge(cls, amount_cents: int) -> int:
        """Credits (cents) a charge of ``amount_cents`` is entitled to."""
        return cls.CREDIT_PACKAGE_CREDITS_CENTS.get(int(amount_cents), int(amount_cents))

    @classmethod
    def _entitled_credits_from_session(
        cls, amount_total: int | None, amount_subtotal: int | None
    ) -> int | None:
        """Credits (cents) Stripe says were actually paid for; None if unknown.

        A package price matches on either the total or the (pre-tax) subtotal so
        tax-inclusive totals do not shortchange promo packages. Otherwise 1:1
        against the lower of the two figures.
        """
        for figure in (amount_total, amount_subtotal):
            if figure is not None and figure in cls.CREDIT_PACKAGE_CREDITS_CENTS:
                return cls.CREDIT_PACKAGE_CREDITS_CENTS[figure]
        known = [v for v in (amount_total, amount_subtotal) if v is not None]
        return min(known) if known else None

    def _resolve_price_binding(
        self, price_id: str, claimed_product_id: str | None
    ) -> tuple[str, str]:
        """Return (product_id, tier) for a Stripe price, derived server-side.

        The tier is taken from the product the price actually belongs to; a
        client-supplied product_id that disagrees is rejected.
        """
        price = stripe.Price.retrieve(price_id)
        if not self._get_stripe_object_value(price, "active"):
            raise ValueError(f"Price {price_id} is not active")
        product = self._get_stripe_object_value(price, "product")
        product_id = (
            product if isinstance(product, str) else self._get_stripe_object_value(product, "id")
        )
        if not product_id:
            raise ValueError(f"Price {price_id} has no product")
        if claimed_product_id and claimed_product_id != product_id:
            logger.error(
                "Rejected price/product mismatch: price=%s belongs to %s but client sent %s",
                price_id,
                product_id,
                claimed_product_id,
            )
            raise ValueError("product_id does not match the supplied price_id")
        tier = get_tier_from_product_id(product_id)
        if not tier or tier == "basic":
            raise ValueError(f"Price {price_id} does not map to a paid tier")
        return product_id, tier

    @staticmethod
    def _apply_topup_fee(amount_dollars: float) -> tuple[float, float, float]:
        """Compute the credit top-up fee split for a one-time payment.

        Returns ``(fee_rate, topup_fee, credits_granted)``. When
        ``CREDIT_TOPUP_FEE_RATE`` is 0 (the default) ``credits_granted`` equals
        ``amount_dollars`` (no fee). The rate is clamped to [0.0, 0.5] and any
        malformed value falls back to 0.0.

        This is the single source of truth for the top-up fee so that BOTH
        one-time payment paths — checkout sessions (``_handle_checkout_completed``)
        and payment intents (``_handle_payment_succeeded``) — apply it identically.
        """
        try:
            fee_rate = max(0.0, min(0.5, float(os.getenv("CREDIT_TOPUP_FEE_RATE", "0.0"))))
        except (TypeError, ValueError):
            fee_rate = 0.0
        topup_fee = round(amount_dollars * fee_rate, 6)
        credits_granted = round(amount_dollars - topup_fee, 6)
        return fee_rate, topup_fee, credits_granted

    # ==================== Checkout Sessions ====================

    @staticmethod
    def _get_stripe_object_value(obj: Any, attr: str) -> Any:
        """
        Safely extract a field from a Stripe object (dict-like or attribute-based).
        """
        if obj is None:
            return None

        if hasattr(obj, attr):
            return getattr(obj, attr)

        if isinstance(obj, dict):
            return obj.get(attr)

        try:
            return obj[attr]
        except (KeyError, TypeError, IndexError):
            return None

    @staticmethod
    def _get_stripe_field(obj: Any, key: str) -> Any:
        """Read a field from a Stripe object or dict, preferring item access.

        Stripe objects subclass ``dict``, so attribute-based access (as used by
        :meth:`_get_stripe_object_value`) returns the built-in method for keys
        that shadow dict methods — notably ``items`` — instead of the field
        value. This prefers ``__getitem__`` so ``obj["items"]`` returns the
        subscription's items collection, falling back to attribute access for
        plain (non-subscriptable) objects.
        """
        if obj is None:
            return None
        try:
            return obj[key]
        except (KeyError, TypeError, IndexError):
            pass
        return getattr(obj, key, None)

    def _get_subscription_period_end(self, subscription: Any) -> int | None:
        """Return a subscription's current_period_end (unix seconds) or None.

        In Stripe's "Basil" API generation (2025-08-27.basil and later, incl.
        2025-09-30.clover) ``current_period_end`` was removed from the
        Subscription object and now lives on each subscription *item*. This
        reads the legacy top-level field first, then falls back to the items.
        """
        period_end = self._get_stripe_field(subscription, "current_period_end")
        if period_end:
            return period_end
        items = self._get_stripe_field(subscription, "items")
        items_data = self._get_stripe_field(items, "data") if items else None
        if items_data:
            for item in items_data:
                item_end = self._get_stripe_field(item, "current_period_end")
                if item_end:
                    return item_end
        return None

    def _get_subscription_period_start(self, subscription: Any) -> int | None:
        """Return a subscription's current_period_start (unix seconds) or None.

        Basil-safe counterpart to :meth:`_get_subscription_period_end` — the
        period bounds moved from the Subscription to its items in the 2025-08+
        API generation.
        """
        period_start = self._get_stripe_field(subscription, "current_period_start")
        if period_start:
            return period_start
        items = self._get_stripe_field(subscription, "items")
        items_data = self._get_stripe_field(items, "data") if items else None
        if items_data:
            for item in items_data:
                item_start = self._get_stripe_field(item, "current_period_start")
                if item_start:
                    return item_start
        return None

    def _get_invoice_subscription_id(self, invoice: Any) -> str | None:
        """Return the subscription id an invoice belongs to, or None.

        In the "Basil" API generation the top-level ``invoice.subscription``
        field was removed. The subscription now lives under
        ``invoice.parent.subscription_details.subscription`` (and per-line under
        ``lines.data[].parent.subscription_item_details.subscription``). This
        checks the legacy field first, then the Basil locations.
        """

        def _as_id(value: Any) -> str | None:
            if not value:
                return None
            if isinstance(value, str):
                return value
            return self._get_stripe_object_value(value, "id")

        sub_id = _as_id(self._get_stripe_object_value(invoice, "subscription"))
        if sub_id:
            return sub_id

        parent = self._get_stripe_object_value(invoice, "parent")
        if parent:
            details = self._get_stripe_object_value(parent, "subscription_details")
            sub_id = (
                _as_id(self._get_stripe_object_value(details, "subscription")) if details else None
            )
            if sub_id:
                return sub_id

        lines = self._get_stripe_object_value(invoice, "lines")
        lines_data = self._get_stripe_object_value(lines, "data") if lines else None
        if lines_data:
            for line in lines_data:
                line_parent = self._get_stripe_object_value(line, "parent")
                item_details = (
                    self._get_stripe_object_value(line_parent, "subscription_item_details")
                    if line_parent
                    else None
                )
                sub_id = (
                    _as_id(self._get_stripe_object_value(item_details, "subscription"))
                    if item_details
                    else None
                )
                if sub_id:
                    return sub_id
        return None

    @staticmethod
    def _coerce_to_int(value: Any) -> int | None:
        """
        Convert Stripe values (str, Decimal, float) into an int representation.
        Returns None when conversion is not possible.
        """
        if value is None:
            return None

        if isinstance(value, bool):
            return int(value)

        if isinstance(value, int | float):
            return int(round(value))

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return int(round(float(stripped)))
            except (ValueError, TypeError):
                return None

        return None

    def _resolve_tier_from_subscription(
        self, subscription: Any, metadata_tier: str | None
    ) -> tuple[str, str | None]:
        """
        Resolve the subscription tier from metadata or subscription items.

        Args:
            subscription: Stripe subscription object
            metadata_tier: Tier value from subscription metadata (may be None or "basic")

        Returns:
            Tuple of (tier, product_id) where tier is guaranteed to be non-None
        """
        tier = metadata_tier
        product_id = None

        # The tier is derived from the price actually on the subscription. The
        # metadata tier is only a fallback for when the product is not mapped.
        items = self._get_stripe_field(subscription, "items")
        if items:
            items_data = self._get_stripe_object_value(items, "data")
            if items_data and len(items_data) > 0:
                first_item = items_data[0]
                price = self._get_stripe_object_value(first_item, "price")
                if price:
                    item_product_id = self._get_stripe_object_value(price, "product")
                    if item_product_id:
                        # Store product_id for logging even if tier lookup fails
                        product_id = item_product_id
                        looked_up_tier = get_tier_from_product_id(item_product_id)
                        if looked_up_tier and looked_up_tier != "basic":
                            if tier and tier != "basic" and tier != looked_up_tier:
                                logger.error(
                                    "Subscription tier metadata %r disagrees with price product "
                                    "%s (tier %r); using the price-derived tier",
                                    tier,
                                    item_product_id,
                                    looked_up_tier,
                                )
                            tier = looked_up_tier
                        elif not tier or tier == "basic":
                            logger.warning(
                                f"Product {item_product_id} not found in subscription_products table or mapped to 'basic'. "
                                f"Please add this product_id to the subscription_products table with the correct tier."
                            )

        # Final fallback to 'pro' for paid subscriptions if tier couldn't be determined
        if not tier or tier == "basic":
            subscription_status = self._get_stripe_object_value(subscription, "status")
            if subscription_status == "active":
                logger.warning(
                    f"Could not determine tier for subscription {self._get_stripe_object_value(subscription, 'id')} "
                    f"(product_id={product_id}). Defaulting to 'pro' since this is an active subscription. "
                    f"ACTION REQUIRED: Add this product_id to the subscription_products table with the correct tier."
                )
                tier = "pro"
            else:
                tier = "basic"

        return tier, product_id

    def _hydrate_checkout_session_metadata(self, session: Any) -> tuple[Any, dict[str, Any]]:
        """
        Ensure we have metadata for a checkout session by re-fetching it from Stripe when needed.
        """
        metadata = self._metadata_to_dict(self._get_stripe_object_value(session, "metadata"))
        if metadata:
            return session, metadata

        session_id = self._get_stripe_object_value(session, "id")
        if not session_id:
            return session, {}

        refreshed_session: Any | None = None
        try:
            refreshed_session = stripe.checkout.Session.retrieve(session_id)
            refreshed_metadata = self._metadata_to_dict(
                self._get_stripe_object_value(refreshed_session, "metadata")
            )
            if refreshed_metadata:
                logger.info(
                    "Hydrated checkout session metadata from Stripe (session_id=%s)", session_id
                )
                return refreshed_session, refreshed_metadata
        except stripe.StripeError as exc:
            logger.warning(
                "Unable to hydrate checkout session metadata for %s: %s", session_id, exc
            )

        # Final fallback: attempt to read metadata from the underlying PaymentIntent
        session_for_intent = refreshed_session or session
        intent_metadata = self._hydrate_payment_intent_metadata(session_for_intent)
        if intent_metadata:
            return session_for_intent, intent_metadata

        return session_for_intent, {}

    def _hydrate_payment_intent_metadata(self, session: Any) -> dict[str, Any]:
        """
        Fetch metadata from the PaymentIntent when it is not present on the checkout session.
        """
        payment_intent_id = self._get_stripe_object_value(session, "payment_intent")
        return self._hydrate_payment_intent_metadata_from_id(payment_intent_id)

    def _hydrate_payment_intent_metadata_from_id(
        self, payment_intent_id: str | None
    ) -> dict[str, Any]:
        """
        Fetch metadata from the payment intent ID when checkout session metadata is missing.
        """
        if not payment_intent_id:
            return {}

        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            metadata = self._metadata_to_dict(self._get_stripe_object_value(intent, "metadata"))
            if metadata:
                logger.info(
                    "Recovered metadata from payment intent %s for checkout session fallback",
                    payment_intent_id,
                )
            return metadata
        except stripe.StripeError as exc:
            logger.warning(
                "Unable to hydrate payment intent metadata for %s: %s", payment_intent_id, exc
            )
            return {}

    def _lookup_payment_record(self, session: Any) -> dict[str, Any] | None:
        """
        Look up the local payment record using payment_intent or checkout session id.
        """
        payment_intent_id = self._get_stripe_object_value(session, "payment_intent")
        session_id = self._get_stripe_object_value(session, "id")

        lookup_attempts: list[tuple[str, str]] = []
        if payment_intent_id:
            lookup_attempts.append(("payment_intent", payment_intent_id))
        if session_id and session_id != payment_intent_id:
            lookup_attempts.append(("checkout_session", session_id))

        for lookup_type, lookup_value in lookup_attempts:
            payment = get_payment_by_stripe_intent(lookup_value)
            if payment:
                logger.info(
                    "Resolved payment context via Supabase using %s lookup (value=%s)",
                    lookup_type,
                    lookup_value,
                )
                return payment

        return None

    def _create_fallback_payment_record(
        self,
        *,
        user_id: int,
        credits_cents: int,
        session_id: str | None,
        payment_intent_id: str | None,
        currency: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Create a synthetic payment record when the original checkout metadata is missing.
        """
        amount_dollars = float(Decimal(credits_cents) / 100)
        payment_currency = (currency or self.default_currency.value).lower()
        fallback_metadata = {
            "created_via": "stripe_webhook_fallback",
            "stripe_session_id": session_id,
            "stripe_payment_intent_id": payment_intent_id,
            "webhook_metadata_snapshot": metadata or {},
        }

        logger.warning(
            "Creating fallback payment record for checkout session %s (user_id=%s, amount=%s %s)",
            session_id,
            user_id,
            amount_dollars,
            payment_currency,
        )

        payment = create_payment(
            user_id=user_id,
            amount=amount_dollars,
            currency=payment_currency,
            payment_method="stripe",
            status="pending",
            stripe_payment_intent_id=payment_intent_id,
            stripe_session_id=session_id,
            metadata=fallback_metadata,
        )

        if not payment:
            logger.error(
                "Unable to create fallback payment record for session %s (user_id=%s)",
                session_id,
                user_id,
            )

        return payment

    def create_checkout_session(
        self, user_id: int, request: CreateCheckoutSessionRequest
    ) -> CheckoutSessionResponse:
        """Create a Stripe checkout session"""
        try:
            # Get user details
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            # Extract real email if stored email is a Privy DID
            user_email = user.get("email", "")
            if user_email.startswith("did:privy:"):
                logger.warning(f"User {user_id} has Privy DID as email: {user_email}")
                # Try to get email from Privy linked accounts via Supabase
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()
                user_result = (
                    client.table("users").select("privy_user_id").eq("id", user_id).execute()
                )
                if user_result.data and user_result.data[0].get("privy_user_id"):
                    privy_user_id = user_result.data[0]["privy_user_id"]
                    logger.info(f"Found privy_user_id for user {user_id}: {privy_user_id}")
                    # For now, use request.customer_email if available, otherwise generic email
                    if request.customer_email:
                        user_email = request.customer_email
                    else:
                        # If no customer_email in request, we can't get real email without Privy token
                        user_email = None
                        logger.warning(
                            f"No customer_email in request for user {user_id} with Privy DID"
                        )
                else:
                    user_email = None

            # Create payment record
            payment = create_payment(
                user_id=user_id,
                amount=float(Decimal(request.amount) / 100),  # Convert cents to dollars
                currency=request.currency.value,
                payment_method="stripe",
                status="pending",
                metadata={
                    "description": request.description,
                    **self._sanitize_client_metadata(request.metadata),
                },
            )

            if not payment:
                raise Exception("Failed to create payment record")

            # Prepare URLs - ALWAYS use request URLs if provided
            success_url = (
                request.success_url
                if request.success_url
                else f"{self.frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
            )
            cancel_url = (
                request.cancel_url if request.cancel_url else f"{self.frontend_url}/payment/cancel"
            )

            logger.info("=== CHECKOUT SESSION URL DEBUG ===")
            logger.info(f"Frontend URL from env: {self.frontend_url}")
            logger.info(f"Request success_url: {request.success_url}")
            logger.info(f"Request cancel_url: {request.cancel_url}")
            logger.info(f"Final success_url being sent to Stripe: {success_url}")
            logger.info(f"Final cancel_url being sent to Stripe: {cancel_url}")
            logger.info("=== END URL DEBUG ===")

            # Credits are derived SERVER-SIDE from the amount charged. The client's
            # ``credit_value`` is never trusted: promo bonuses only come from
            # CREDIT_PACKAGE_CREDITS_CENTS, everything else is granted 1:1.
            credits_cents = self._credits_for_charge(request.amount)
            credits_display = f"${credits_cents / 100:.0f}"
            if request.credit_value is not None:
                requested_cents = int(Decimal(str(request.credit_value)) * 100)
                if requested_cents != credits_cents:
                    logger.warning(
                        "Ignoring client credit_value=$%s for user %s (amount=$%.2f); "
                        "server-derived credits=$%.2f",
                        request.credit_value,
                        user_id,
                        request.amount / 100,
                        credits_cents / 100,
                    )

            checkout_metadata = {
                **self._sanitize_client_metadata(request.metadata),
                "user_id": str(user_id),
                "payment_id": str(payment["id"]),
                "credits_cents": str(credits_cents),
                "credits": str(credits_cents),  # Keep for backward compatibility
            }

            # Create Stripe checkout session
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": request.currency.value,
                            "unit_amount": request.amount,
                            "product_data": {
                                "name": "Gatewayz Credits",
                                "description": f"{credits_display} credits for your account",
                            },
                        },
                        "quantity": 1,
                    }
                ],
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=request.customer_email or user_email,
                client_reference_id=str(user_id),
                metadata=checkout_metadata,
                payment_intent_data={"metadata": checkout_metadata.copy()},
                expires_at=int((datetime.now(UTC) + timedelta(hours=24)).timestamp()),
            )

            # Update payment with identifiers known at session creation
            payment_update_kwargs: dict[str, Any] = {
                "payment_id": payment["id"],
                "status": "pending",
                "stripe_session_id": session.id,
            }
            session_payment_intent = self._get_stripe_object_value(session, "payment_intent")
            if session_payment_intent:
                payment_update_kwargs["stripe_payment_intent_id"] = session_payment_intent

            update_payment_status(**payment_update_kwargs)

            logger.info(f"Checkout session created: {session.id} for user {user_id}")

            return CheckoutSessionResponse(
                session_id=session.id,
                url=session.url,
                payment_id=payment["id"],
                status=PaymentStatus.PENDING,
                amount=request.amount,
                currency=request.currency.value,
                expires_at=datetime.fromtimestamp(session.expires_at, tz=UTC),
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            capture_payment_error(
                e,
                operation="checkout_session",
                user_id=str(user_id),
                amount=request.amount / 100,
                details={"currency": request.currency.value},
            )
            raise Exception(f"Payment processing error: {str(e)}") from e

        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            capture_payment_error(
                e,
                operation="checkout_session",
                user_id=str(user_id),
                amount=request.amount / 100,
                details={"currency": request.currency.value},
            )
            raise

    def retrieve_checkout_session(self, session_id: str) -> dict[str, Any]:
        """Retrieve checkout session details"""
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return {
                "id": session.id,
                "payment_status": session.payment_status,
                "status": session.status,
                "amount_total": session.amount_total,
                "currency": session.currency,
                "customer_email": session.customer_email,
                "payment_intent": session.payment_intent,
                "metadata": session.metadata,
            }
        except stripe.StripeError as e:
            logger.error(f"Error retrieving checkout session: {e}")
            capture_payment_error(
                e, operation="retrieve_session", details={"session_id": session_id}
            )
            raise Exception(f"Failed to retrieve session: {str(e)}") from e

    # ==================== Payment Intents ====================

    def create_payment_intent(
        self, user_id: int, request: CreatePaymentIntentRequest
    ) -> PaymentIntentResponse:
        """Create a Stripe payment intent"""
        try:
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            payment = create_payment(
                user_id=user_id,
                amount=float(Decimal(request.amount) / 100),
                currency=request.currency.value,
                payment_method="stripe",
                status="pending",
                metadata={"description": request.description, **(request.metadata or {})},
            )

            intent_params = {
                "amount": request.amount,
                "currency": request.currency.value,
                "metadata": {
                    "user_id": str(user_id),
                    "payment_id": str(payment["id"]),
                    "credits": str(request.amount),
                    **(request.metadata or {}),
                },
                "description": request.description,
            }

            if request.automatic_payment_methods:
                intent_params["automatic_payment_methods"] = {"enabled": True}
            else:
                intent_params["payment_method_types"] = [
                    pm.value for pm in request.payment_method_types
                ]

            intent = stripe.PaymentIntent.create(**intent_params)

            update_payment_status(
                payment_id=payment["id"], status="pending", stripe_payment_intent_id=intent.id
            )

            logger.info(f"Payment intent created: {intent.id} for user {user_id}")

            return PaymentIntentResponse(
                payment_intent_id=intent.id,
                client_secret=intent.client_secret,
                payment_id=payment["id"],
                status=PaymentStatus(intent.status),
                amount=intent.amount,
                currency=intent.currency,
                next_action=intent.next_action,
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error creating payment intent: {e}")
            raise Exception(f"Payment processing error: {str(e)}") from e

        except Exception as e:
            logger.error(f"Error creating payment intent: {e}")
            raise

    def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
        """Retrieve payment intent details"""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            return {
                "id": intent.id,
                "status": intent.status,
                "amount": intent.amount,
                "currency": intent.currency,
                "customer": intent.customer,
                "payment_method": intent.payment_method,
                "metadata": intent.metadata,
            }
        except stripe.StripeError as e:
            logger.error(f"Error retrieving payment intent: {e}")
            raise Exception(f"Failed to retrieve payment intent: {str(e)}") from e

    # ==================== Webhooks ====================

    def handle_webhook(self, payload: bytes, signature: str) -> WebhookProcessingResult:
        """Handle Stripe webhook events with secure signature validation and deduplication"""
        # Validate webhook secret is configured
        if not self.webhook_secret:
            logger.error("Webhook secret not configured - rejecting webhook")
            raise ValueError("Webhook secret not configured")
        # Validate signature is provided
        if not signature:
            logger.error("Missing webhook signature")
            raise ValueError("Missing webhook signature")
        try:
            # Use Stripe's built-in signature verification (constant-time comparison)
            event = stripe.Webhook.construct_event(payload, signature, self.webhook_secret)

            logger.info(f"Processing webhook: {event['type']} (ID: {event['id']})")

            # Extract user_id from event metadata if available
            user_id = None
            try:
                event_obj = event["data"]["object"]
                if event_obj.get("metadata"):
                    user_id_str = event_obj["metadata"].get("user_id")
                    if user_id_str:
                        user_id = int(user_id_str)
            except (AttributeError, ValueError, TypeError, KeyError):
                pass

            # Claim the event up front (insert-first idempotency). This closes the
            # TOCTOU window the old check-then-record pair had: a concurrent
            # duplicate delivery loses the INSERT race on the UNIQUE(event_id)
            # constraint and is reported as a duplicate here.
            claim = claim_event(
                event_id=event["id"],
                event_type=event["type"],
                user_id=user_id,
                metadata={"stripe_account": event.get("account")},
            )
            if claim == "duplicate":
                logger.warning(f"Duplicate webhook event detected, skipping: {event['id']}")
                return WebhookProcessingResult(
                    success=True,
                    event_type=event["type"],
                    event_id=event["id"],
                    message=f"Event {event['id']} already processed (duplicate)",
                    processed_at=datetime.now(UTC),
                )
            # claim == "unavailable" → dedup table down; fall through and process
            # anyway (fail open). The credit-granting handlers are idempotent at the
            # ledger level (request_id), so an at-least-once retry cannot double-credit.

            try:
                # One-time payment events
                if event["type"] == "checkout.session.completed":
                    self._handle_checkout_completed(event["data"]["object"])
                elif event["type"] == "payment_intent.succeeded":
                    self._handle_payment_succeeded(event["data"]["object"])
                elif event["type"] == "payment_intent.payment_failed":
                    self._handle_payment_failed(event["data"]["object"])
                elif event["type"] == "charge.refunded":
                    self._handle_charge_refunded(event["data"]["object"])
                elif event["type"] == "charge.dispute.created":
                    self._handle_dispute_created(event["data"]["object"])
                elif event["type"] == "charge.dispute.closed":
                    self._handle_dispute_closed(event["data"]["object"])

                # Subscription events
                elif event["type"] == "customer.subscription.created":
                    self._handle_subscription_created(event["data"]["object"])
                elif event["type"] == "customer.subscription.updated":
                    self._handle_subscription_updated(event["data"]["object"])
                elif event["type"] == "customer.subscription.deleted":
                    self._handle_subscription_deleted(event["data"]["object"])
                elif event["type"] == "invoice.paid":
                    self._handle_invoice_paid(event["data"]["object"])
                elif event["type"] == "invoice.payment_failed":
                    self._handle_invoice_payment_failed(event["data"]["object"])
            except Exception:
                # Handler failed: release our claim so Stripe's automatic retry can
                # re-process this event instead of it being permanently marked done.
                if claim == "claimed":
                    release_event(event["id"])
                raise

            # Success: the claim row stays as the permanent processed marker.
            return WebhookProcessingResult(
                success=True,
                event_type=event["type"],
                event_id=event["id"],
                message=f"Event {event['type']} processed successfully",
                processed_at=datetime.now(UTC),
            )

        except ValueError as e:
            logger.error(f"Invalid webhook signature: {e}")
            raise

        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
            raise

    def reconcile_checkout_session(self, session) -> bool:
        """Grant credits for a paid session if the webhook has not already done so.

        Called from the success-page status endpoint so a slow or dropped
        ``checkout.session.completed`` delivery cannot leave a paying user
        without credits. Delegates to the same handler the webhook uses, so
        there is one credit-granting code path rather than two that can drift.

        Returns True when this call is what actually granted the credits.
        """
        session_id = self._get_stripe_object_value(session, "id")
        _, metadata = self._hydrate_checkout_session_metadata(session)
        metadata = metadata or {}

        payment_id = metadata.get("payment_id")
        if payment_id is None:
            logger.debug("Session %s has no payment_id metadata; nothing to reconcile", session_id)
            return False

        try:
            existing = get_payment(int(payment_id))
        except (TypeError, ValueError):
            logger.warning("Session %s has a non-numeric payment_id metadata", session_id)
            return False

        if existing and str(existing.get("status", "")).lower() == "completed":
            # The webhook already landed. Nothing to do.
            return False

        logger.info(
            "Reconciling checkout session %s from the status endpoint — the webhook "
            "has not granted credits yet",
            session_id,
        )
        self._handle_checkout_completed(session)
        return True

    def _handle_checkout_completed(self, session):
        """Handle completed checkout session"""
        try:
            session, metadata = self._hydrate_checkout_session_metadata(session)
            metadata = metadata or {}

            session_id = self._get_stripe_object_value(session, "id")
            if session_id is None and not metadata:
                raise ValueError(
                    "Checkout session payload is missing metadata and session id; cannot process payment"
                )
            payment_intent_id = self._get_stripe_object_value(session, "payment_intent")

            # Log metadata for debugging
            logger.info(
                f"Checkout completed: session_id={session_id}, metadata_keys={list(metadata.keys())}"
            )
            logger.debug(f"Full metadata: {metadata}")

            # Subscription checkouts are handled entirely by the subscription
            # lifecycle webhooks (customer.subscription.created / invoice.paid),
            # which grant the recurring monthly allowance. Processing them here as a
            # one-time top-up would incorrectly add the subscription's first charge
            # as purchased credits (double grant).
            session_mode = self._get_stripe_object_value(session, "mode")
            if session_mode == "subscription":
                logger.info(
                    f"Checkout session {session_id} is a subscription (mode=subscription); "
                    f"skipping one-time credit grant (handled by subscription webhooks)."
                )
                return

            # Backfill metadata from the related payment intent if session metadata is absent/incomplete
            required_metadata_keys = ("user_id", "payment_id", "credits_cents")
            missing_keys = [key for key in required_metadata_keys if not metadata.get(key)]
            if payment_intent_id and missing_keys:
                logger.info(
                    f"Checkout session {session_id} missing metadata keys: {missing_keys}. "
                    f"Attempting to hydrate from payment intent {payment_intent_id}"
                )
                intent_metadata = self._hydrate_payment_intent_metadata_from_id(payment_intent_id)
                if intent_metadata:
                    logger.info(
                        f"Recovered metadata from payment intent: {list(intent_metadata.keys())}"
                    )
                    for key, value in intent_metadata.items():
                        metadata.setdefault(key, value)

            user_id = self._coerce_to_int(metadata.get("user_id"))
            payment_id = self._coerce_to_int(metadata.get("payment_id"))
            # Try both "credits_cents" and "credits" for backward compatibility
            credits_cents = self._coerce_to_int(metadata.get("credits_cents"))
            if credits_cents is None:
                credits_cents = self._coerce_to_int(metadata.get("credits"))

            if user_id is None:
                client_reference_id = self._get_stripe_object_value(session, "client_reference_id")
                user_id = self._coerce_to_int(client_reference_id)

            payment_record = None
            if user_id is None or payment_id is None or credits_cents is None:
                payment_record = self._lookup_payment_record(session)
                if payment_record:
                    logger.warning(
                        "Checkout session %s missing metadata. Fallback payment context recovered (payment_id=%s).",
                        session_id,
                        payment_record.get("id"),
                    )
                    if payment_id is None:
                        payment_id = payment_record.get("id")
                    if user_id is None:
                        user_id = payment_record.get("user_id")
                    if credits_cents is None:
                        fallback_fields = (
                            payment_record.get("credits_purchased"),
                            payment_record.get("amount_cents"),
                        )
                        for field_value in fallback_fields:
                            credits_cents = self._coerce_to_int(field_value)
                            if credits_cents is not None:
                                break
                        if credits_cents is None:
                            amount_usd = payment_record.get(
                                "amount_usd", payment_record.get("amount")
                            )
                            if amount_usd is not None:
                                try:
                                    credits_cents = int(Decimal(str(amount_usd)) * 100)
                                except (TypeError, ValueError):
                                    credits_cents = None

            if credits_cents is None:
                amount_total = self._coerce_to_int(
                    self._get_stripe_object_value(session, "amount_total")
                )
                amount_subtotal = self._coerce_to_int(
                    self._get_stripe_object_value(session, "amount_subtotal")
                )
                for fallback_amount in (amount_total, amount_subtotal):
                    if fallback_amount is not None:
                        credits_cents = fallback_amount
                        logger.info(
                            "Using checkout session amount fallback for credits (session_id=%s)",
                            session_id,
                        )
                        break

            if (
                payment_id is None
                and payment_record is None
                and user_id is not None
                and credits_cents is not None
            ):
                currency = self._get_stripe_object_value(session, "currency")
                payment_record = self._create_fallback_payment_record(
                    user_id=user_id,
                    credits_cents=credits_cents,
                    session_id=session_id,
                    payment_intent_id=payment_intent_id,
                    currency=currency,
                    metadata=metadata,
                )
                if payment_record:
                    payment_id = payment_record.get("id")

            if user_id is None or payment_id is None or credits_cents is None:
                # Provide detailed diagnostics for missing fields
                missing_fields = []
                if user_id is None:
                    missing_fields.append(
                        f"user_id (metadata.get('user_id')={metadata.get('user_id')})"
                    )
                if payment_id is None:
                    missing_fields.append(
                        f"payment_id (metadata.get('payment_id')={metadata.get('payment_id')})"
                    )
                if credits_cents is None:
                    missing_fields.append(
                        f"credits_cents (credits_cents={metadata.get('credits_cents')}, "
                        f"credits={metadata.get('credits')})"
                    )

                logger.error(
                    f"Checkout session {session_id} missing required metadata fields: {missing_fields}. "
                    f"Metadata keys available: {list(metadata.keys())}. "
                    f"Full metadata: {metadata}"
                )

                raise ValueError(
                    "Checkout session missing required metadata "
                    f"(session_id={session_id}, user_id={user_id}, "
                    f"payment_id={payment_id}, credits_cents={credits_cents})"
                )

            # Idempotency guard: if this payment was already completed (e.g. a
            # Stripe webhook retry, or a duplicate delivery), do NOT add credits
            # again. add_credits_to_user is not idempotent on its own, so this
            # check is what makes the handler safe to re-run.
            existing_payment = get_payment(payment_id) if payment_id is not None else None
            if existing_payment and str(existing_payment.get("status", "")).lower() == "completed":
                logger.info(
                    "Checkout session %s already completed (payment_id=%s); "
                    "skipping duplicate credit grant",
                    session_id,
                    payment_id,
                )
                return

            # ENFORCE amounts: never grant more than Stripe says was paid for,
            # whatever the (client-influenceable) metadata claims.
            amount_total = self._coerce_to_int(
                self._get_stripe_object_value(session, "amount_total")
            )
            amount_subtotal = self._coerce_to_int(
                self._get_stripe_object_value(session, "amount_subtotal")
            )
            paid_cents = min(
                (v for v in (amount_total, amount_subtotal) if v is not None), default=None
            )
            if paid_cents is None:
                logger.warning(
                    "Checkout session %s has no amount_total/subtotal; cannot cap credits",
                    session_id,
                )
                verification_amount = credits_cents
            else:
                verification_amount = paid_cents
            verification_result = self._verify_payment_amount(
                amount_cents=verification_amount,
                session_id=session_id,
                user_id=user_id,
                metadata=metadata,
                claimed_credits_cents=credits_cents,
                entitled_credits_cents=self._entitled_credits_from_session(
                    amount_total, amount_subtotal
                ),
            )
            credits_cents = verification_result.get("allowed_credits_cents", credits_cents)
            amount_dollars = float(Decimal(credits_cents) / 100)  # Convert cents to dollars

            # Credit top-up fee (OpenRouter-style monetization). When
            # CREDIT_TOPUP_FEE_RATE > 0, withhold that fraction of the paid
            # amount as revenue and grant the remainder as usable credits.
            # Default 0.0 → credits_granted == amount_dollars (no change).
            fee_rate, topup_fee, credits_granted = self._apply_topup_fee(amount_dollars)

            # Build transaction metadata including verification audit trail
            transaction_metadata = {
                "stripe_session_id": session_id,
                "stripe_payment_intent_id": payment_intent_id,
                "amount_paid": amount_dollars,
                "topup_fee_rate": fee_rate,
                "topup_fee": topup_fee,
                "amount_verification": {
                    "credits_capped": verification_result.get("credits_capped", False),
                    "verified": verification_result.get("verified", False),
                    "severity": verification_result.get("severity", "unknown"),
                    "matched_package": verification_result.get("matched_package"),
                    "expected_cents": verification_result.get("expected_cents"),
                    "difference_cents": verification_result.get("difference_cents", 0),
                    "difference_percent": verification_result.get("difference_percent", 0.0),
                },
            }

            # Add credits and log transaction. The request_id makes this grant
            # idempotent at the ledger level (keyed on the Stripe session), so even
            # if the payment-status guard above is bypassed by a concurrent
            # duplicate delivery, the credits are granted at most once.
            add_credits_to_user(
                user_id=user_id,
                credits=credits_granted,
                transaction_type="purchase",
                description=(
                    f"Stripe checkout - ${amount_dollars}"
                    + (f" (−${topup_fee} fee)" if topup_fee else "")
                ),
                payment_id=payment_id,
                metadata=transaction_metadata,
                request_id=self._grant_idempotency_key(f"cs:{session_id}"),
            )

            # Update payment
            update_payment_status(
                payment_id=payment_id,
                status="completed",
                stripe_payment_intent_id=payment_intent_id,
                stripe_session_id=session_id,
            )

            logger.info(
                f"Checkout completed: paid ${amount_dollars}, fee ${topup_fee}, "
                f"granted {credits_granted} credits to user {user_id}"
            )

            # Clear trial status for the user when they purchase credits
            # This converts trial users to paid users (pay-per-use, NOT subscription)
            # IMPORTANT: subscription_status should be 'inactive' for credit purchases,
            # NOT 'active'. 'active' subscription_status implies an actual subscription
            # (Pro/Max tier), which would cause tier/subscription mismatch bugs.
            try:
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()

                # First check if user already has an active subscription (Pro/Max)
                # If so, don't change their subscription_status
                user_result = (
                    client.table("users")
                    .select("subscription_status, tier")
                    .eq("id", user_id)
                    .execute()
                )
                current_status = None
                current_tier = None
                if user_result.data and len(user_result.data) > 0:
                    current_status = user_result.data[0].get("subscription_status")
                    current_tier = user_result.data[0].get("tier")

                # Only update subscription_status if user is on trial or has expired trial
                # Users with active subscriptions (Pro/Max) should keep their status
                if current_status in ("trial", "expired") or current_tier == "basic":
                    # Set to 'inactive' - meaning no active subscription but not on trial
                    # This is the correct status for pay-per-use credit purchasers
                    client.table("users").update(
                        {
                            "subscription_status": "inactive",
                            "updated_at": datetime.now(UTC).isoformat(),
                        }
                    ).eq("id", user_id).execute()

                    logger.info(
                        f"User {user_id} subscription_status updated to 'inactive' after credit purchase"
                    )
                else:
                    logger.info(
                        f"User {user_id} already has subscription_status='{current_status}', "
                        f"tier='{current_tier}' - not changing status for credit purchase"
                    )

                # Clear trial status for all user's API keys
                # Only update subscription_status if the user doesn't have an active subscription
                api_key_update_data = {
                    "is_trial": False,
                    "trial_converted": True,
                }
                # Only set subscription_status to 'inactive' for users without active subscriptions
                # Pro/Max users should keep their 'active' status on API keys
                if current_status != "active":
                    api_key_update_data["subscription_status"] = "inactive"

                client.table("api_keys_new").update(api_key_update_data).eq(
                    "user_id", user_id
                ).execute()

                logger.info(f"User {user_id} trial status cleared after credit purchase")

            except Exception as trial_error:
                # Don't fail the payment if trial status update fails
                logger.error(
                    f"Error clearing trial status for user {user_id}: {trial_error}", exc_info=True
                )

            # First top-up bonus (first purchase only)
            try:
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()
                user_result = client.table("users").select("*").eq("id", user_id).execute()

                if user_result.data:
                    user = user_result.data[0]
                    has_made_first_purchase = user.get("has_made_first_purchase", False)

                    # Grant the one-time first top-up bonus: a flat +$5 when the
                    # user's FIRST one-time top-up is $5 or more. This is the only
                    # exception to the "no free credits" policy.
                    if not has_made_first_purchase and amount_dollars >= 5.0:
                        add_credits_to_user(
                            user_id=user_id,
                            credits=5.0,
                            transaction_type="first_topup_bonus",
                            description="First top-up bonus",
                            metadata={
                                "stripe_session_id": session_id,
                                "trigger_amount": amount_dollars,
                            },
                            request_id=self._grant_idempotency_key(f"bonus:{session_id}"),
                        )
                        logger.info(
                            f"First top-up bonus applied! User {user_id} received $5 "
                            f"(first top-up of ${amount_dollars})"
                        )

                    # Mark first purchase regardless of bonus eligibility
                    if not has_made_first_purchase:
                        client.table("users").update({"has_made_first_purchase": True}).eq(
                            "id", user_id
                        ).execute()

            except Exception as bonus_error:
                # Don't fail the payment if the first-topup bonus bookkeeping fails
                logger.error(f"Error processing first-topup bonus: {bonus_error}", exc_info=True)

            # CRITICAL: Invalidate user cache AFTER all user data updates
            # add_credits_to_user already invalidates cache, but subsequent updates
            # (subscription_status, trial status) happen after that, so we need to
            # invalidate again to ensure the cache reflects all changes
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)
            logger.info(f"User {user_id} cache invalidated after checkout completion")

        except Exception as e:
            logger.error(f"Error handling checkout completed: {e}")
            raise

    # ==================== Payment Amount Verification ====================

    # Known credit package prices (amount_cents -> package_name)
    # Mirrors the packages defined in get_credit_packages()
    KNOWN_CREDIT_PACKAGES: dict[int, str] = {
        1000: "starter",  # $10.00 - Starter Pack
        4500: "professional",  # $45.00 - Professional Pack (10% discount on $50 credits)
    }

    # Tolerance thresholds for amount verification
    AMOUNT_TOLERANCE_CENTS = 50  # $0.50 absolute tolerance
    AMOUNT_TOLERANCE_PERCENT = 0.01  # 1% relative tolerance
    AMOUNT_OVER_THRESHOLD_PERCENT = 0.10  # 10% over triggers error-level alert

    def _verify_payment_amount(
        self,
        amount_cents: int,
        session_id: str | None = None,
        user_id: int | None = None,
        metadata: dict | None = None,
        claimed_credits_cents: int | None = None,
        entitled_credits_cents: int | None = None,
    ) -> dict:
        """
        Verify a payment amount against known credit package pricing AND enforce
        that claimed credits never exceed what was actually paid for.

        ``amount_cents`` is the amount Stripe charged. When
        ``claimed_credits_cents`` (from metadata / payment record) is supplied,
        the result carries ``allowed_credits_cents = min(claimed, entitled)``
        where ``entitled`` defaults to the server-side credits for the charge.
        ``credits_capped`` is True (and an error is raised to Sentry) when the
        claim exceeded entitlement. Package-price mismatches remain advisory.

        Args:
            amount_cents: The amount in cents from the Stripe session.
            session_id: Stripe checkout session ID for logging.
            user_id: User ID for logging.
            metadata: Checkout session metadata (may contain package hints).

        Returns:
            Dict with verification result:
                - verified: bool (True if amount matches a known package)
                - severity: str (matched, within_tolerance, under, over, unknown_plan)
                - expected_cents: int | None (expected amount if matched)
                - matched_package: str | None (package name if matched)
                - difference_cents: int (signed difference: actual - expected)
                - difference_percent: float (percentage difference)
                - message: str (human-readable description)
        """
        try:
            from src.services.prometheus_metrics import payment_amount_mismatch
        except ImportError:
            payment_amount_mismatch = None

        result: dict = {
            "verified": False,
            "severity": "unknown_plan",
            "expected_cents": None,
            "matched_package": None,
            "difference_cents": 0,
            "difference_percent": 0.0,
            "message": "",
            "allowed_credits_cents": claimed_credits_cents,
            "credits_capped": False,
        }

        if claimed_credits_cents is not None:
            entitled = (
                entitled_credits_cents
                if entitled_credits_cents is not None
                else self._credits_for_charge(amount_cents)
            )
            if claimed_credits_cents > entitled:
                result["allowed_credits_cents"] = entitled
                result["credits_capped"] = True
                logger.error(
                    "CREDIT INFLATION BLOCKED: claimed %s credit-cents but only %s paid for "
                    "(session=%s, user=%s). Granting the entitled amount.",
                    claimed_credits_cents,
                    entitled,
                    session_id,
                    user_id,
                )
                try:
                    capture_payment_error(
                        RuntimeError("Checkout credits claim exceeds amount paid"),
                        operation="payment_amount_enforcement",
                        user_id=str(user_id) if user_id else None,
                        amount=amount_cents / 100,
                        details={
                            "session_id": session_id,
                            "claimed_credits_cents": claimed_credits_cents,
                            "entitled_credits_cents": entitled,
                        },
                    )
                except Exception:
                    pass
                if payment_amount_mismatch:
                    payment_amount_mismatch.labels(severity="over").inc()

        # Step 1: Try to match against known credit packages
        # Check for exact match first
        if amount_cents in self.KNOWN_CREDIT_PACKAGES:
            package_name = self.KNOWN_CREDIT_PACKAGES[amount_cents]
            result.update(
                {
                    "verified": True,
                    "severity": "matched",
                    "expected_cents": amount_cents,
                    "matched_package": package_name,
                    "difference_cents": 0,
                    "difference_percent": 0.0,
                    "message": f"Exact match for '{package_name}' package (${amount_cents / 100:.2f})",
                }
            )
            logger.info(
                "Payment amount verified: exact match for '%s' package "
                "(amount=$%.2f, session=%s, user=%s)",
                package_name,
                amount_cents / 100,
                session_id,
                user_id,
            )
            if payment_amount_mismatch:
                payment_amount_mismatch.labels(severity="matched").inc()
            return result

        # Step 2: Try fuzzy matching against known packages (handles rounding/currency)
        best_match_package = None
        best_match_expected = None
        smallest_diff = float("inf")

        for expected_cents, package_name in self.KNOWN_CREDIT_PACKAGES.items():
            diff_cents = abs(amount_cents - expected_cents)
            if diff_cents < smallest_diff:
                smallest_diff = diff_cents
                best_match_package = package_name
                best_match_expected = expected_cents

        # Step 3: Also check subscription products from the database
        # This handles Pro/Max subscription one-time purchases or promotions
        try:
            from src.db.subscription_products import get_all_active_products

            active_products = get_all_active_products()
            for product in active_products:
                # subscription_products may store price in different formats
                product_price = product.get("price_cents") or product.get("price_per_month")
                if product_price is not None:
                    try:
                        # price_per_month is typically in dollars, convert to cents
                        if product.get("price_cents"):
                            expected = int(product["price_cents"])
                        else:
                            expected = int(float(product_price) * 100)
                        diff_cents = abs(amount_cents - expected)
                        if diff_cents < smallest_diff:
                            smallest_diff = diff_cents
                            tier = product.get("tier", "unknown")
                            best_match_package = f"subscription:{tier}"
                            best_match_expected = expected
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            logger.debug("Could not check subscription products for amount verification: %s", e)

        # Step 4: Evaluate the best match
        if best_match_expected is not None:
            diff_cents_signed = amount_cents - best_match_expected
            diff_percent = (
                abs(diff_cents_signed) / best_match_expected if best_match_expected > 0 else 0.0
            )
            tolerance_cents = max(
                self.AMOUNT_TOLERANCE_CENTS,
                int(best_match_expected * self.AMOUNT_TOLERANCE_PERCENT),
            )

            result["expected_cents"] = best_match_expected
            result["matched_package"] = best_match_package
            result["difference_cents"] = diff_cents_signed
            result["difference_percent"] = round(diff_percent * 100, 2)

            if abs(diff_cents_signed) <= tolerance_cents:
                # Within tolerance -- effectively a match
                result["verified"] = True
                result["severity"] = "within_tolerance"
                result["message"] = (
                    f"Amount ${amount_cents / 100:.2f} is within tolerance of "
                    f"'{best_match_package}' (expected ${best_match_expected / 100:.2f}, "
                    f"diff: {diff_cents_signed:+d} cents)"
                )
                logger.info(
                    "Payment amount within tolerance: %s (session=%s, user=%s)",
                    result["message"],
                    session_id,
                    user_id,
                )
                if payment_amount_mismatch:
                    payment_amount_mismatch.labels(severity="within_tolerance").inc()

            elif diff_cents_signed < 0:
                # Amount is LESS than expected
                result["verified"] = False
                result["severity"] = "under"
                result["message"] = (
                    f"Amount ${amount_cents / 100:.2f} is UNDER expected price for "
                    f"'{best_match_package}' (expected ${best_match_expected / 100:.2f}, "
                    f"diff: {diff_cents_signed:+d} cents, {result['difference_percent']:.1f}%)"
                )
                logger.warning(
                    "Payment amount UNDER expected: %s (session=%s, user=%s). "
                    "Stripe is source of truth -- credits will be granted for charged amount.",
                    result["message"],
                    session_id,
                    user_id,
                )
                if payment_amount_mismatch:
                    payment_amount_mismatch.labels(severity="under").inc()

            elif diff_percent > self.AMOUNT_OVER_THRESHOLD_PERCENT:
                # Amount is SIGNIFICANTLY MORE than expected (>10% over)
                result["verified"] = False
                result["severity"] = "over"
                result["message"] = (
                    f"Amount ${amount_cents / 100:.2f} is SIGNIFICANTLY OVER expected price for "
                    f"'{best_match_package}' (expected ${best_match_expected / 100:.2f}, "
                    f"diff: +{diff_cents_signed} cents, +{result['difference_percent']:.1f}%). "
                    f"Flagged for review."
                )
                logger.error(
                    "Payment amount SIGNIFICANTLY OVER expected: %s (session=%s, user=%s). "
                    "Processing payment but flagging for manual review.",
                    result["message"],
                    session_id,
                    user_id,
                )
                # Send Sentry alert for significant overpayments
                try:
                    capture_payment_error(
                        RuntimeError(
                            f"Payment amount significantly over expected: {result['message']}"
                        ),
                        operation="payment_amount_verification",
                        user_id=str(user_id) if user_id else None,
                        amount=amount_cents / 100,
                        details={
                            "session_id": session_id,
                            "expected_cents": best_match_expected,
                            "actual_cents": amount_cents,
                            "difference_percent": result["difference_percent"],
                            "matched_package": best_match_package,
                        },
                    )
                except Exception:
                    pass  # Don't fail verification on Sentry errors
                if payment_amount_mismatch:
                    payment_amount_mismatch.labels(severity="over").inc()

            else:
                # Over but within 10% -- slightly over, still acceptable
                result["verified"] = True
                result["severity"] = "within_tolerance"
                result["message"] = (
                    f"Amount ${amount_cents / 100:.2f} is slightly over expected price for "
                    f"'{best_match_package}' (expected ${best_match_expected / 100:.2f}, "
                    f"diff: +{diff_cents_signed} cents, +{result['difference_percent']:.1f}%)"
                )
                logger.info(
                    "Payment amount slightly over expected: %s (session=%s, user=%s)",
                    result["message"],
                    session_id,
                    user_id,
                )
                if payment_amount_mismatch:
                    payment_amount_mismatch.labels(severity="within_tolerance").inc()
        else:
            # No known package found at all
            result["severity"] = "unknown_plan"
            result["message"] = (
                f"Amount ${amount_cents / 100:.2f} does not match any known credit "
                f"package or subscription plan"
            )
            logger.warning(
                "Payment amount does not match ANY known plan: %s (session=%s, user=%s). "
                "This may be a custom amount or a new plan not yet registered.",
                result["message"],
                session_id,
                user_id,
            )
            if payment_amount_mismatch:
                payment_amount_mismatch.labels(severity="unknown_plan").inc()

        return result

    def _handle_payment_succeeded(self, payment_intent):
        """Handle successful payment"""
        try:
            payment = get_payment_by_stripe_intent(payment_intent.id)
            if payment:
                # Idempotency guard: skip duplicate credit grants on webhook
                # retries / duplicate deliveries (add_credits_to_user is not
                # idempotent on its own).
                if str(payment.get("status", "")).lower() == "completed":
                    logger.info(
                        "Payment intent %s already completed (payment_id=%s); "
                        "skipping duplicate credit grant",
                        payment_intent.id,
                        payment["id"],
                    )
                    return
                update_payment_status(payment_id=payment["id"], status="completed")
                # Add credits and log transaction. Apply the same top-up fee as the
                # checkout path so the fee model is consistent across both one-time
                # payment flows (previously the fee was only withheld on checkout
                # sessions, letting payment-intent top-ups skip it).
                amount = float(payment.get("amount_usd", payment.get("amount", 0)) or 0)
                fee_rate, topup_fee, credits_granted = self._apply_topup_fee(amount)
                add_credits_to_user(
                    user_id=payment["user_id"],
                    credits=credits_granted,
                    transaction_type="purchase",
                    description=(
                        f"Stripe payment - ${amount}"
                        + (f" (−${topup_fee} fee)" if topup_fee else "")
                    ),
                    payment_id=payment["id"],
                    metadata={
                        "stripe_payment_intent_id": payment_intent.id,
                        "amount_paid": amount,
                        "topup_fee_rate": fee_rate,
                        "topup_fee": topup_fee,
                    },
                    request_id=self._grant_idempotency_key(f"pi:{payment_intent.id}"),
                )
                logger.info(f"Payment succeeded: {payment_intent.id}")
        except Exception as e:
            logger.error(f"Error handling payment succeeded: {e}")

    def _handle_payment_failed(self, payment_intent):
        """Handle failed payment"""
        try:
            payment = get_payment_by_stripe_intent(payment_intent.id)
            if payment:
                update_payment_status(payment_id=payment["id"], status="failed")
                logger.info(f"Payment failed: {payment_intent.id}")
        except Exception as e:
            logger.error(f"Error handling payment failed: {e}")

    # ==================== Credit Packages ====================

    def get_credit_packages(self) -> CreditPackagesResponse:
        """Get available credit packages"""
        packages = [
            CreditPackage(
                id="starter",
                name="Starter Pack",
                credits=1000,
                amount=1000,
                currency=StripeCurrency.USD,
                description="Perfect for trying out the platform",
                features=["1,000 credits", "~100,000 tokens", "Valid for 30 days"],
            ),
            CreditPackage(
                id="professional",
                name="Professional Pack",
                credits=5000,
                amount=4500,
                currency=StripeCurrency.USD,
                discount_percentage=10.0,
                popular=True,
                description="Best value for regular users",
                features=["5,000 credits", "~500,000 tokens", "10% discount", "Valid for 90 days"],
            ),
        ]

        return CreditPackagesResponse(packages=packages, currency=StripeCurrency.USD)

    # ==================== Refund / dispute clawback ====================

    def _payment_credit_position(self, payment_id: int) -> tuple[float, float]:
        """Return (original grant, cumulative credits already owed back) for a payment.

        Original grant = positive 'purchase' rows that are not dispute reinstatements.
        Owed back = ``credits_owed`` on every reversal row (claimed or applied), net of
        the ``reinstated_owed`` recorded by won-dispute reinstatements.
        """
        from src.config.supabase_config import get_supabase_client

        rows = (
            get_supabase_client()
            .table("credit_transactions")
            .select("amount, transaction_type, metadata")
            .eq("payment_id", payment_id)
            .execute()
        )
        granted = 0.0
        reversed_net = 0.0
        for r in rows.data or []:
            md = r.get("metadata") or {}
            ttype = r.get("transaction_type")
            amount = float(r.get("amount") or 0)
            if ttype == "purchase" and amount > 0:
                if md.get("dispute_reinstatement"):
                    reversed_net -= float(md.get("reinstated_owed") or amount)
                else:
                    granted += amount
            elif ttype == "refund" and md.get("credits_owed") is not None:
                reversed_net += float(md.get("credits_owed") or 0)
        return granted, max(0.0, reversed_net)

    @staticmethod
    def _payment_paid_cents(payment: dict[str, Any]) -> int:
        cents = payment.get("amount_cents")
        if cents is not None:
            return int(cents)
        usd = payment.get("amount_usd", payment.get("amount"))
        return int(round(float(usd or 0) * 100))

    def _reverse_purchase_credits(
        self,
        payment: dict[str, Any],
        amount_cents: int,
        idempotency_source: str,
        reason: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Claw back the credits granted for (part of) a payment.

        Idempotent on ``idempotency_source`` via the ledger request_id. The
        reversal is proportional to ``amount_cents`` / amount paid. Balances are
        never driven negative: whatever cannot be recovered is recorded as
        ``clawback_shortfall`` on the ledger row, the payment is flagged
        ``needs_review`` and Sentry is notified.
        """
        from src.config.supabase_config import get_supabase_client
        from src.db.credit_transactions import (
            TransactionType,
            log_credit_transaction,
        )
        from src.db.users import invalidate_user_cache_by_id

        request_id = self._grant_idempotency_key(idempotency_source)
        client = get_supabase_client()
        payment_id = payment["id"]
        user_id = payment["user_id"]

        def _claim_exists() -> bool:
            res = (
                client.table("credit_transactions")
                .select("request_id")
                .eq("request_id", request_id)
                .execute()
            )
            return bool(res.data)

        granted, already_reversed = self._payment_credit_position(payment_id)
        if granted <= 0:
            logger.warning(
                "No purchase grant found for payment %s (%s); nothing to reverse",
                payment_id,
                idempotency_source,
            )
            update_payment_metadata(
                payment_id, {"needs_review": True, "clawback_note": "no purchase grant found"}
            )
            return {"status": "no_grant"}

        paid_cents = self._payment_paid_cents(payment)
        fraction = 1.0 if paid_cents <= 0 else min(1.0, max(0, amount_cents) / paid_cents)
        # Never reverse more than this payment's remaining (net) grant.
        to_reverse = round(min(granted * fraction, max(0.0, granted - already_reversed)), 6)

        base_meta = {**(extra_metadata or {}), "credits_owed": to_reverse}
        # Claim idempotency FIRST: the unique request_id insert arbitrates concurrent
        # callers (route + webhook); only the winner touches the balance.
        claim = log_credit_transaction(
            user_id=user_id,
            amount=0.0,
            transaction_type=TransactionType.REFUND,
            description=f"Credits reversed: {reason}",
            balance_before=0.0,
            balance_after=0.0,
            payment_id=payment_id,
            metadata={**base_meta, "credits_reversed": 0.0},
            created_by="system:stripe_clawback",
            request_id=request_id,
        )
        if not claim:
            if _claim_exists():
                logger.info("Clawback %s already applied; skipping", idempotency_source)
                return {"status": "duplicate"}
            raise RuntimeError(f"Could not record clawback {idempotency_source}; retry")

        def _release_claim() -> None:
            try:
                client.table("credit_transactions").delete().eq("request_id", request_id).execute()
            except Exception:
                logger.error("Could not release clawback claim %s", request_id, exc_info=True)

        deducted = 0.0
        allowance = 0.0
        purchased_before = 0.0
        try:
            for _ in range(3):  # optimistic-lock retry
                row = (
                    client.table("users")
                    .select("purchased_credits, subscription_allowance")
                    .eq("id", user_id)
                    .execute()
                )
                if not row.data:
                    raise ValueError(f"User {user_id} not found for clawback")
                purchased_before = float(row.data[0].get("purchased_credits") or 0)
                allowance = float(row.data[0].get("subscription_allowance") or 0)
                deducted = min(to_reverse, max(0.0, purchased_before))
                updated = (
                    client.table("users")
                    .update({"purchased_credits": purchased_before - deducted})
                    .eq("id", user_id)
                    .eq("purchased_credits", row.data[0].get("purchased_credits"))
                    .execute()
                )
                if updated.data:
                    break
            else:
                raise RuntimeError(f"Clawback for user {user_id} lost the balance race; retry")
        except Exception:
            # Nothing was deducted: release the claim so a retry can re-apply it.
            _release_claim()
            raise

        shortfall = round(to_reverse - deducted, 6)
        try:
            client.table("credit_transactions").update(
                {
                    "amount": -deducted,
                    "balance_before": allowance + purchased_before,
                    "balance_after": allowance + purchased_before - deducted,
                    "metadata": {
                        **base_meta,
                        "credits_reversed": deducted,
                        "clawback_shortfall": shortfall,
                    },
                }
            ).eq("request_id", request_id).execute()
        except Exception:
            logger.error(
                "Clawback %s applied but ledger finalize failed", request_id, exc_info=True
            )
        invalidate_user_cache_by_id(user_id)

        if shortfall > 0:
            logger.error(
                "CLAWBACK SHORTFALL: user %s payment %s owes %s credits after %s",
                user_id,
                payment_id,
                shortfall,
                reason,
            )
            update_payment_metadata(
                payment_id,
                {"needs_review": True, "clawback_shortfall": shortfall, "clawback_reason": reason},
            )
            try:
                capture_payment_error(
                    RuntimeError("Credit clawback shortfall"),
                    operation="credit_clawback",
                    user_id=str(user_id),
                    amount=shortfall,
                    details={"payment_id": payment_id, "reason": reason},
                )
            except Exception:
                pass
        return {"status": "reversed", "reversed": deducted, "shortfall": shortfall}

    def _payment_for_intent(self, payment_intent_id: str | None) -> dict[str, Any] | None:
        if not payment_intent_id:
            return None
        return get_payment_by_stripe_intent(payment_intent_id)

    def _handle_charge_refunded(self, charge):
        """charge.refunded: reverse credits for each refund on the charge (per refund id)."""
        charge_id = self._get_stripe_object_value(charge, "id")
        payment = self._payment_for_intent(self._get_stripe_object_value(charge, "payment_intent"))
        if not payment:
            logger.warning("charge.refunded for %s: no local payment found", charge_id)
            return
        refunds = self._get_stripe_object_value(charge, "refunds")
        refund_list = self._get_stripe_object_value(refunds, "data") if refunds else None
        if not refund_list:
            refund_list = stripe.Refund.list(charge=charge_id, limit=100).data
        for refund in refund_list:
            if self._get_stripe_object_value(refund, "status") in ("failed", "canceled"):
                continue
            refund_id = self._get_stripe_object_value(refund, "id")
            self._reverse_purchase_credits(
                payment,
                int(self._get_stripe_object_value(refund, "amount") or 0),
                f"refund:{refund_id}",
                f"Stripe refund {refund_id}",
                {"stripe_charge_id": charge_id, "stripe_refund_id": refund_id},
            )

    def _dispute_payment(self, dispute) -> dict[str, Any] | None:
        pi = self._get_stripe_object_value(dispute, "payment_intent")
        if not pi:
            charge_id = self._get_stripe_object_value(dispute, "charge")
            if charge_id:
                pi = self._get_stripe_object_value(
                    stripe.Charge.retrieve(charge_id), "payment_intent"
                )
        return self._payment_for_intent(pi)

    def _handle_dispute_created(self, dispute):
        """charge.dispute.created: claw credits back immediately (funds are withdrawn)."""
        dispute_id = self._get_stripe_object_value(dispute, "id")
        payment = self._dispute_payment(dispute)
        if not payment:
            logger.warning("Dispute %s: no local payment found", dispute_id)
            return
        self._reverse_purchase_credits(
            payment,
            int(self._get_stripe_object_value(dispute, "amount") or 0),
            f"dispute:{dispute_id}",
            f"Stripe dispute {dispute_id}",
            {"stripe_dispute_id": dispute_id},
        )

    def _handle_dispute_closed(self, dispute):
        """charge.dispute.closed: lost -> ensure reversed (idempotent); won -> reinstate."""
        dispute_id = self._get_stripe_object_value(dispute, "id")
        status = self._get_stripe_object_value(dispute, "status")
        payment = self._dispute_payment(dispute)
        if not payment:
            logger.warning("Dispute %s closed: no local payment found", dispute_id)
            return
        if status == "lost":
            self._reverse_purchase_credits(
                payment,
                int(self._get_stripe_object_value(dispute, "amount") or 0),
                f"dispute:{dispute_id}",
                f"Stripe dispute {dispute_id} lost",
                {"stripe_dispute_id": dispute_id},
            )
        elif status == "won":
            from src.db.credit_transactions import get_transaction_by_request_id

            original = get_transaction_by_request_id(
                self._grant_idempotency_key(f"dispute:{dispute_id}")
            )
            restored = abs(float(original.get("amount") or 0)) if original else 0.0
            owed = float(((original or {}).get("metadata") or {}).get("credits_owed") or restored)
            if restored > 0:
                add_credits_to_user(
                    user_id=payment["user_id"],
                    credits=restored,
                    transaction_type="purchase",
                    description=f"Credits reinstated: dispute {dispute_id} won",
                    payment_id=payment["id"],
                    metadata={
                        "stripe_dispute_id": dispute_id,
                        "dispute_reinstatement": True,
                        "reinstated_owed": owed,
                    },
                    request_id=self._grant_idempotency_key(f"dispute-won:{dispute_id}"),
                )

    # ==================== Refunds ====================

    def create_refund(self, request: CreateRefundRequest) -> RefundResponse:
        """Create a refund"""
        try:
            refund = stripe.Refund.create(
                payment_intent=request.payment_intent_id,
                amount=request.amount,
                reason=request.reason,
            )

            # Reverse the credits for this refund (idempotent with the later
            # charge.refunded webhook, which uses the same refund-id key).
            try:
                payment = self._payment_for_intent(request.payment_intent_id)
                if payment:
                    self._reverse_purchase_credits(
                        payment,
                        int(refund.amount or 0),
                        f"refund:{refund.id}",
                        f"Stripe refund {refund.id}",
                        {"stripe_refund_id": refund.id},
                    )
            except Exception as clawback_error:
                logger.error(
                    "Refund %s succeeded but credit reversal failed: %s",
                    refund.id,
                    clawback_error,
                    exc_info=True,
                )
                capture_payment_error(
                    clawback_error,
                    operation="refund_clawback",
                    details={"payment_intent_id": request.payment_intent_id},
                )

            return RefundResponse(
                refund_id=refund.id,
                payment_intent_id=refund.payment_intent,
                amount=refund.amount,
                currency=refund.currency,
                status=refund.status,
                reason=refund.reason,
                created_at=datetime.fromtimestamp(refund.created, tz=UTC),
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error creating refund: {e}")
            capture_payment_error(
                e,
                operation="refund",
                amount=request.amount,
                details={"payment_intent_id": request.payment_intent_id, "reason": request.reason},
            )
            raise Exception(f"Refund failed: {str(e)}") from e

    # ==================== Subscription Checkout ====================

    def create_subscription_checkout(
        self, user_id: int, request: CreateSubscriptionCheckoutRequest
    ) -> SubscriptionCheckoutResponse:
        """
        Create a Stripe checkout session for subscription

        Args:
            user_id: User ID
            request: Subscription checkout request parameters

        Returns:
            SubscriptionCheckoutResponse with session_id and checkout URL
        """
        try:
            # Validate price_id up front. A missing/blank price_id otherwise reaches Stripe
            # and surfaces as an opaque 500 — the frontend "Get Started" button then silently
            # no-ops. Fail fast with a 400 (ValueError) and an actionable message instead.
            if not request.price_id or not str(request.price_id).strip():
                raise ValueError(
                    "price_id is required to start a subscription checkout. "
                    "Ensure the pricing tier is configured with a valid Stripe price ID."
                )

            # Get user details
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            # Extract real email if stored email is a Privy DID
            user_email = user.get("email", "")
            if user_email.startswith("did:privy:"):
                logger.warning(f"User {user_id} has Privy DID as email: {user_email}")
                if request.customer_email:
                    user_email = request.customer_email
                else:
                    user_email = None
                    logger.warning(
                        f"No customer_email in request for user {user_id} with Privy DID"
                    )

            # Get or create Stripe customer
            stripe_customer_id = user.get("stripe_customer_id")

            if not stripe_customer_id:
                # Create new Stripe customer
                logger.info(f"Creating Stripe customer for user {user_id}")
                customer = stripe.Customer.create(
                    email=request.customer_email or user_email,
                    metadata={
                        "user_id": str(user_id),
                        "username": user.get("username", ""),
                    },
                )
                stripe_customer_id = customer.id

                # Save customer ID to database
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()
                client.table("users").update(
                    {
                        "stripe_customer_id": stripe_customer_id,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ).eq("id", user_id).execute()

                logger.info(f"Stripe customer created: {stripe_customer_id} for user {user_id}")

            # Bind tier to the Stripe price actually being purchased. The client's
            # product_id is only a claim: it must match the price's real product,
            # and the tier is derived from that product (server-side mapping).
            product_id, tier = self._resolve_price_binding(request.price_id, request.product_id)

            logger.info(
                f"Creating subscription checkout for user {user_id}, tier: {tier}, price_id: {request.price_id}"
            )

            # Create Stripe Checkout Session for subscription
            session_params = {
                "customer": stripe_customer_id,
                "payment_method_types": ["card"],
                "line_items": [
                    {
                        "price": request.price_id,
                        "quantity": 1,
                    }
                ],
                "mode": request.mode,
                "success_url": request.success_url,
                "cancel_url": request.cancel_url,
                "metadata": {
                    **self._sanitize_client_metadata(request.metadata),
                    "user_id": str(user_id),
                    "product_id": product_id,
                    "price_id": request.price_id,
                    "tier": tier,
                },
            }

            # Add subscription_data for subscription mode
            if request.mode == "subscription":
                session_params["subscription_data"] = {
                    "metadata": {
                        "user_id": str(user_id),
                        "product_id": product_id,
                        "price_id": request.price_id,
                        "tier": tier,
                    }
                }

            session = stripe.checkout.Session.create(**session_params)

            logger.info(f"Subscription checkout session created: {session.id} for user {user_id}")
            logger.info(f"Checkout URL: {session.url}")

            return SubscriptionCheckoutResponse(
                session_id=session.id,
                url=session.url,
                customer_id=stripe_customer_id,
                status=session.status,
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error creating subscription checkout: {e}")
            # Client-fixable Stripe errors (HTTP 4xx) — e.g. an invalid/unknown price_id, or a
            # test/live mode mismatch — should surface as a 400, not a generic 500. The raw
            # Stripe error text is logged above for debugging, but is NOT included in the
            # ValueError message: it flows into HTTPException(400).detail verbatim (the
            # global handler in error_handlers.py only sanitizes 500/404, not 400), which
            # would otherwise leak internal Stripe error details to the client.
            # Genuine Stripe outages (5xx/network) stay as 500 (already sanitized).
            http_status = getattr(e, "http_status", None)
            if http_status and 400 <= http_status < 500:
                raise ValueError(
                    "Your subscription request couldn't be processed. "
                    "Please check your payment details and try again."
                ) from e
            raise Exception(f"Payment processing error: {str(e)}") from e

        except Exception as e:
            logger.error(f"Error creating subscription checkout: {e}")
            raise

    # ==================== Subscription Webhook Handlers ====================

    def _lookup_user_by_stripe_customer(self, customer_id: str) -> int | None:
        """
        Fallback lookup: Find user_id by Stripe customer ID.
        Used when subscription metadata is missing user_id.
        """
        if not customer_id:
            return None

        try:
            from src.config.supabase_config import get_supabase_client

            client = get_supabase_client()
            result = (
                client.table("users").select("id").eq("stripe_customer_id", customer_id).execute()
            )

            if result.data and len(result.data) > 0:
                user_id = result.data[0]["id"]
                logger.info(f"Found user_id={user_id} via stripe_customer_id={customer_id}")
                return user_id

            logger.warning(f"No user found with stripe_customer_id={customer_id}")
            return None

        except Exception as e:
            logger.error(f"Error looking up user by stripe customer {customer_id}: {e}")
            return None

    def _extract_user_id_from_subscription(self, subscription) -> int | None:
        """
        Extract user_id from subscription with multiple fallback strategies.

        Strategy order:
        1. subscription.metadata.user_id (primary)
        2. Lookup by stripe_customer_id (fallback)

        Returns None if user cannot be identified.
        """
        # Strategy 1: Get from metadata
        user_id_str = None
        metadata = self._metadata_to_dict(self._get_stripe_object_value(subscription, "metadata"))

        if metadata:
            user_id_str = metadata.get("user_id")

        if user_id_str:
            try:
                return int(user_id_str)
            except (ValueError, TypeError):
                logger.warning(f"Invalid user_id in subscription metadata: {user_id_str}")

        # Strategy 2: Lookup by Stripe customer ID
        customer_id = self._get_stripe_object_value(subscription, "customer")
        if customer_id:
            user_id = self._lookup_user_by_stripe_customer(customer_id)
            if user_id:
                logger.info(
                    f"Recovered user_id={user_id} from stripe_customer_id={customer_id} "
                    f"(subscription metadata was missing user_id)"
                )
                return user_id

        return None

    def _handle_subscription_created(self, subscription):
        """Handle subscription created event"""
        try:
            # Extract user_id with fallback strategies
            user_id = self._extract_user_id_from_subscription(subscription)

            if user_id is None:
                subscription_id = self._get_stripe_object_value(subscription, "id")
                customer_id = self._get_stripe_object_value(subscription, "customer")
                logger.error(
                    f"Cannot process subscription.created: unable to identify user. "
                    f"subscription_id={subscription_id}, customer_id={customer_id}. "
                    f"ACTION REQUIRED: Manually update user's subscription status."
                )
                raise ValueError(
                    f"Missing user_id in subscription metadata and no fallback found "
                    f"(subscription_id={subscription_id})"
                )

            metadata = self._metadata_to_dict(
                self._get_stripe_object_value(subscription, "metadata")
            )
            metadata_tier = metadata.get("tier") if metadata else None
            product_id = metadata.get("product_id") if metadata else None

            # Resolve tier from metadata or subscription items
            tier, resolved_product_id = self._resolve_tier_from_subscription(
                subscription, metadata_tier
            )
            product_id = product_id or resolved_product_id

            logger.info(f"Subscription created for user {user_id}: {subscription.id}, tier: {tier}")

            # Update user's subscription status and tier
            from src.config.supabase_config import get_supabase_client
            from src.db.plans import get_plan_id_by_tier

            client = get_supabase_client()

            update_data = {
                "subscription_status": "active",
                "tier": tier,
                "stripe_subscription_id": subscription.id,
                "stripe_product_id": product_id,
                "stripe_customer_id": subscription.customer,
                "updated_at": datetime.now(UTC).isoformat(),
            }

            # Add subscription end date if available
            period_end = self._get_subscription_period_end(subscription)
            if period_end:
                update_data["subscription_end_date"] = period_end

            client.table("users").update(update_data).eq("id", user_id).execute()

            # Create/assign user_plans entry for the new tier
            plan_id = get_plan_id_by_tier(tier)
            if plan_id:
                # Deactivate any existing plans
                client.table("user_plans").update({"is_active": False}).eq(
                    "user_id", user_id
                ).execute()

                # Create new plan assignment for the subscription period
                start_date = datetime.now(UTC)
                # Use subscription period end if available, otherwise 1 month
                period_end = self._get_subscription_period_end(subscription)
                if period_end:
                    end_date = datetime.fromtimestamp(period_end, tz=UTC)
                else:
                    end_date = start_date + timedelta(days=30)

                user_plan_data = {
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "started_at": start_date.isoformat(),
                    "expires_at": end_date.isoformat(),
                    "is_active": True,
                }

                result = client.table("user_plans").insert(user_plan_data).execute()
                if result.data:
                    logger.info(
                        f"User {user_id} assigned to plan {plan_id} (tier={tier}) for subscription {subscription.id}"
                    )
                else:
                    logger.error(
                        f"Failed to create user_plans entry for user {user_id}, plan {plan_id}"
                    )
            else:
                logger.warning(
                    f"Could not find plan ID for tier: {tier}, user plan entry not created"
                )

            # Clear trial status for all user's API keys
            client.table("api_keys_new").update(
                {
                    "is_trial": False,
                    "trial_converted": True,
                    "subscription_status": "active",
                    "subscription_plan": tier,
                }
            ).eq("user_id", user_id).execute()

            # Set initial subscription allowance
            from src.db.subscription_products import get_allowance_from_tier
            from src.db.users import reset_subscription_allowance

            allowance = get_allowance_from_tier(tier)
            if allowance > 0:
                if not reset_subscription_allowance(user_id, allowance, tier):
                    # If allowance reset fails, raise an exception to trigger webhook retry
                    # This prevents a user from having active subscription but zero credits
                    raise RuntimeError(
                        f"Failed to set initial allowance for user {user_id} ({tier} tier). "
                        f"Webhook will be retried by Stripe."
                    )
                logger.info(
                    f"Set initial allowance of ${allowance} for user {user_id} ({tier} tier)"
                )

            # CRITICAL: Invalidate user cache so profile API returns fresh data
            # This ensures the credits page and header show updated tier immediately
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)

            logger.info(
                f"User {user_id} subscription activated: tier={tier}, subscription_id={subscription.id}, trial status cleared, cache invalidated"
            )

        except Exception as e:
            logger.error(f"Error handling subscription created: {e}", exc_info=True)
            raise

    def _handle_subscription_updated(self, subscription):
        """Handle subscription updated event"""
        try:
            # Extract user_id with fallback strategies
            user_id = self._extract_user_id_from_subscription(subscription)

            if user_id is None:
                subscription_id = self._get_stripe_object_value(subscription, "id")
                customer_id = self._get_stripe_object_value(subscription, "customer")
                logger.error(
                    f"Cannot process subscription.updated: unable to identify user. "
                    f"subscription_id={subscription_id}, customer_id={customer_id}. "
                    f"ACTION REQUIRED: Manually update user's subscription status."
                )
                raise ValueError(
                    f"Missing user_id in subscription metadata and no fallback found "
                    f"(subscription_id={subscription_id})"
                )

            status = subscription.status  # active, past_due, canceled, etc.
            metadata = self._metadata_to_dict(
                self._get_stripe_object_value(subscription, "metadata")
            )
            metadata_tier = metadata.get("tier") if metadata else None

            # Resolve tier from metadata or subscription items
            tier, _ = self._resolve_tier_from_subscription(subscription, metadata_tier)

            logger.info(
                f"Subscription updated for user {user_id}: {subscription.id}, status: {status}, tier: {tier}"
            )

            # Update user's subscription status
            from src.config.supabase_config import get_supabase_client
            from src.db.plans import get_plan_id_by_tier

            client = get_supabase_client()

            update_data = {
                "subscription_status": status,
                "tier": tier,
                "updated_at": datetime.now(UTC).isoformat(),
            }

            period_end = self._get_subscription_period_end(subscription)
            if period_end:
                update_data["subscription_end_date"] = period_end

            # If subscription is canceled or past_due, potentially downgrade
            if status in ["canceled", "past_due", "unpaid"]:
                update_data["tier"] = "basic"
                logger.warning(
                    f"User {user_id} subscription status changed to {status}, downgrading to basic tier"
                )

            client.table("users").update(update_data).eq("id", user_id).execute()

            # Update user_plans entry when subscription is active
            if status == "active":
                plan_id = get_plan_id_by_tier(tier)
                if plan_id:
                    # Deactivate any existing plans
                    client.table("user_plans").update({"is_active": False}).eq(
                        "user_id", user_id
                    ).execute()

                    # Create new plan assignment for the updated subscription period
                    start_date = datetime.now(UTC)
                    # Use subscription period end if available, otherwise 1 month
                    period_end = self._get_subscription_period_end(subscription)
                    if period_end:
                        end_date = datetime.fromtimestamp(period_end, tz=UTC)
                    else:
                        end_date = start_date + timedelta(days=30)

                    user_plan_data = {
                        "user_id": user_id,
                        "plan_id": plan_id,
                        "started_at": start_date.isoformat(),
                        "expires_at": end_date.isoformat(),
                        "is_active": True,
                    }

                    result = client.table("user_plans").insert(user_plan_data).execute()
                    if result.data:
                        logger.info(
                            f"User {user_id} assigned to plan {plan_id} (tier={tier}) on subscription update"
                        )
                    else:
                        logger.error(
                            f"Failed to create user_plans entry for user {user_id}, plan {plan_id}"
                        )
                else:
                    logger.warning(
                        f"Could not find plan ID for tier: {tier} on subscription update"
                    )

                # Clear trial status for all user's API keys when subscription becomes active
                client.table("api_keys_new").update(
                    {
                        "is_trial": False,
                        "trial_converted": True,
                        "subscription_status": "active",
                        "subscription_plan": tier,
                    }
                ).eq("user_id", user_id).execute()
                logger.info(f"User {user_id} trial status cleared on subscription update to active")

                # NOTE: the allowance is deliberately NOT touched here. Resetting it on
                # every subscription.updated (any update, not just tier changes) let
                # users refill their allowance at will. Renewals reset it via
                # invoice.paid (subscription_cycle); tier changes grant only a
                # once-per-period delta via invoice.paid (subscription_update).

            # CRITICAL: Invalidate user cache so profile API returns fresh data
            # This ensures the credits page and header show updated tier immediately
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)

            logger.info(
                f"User {user_id} subscription updated: status={status}, tier={tier}, cache invalidated"
            )

        except Exception as e:
            logger.error(f"Error handling subscription updated: {e}", exc_info=True)
            raise

    def _handle_subscription_deleted(self, subscription):
        """Handle subscription deleted/canceled event (customer.subscription.deleted).

        This webhook fires when:
        1. A subscription's billing period ends after cancel_at_period_end was set to True
        2. A subscription is deleted via the Stripe dashboard
        3. A subscription is canceled immediately via API (though cancel_subscription()
           already handles forfeiture in that case)

        Credit handling:
        - subscription_allowance: SET TO 0 (forfeited - these are monthly tier credits)
        - purchased_credits: KEPT (these were paid for separately)

        The forfeited amount and retained purchased credits are logged in the
        SUBSCRIPTION_CANCELLATION transaction metadata for audit purposes.
        """
        try:
            # Extract user_id with fallback strategies
            user_id = self._extract_user_id_from_subscription(subscription)

            if user_id is None:
                subscription_id = self._get_stripe_object_value(subscription, "id")
                customer_id = self._get_stripe_object_value(subscription, "customer")
                logger.error(
                    f"Cannot process subscription.deleted: unable to identify user. "
                    f"subscription_id={subscription_id}, customer_id={customer_id}. "
                    f"ACTION REQUIRED: Manually update user's subscription status."
                )
                raise ValueError(
                    f"Missing user_id in subscription metadata and no fallback found "
                    f"(subscription_id={subscription_id})"
                )

            subscription_id = self._get_stripe_object_value(subscription, "id")

            # Determine the effective date from Stripe's subscription object
            # canceled_at is when the user clicked "cancel", NOT when the subscription
            # actually ends. For scheduled cancellations (cancel_at_period_end=True),
            # the subscription remains active until current_period_end.
            # Use the Basil-safe helper: in the 2025-08+ API generation the period
            # bounds moved from the Subscription onto its items, so a direct read of
            # current_period_end returns None and the effective_date would silently
            # fall back to now().
            current_period_end = self._get_subscription_period_end(subscription)
            if isinstance(current_period_end, (int, float)) and current_period_end > 0:
                effective_date = datetime.fromtimestamp(current_period_end, tz=UTC).isoformat()
            else:
                effective_date = datetime.now(UTC).isoformat()

            # Get user's current tier before downgrade for audit logging
            user = get_user_by_id(user_id)
            previous_tier = user.get("tier", "unknown") if user else "unknown"

            logger.info(
                f"Processing subscription.deleted for user {user_id}: "
                f"subscription_id={subscription_id}, previous_tier={previous_tier}, "
                f"effective_date={effective_date}"
            )

            # Forfeit subscription allowance before downgrading
            # Credit policy:
            # - subscription_allowance -> zeroed (forfeited)
            # - purchased_credits -> preserved (user paid for these separately)
            # Use raise_on_error=True to ensure data consistency - if forfeiture fails,
            # Stripe will retry the webhook
            from src.db.users import forfeit_subscription_allowance

            forfeiture_result = forfeit_subscription_allowance(
                user_id,
                raise_on_error=True,
                effective_date=effective_date,
                cancellation_context="period_end_webhook",
            )

            forfeited = forfeiture_result.get("forfeited_allowance", 0)
            retained = forfeiture_result.get("retained_purchased_credits", 0)

            logger.info(
                f"Subscription deletion credit summary for user {user_id}: "
                f"forfeited_allowance=${forfeited:.2f}, "
                f"retained_purchased_credits=${retained:.2f}, "
                f"subscription_id={subscription_id}, "
                f"previous_tier={previous_tier}"
            )

            # Downgrade user to basic tier
            from src.config.supabase_config import get_supabase_client

            client = get_supabase_client()

            client.table("users").update(
                {
                    "subscription_status": "canceled",
                    "tier": "basic",
                    "stripe_subscription_id": None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ).eq("id", user_id).execute()

            # Update API keys to reflect new tier
            client.table("api_keys_new").update(
                {
                    "subscription_status": "canceled",
                    "subscription_plan": "basic",
                }
            ).eq("user_id", user_id).execute()

            # CRITICAL: Invalidate user cache so profile API returns fresh data
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)

            logger.info(
                f"User {user_id} subscription canceled and downgraded to basic tier "
                f"(subscription_id={subscription_id}, "
                f"forfeited_allowance=${forfeited:.2f}, "
                f"retained_purchased_credits=${retained:.2f}, "
                f"cache_invalidated=True)"
            )

        except Exception as e:
            logger.error(f"Error handling subscription deleted: {e}", exc_info=True)
            raise

    def _handle_invoice_paid(self, invoice):
        """
        Handle invoice paid event - add credits for subscription renewal.

        IMPORTANT: This handler distinguishes between:
        1. Renewal invoices (billing_reason='subscription_cycle') - These reset allowance
        2. Proration invoices (billing_reason='subscription_update') - These do NOT reset
           allowance because the upgrade/downgrade endpoint already handled it
        3. Initial subscription invoices (billing_reason='subscription_create') - These reset allowance

        This distinction prevents double-crediting on upgrades/downgrades.
        """
        try:
            # Get subscription from invoice (Basil-safe: subscription moved off the
            # top-level invoice in Stripe's 2025-08+ API generation).
            invoice_subscription_id = self._get_invoice_subscription_id(invoice)
            if not invoice_subscription_id:
                logger.info(f"Invoice {invoice.id} is not for a subscription, skipping")
                return

            # Check billing_reason to determine if this is a proration invoice
            billing_reason = getattr(invoice, "billing_reason", None)
            logger.info(
                f"Processing invoice.paid: invoice_id={invoice.id}, "
                f"billing_reason={billing_reason}, subscription={invoice_subscription_id}"
            )

            # Proration invoices (tier changes) never reset the allowance; they may
            # grant only the delta above what was already granted this period, and
            # only now that the invoice is actually paid.
            if billing_reason == "subscription_update":
                subscription = stripe.Subscription.retrieve(invoice_subscription_id)
                user_id = self._extract_user_id_from_subscription(subscription)
                if user_id is None:
                    raise ValueError(
                        f"Missing user_id for proration invoice {invoice.id} "
                        f"(subscription_id={invoice_subscription_id})"
                    )
                meta = self._metadata_to_dict(
                    self._get_stripe_object_value(subscription, "metadata")
                )
                tier, _ = self._resolve_tier_from_subscription(subscription, meta.get("tier"))
                user = get_user_by_id(user_id) or {}
                from_tier, to_tier = self._resolve_tier_change_from_invoice(
                    invoice, tier, user.get("tier")
                )
                self._apply_tier_change_allowance(
                    user_id, subscription, from_tier, to_tier, invoice_id=invoice.id
                )
                return

            subscription = stripe.Subscription.retrieve(invoice_subscription_id)

            # Extract user_id with fallback strategies
            user_id = self._extract_user_id_from_subscription(subscription)

            if user_id is None:
                subscription_id = self._get_stripe_object_value(subscription, "id")
                customer_id = self._get_stripe_object_value(subscription, "customer")
                logger.error(
                    f"Cannot process invoice.paid: unable to identify user. "
                    f"invoice_id={invoice.id}, subscription_id={subscription_id}, customer_id={customer_id}. "
                    f"ACTION REQUIRED: Manually add subscription credits."
                )
                raise ValueError(
                    f"Missing user_id in subscription metadata and no fallback found "
                    f"(invoice_id={invoice.id})"
                )

            metadata = self._metadata_to_dict(
                self._get_stripe_object_value(subscription, "metadata")
            )
            metadata_tier = metadata.get("tier") if metadata else None

            # Additional safeguard: check if allowance was recently handled by upgrade/downgrade
            # This catches edge cases where billing_reason might not be set correctly
            allowance_handled_at = metadata.get("allowance_handled_at") if metadata else None
            if allowance_handled_at:
                try:
                    handled_time = datetime.fromisoformat(allowance_handled_at)
                    seconds_since_handled = (datetime.now(UTC) - handled_time).total_seconds()
                    if seconds_since_handled < 120:
                        allowance_handled_by = metadata.get("allowance_handled_by", "unknown")
                        logger.info(
                            f"Invoice {invoice.id} for user {user_id}: allowance was handled "
                            f"{seconds_since_handled:.1f}s ago by {allowance_handled_by}. "
                            f"Skipping allowance reset to prevent double-crediting."
                        )
                        return
                except (ValueError, TypeError) as e:
                    logger.warning(
                        f"Could not parse allowance_handled_at '{allowance_handled_at}' "
                        f"for invoice {invoice.id}: {e}. Proceeding with allowance reset."
                    )

            # Resolve tier from metadata or subscription items
            tier, _ = self._resolve_tier_from_subscription(subscription, metadata_tier)

            logger.info(
                f"Processing allowance reset for invoice {invoice.id}, user {user_id}, "
                f"tier={tier}, billing_reason={billing_reason}"
            )

            # Reset subscription allowance (old allowance is forfeited, no carry-over)
            from src.db.subscription_products import get_allowance_from_tier
            from src.db.users import reset_subscription_allowance

            allowance = get_allowance_from_tier(tier)
            if allowance > 0:
                # Reset allowance to full amount (old allowance is forfeited, no carry-over)
                if not reset_subscription_allowance(user_id, allowance, tier):
                    # If allowance reset fails, raise an exception to trigger webhook retry
                    # This prevents a user from paying but not receiving their credits
                    raise RuntimeError(
                        f"Failed to reset allowance for user {user_id} ({tier} tier) "
                        f"on invoice payment. Webhook will be retried by Stripe."
                    )
                logger.info(
                    f"Reset allowance to ${allowance} for user {user_id} ({tier} tier) "
                    f"on invoice payment (billing_reason={billing_reason})"
                )
            else:
                logger.warning(f"No allowance configured for tier: {tier}")

        except Exception as e:
            logger.error(f"Error handling invoice paid: {e}", exc_info=True)
            raise

    def _handle_invoice_payment_failed(self, invoice):
        """Handle invoice payment failed event - mark as past_due and downgrade tier"""
        try:
            invoice_subscription_id = self._get_invoice_subscription_id(invoice)
            if not invoice_subscription_id:
                logger.info(f"Invoice {invoice.id} is not for a subscription, skipping")
                return

            subscription = stripe.Subscription.retrieve(invoice_subscription_id)

            # Extract user_id with fallback strategies
            user_id = self._extract_user_id_from_subscription(subscription)

            if user_id is None:
                subscription_id = self._get_stripe_object_value(subscription, "id")
                customer_id = self._get_stripe_object_value(subscription, "customer")
                logger.error(
                    f"Cannot process invoice.payment_failed: unable to identify user. "
                    f"invoice_id={invoice.id}, subscription_id={subscription_id}, customer_id={customer_id}. "
                    f"ACTION REQUIRED: Manually update user's subscription status."
                )
                raise ValueError(
                    f"Missing user_id in subscription metadata and no fallback found "
                    f"(invoice_id={invoice.id})"
                )

            logger.warning(f"Invoice payment failed for user {user_id}: {invoice.id}")

            # Update user's subscription status to past_due and downgrade to basic tier
            from src.config.supabase_config import get_supabase_client

            client = get_supabase_client()

            # Downgrade to basic tier immediately to prevent unauthorized access
            client.table("users").update(
                {
                    "subscription_status": "past_due",
                    "tier": "basic",  # Downgrade tier on payment failure
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ).eq("id", user_id).execute()

            # Also update API keys to reflect downgrade
            client.table("api_keys_new").update(
                {
                    "subscription_status": "past_due",
                    "subscription_plan": "basic",
                }
            ).eq("user_id", user_id).execute()

            # CRITICAL: Invalidate user cache so profile API returns fresh data
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)

            logger.info(
                f"User {user_id} subscription marked as past_due and downgraded to basic tier due to failed payment, cache invalidated"
            )

        except Exception as e:
            logger.error(f"Error handling invoice payment failed: {e}", exc_info=True)
            raise

    # ==================== Subscription Management ====================

    def get_current_subscription(self, user_id: int) -> CurrentSubscriptionResponse:
        """
        Get the current subscription status for a user.

        Args:
            user_id: User ID

        Returns:
            CurrentSubscriptionResponse with subscription details
        """
        try:
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            stripe_subscription_id = user.get("stripe_subscription_id")
            tier = user.get("tier", "basic")

            if not stripe_subscription_id:
                return CurrentSubscriptionResponse(
                    has_subscription=False,
                    tier=tier,
                )

            # Fetch subscription from Stripe
            try:
                subscription = stripe.Subscription.retrieve(stripe_subscription_id)
            except stripe.StripeError as e:
                logger.warning(f"Could not retrieve subscription {stripe_subscription_id}: {e}")
                return CurrentSubscriptionResponse(
                    has_subscription=False,
                    subscription_id=stripe_subscription_id,
                    tier=tier,
                )

            # Extract price and product from subscription items
            price_id = None
            product_id = None
            items = self._get_stripe_object_value(subscription, "items")
            if items:
                items_data = self._get_stripe_object_value(items, "data")
                if items_data and len(items_data) > 0:
                    first_item = items_data[0]
                    price = self._get_stripe_object_value(first_item, "price")
                    if price:
                        price_id = self._get_stripe_object_value(price, "id")
                        product_id = self._get_stripe_object_value(price, "product")

            return CurrentSubscriptionResponse(
                has_subscription=True,
                subscription_id=subscription.id,
                status=subscription.status,
                tier=tier,
                current_period_start=(
                    datetime.fromtimestamp(_sub_period_start, tz=UTC)
                    if (_sub_period_start := self._get_subscription_period_start(subscription))
                    else None
                ),
                current_period_end=(
                    datetime.fromtimestamp(_sub_period_end, tz=UTC)
                    if (_sub_period_end := self._get_subscription_period_end(subscription))
                    else None
                ),
                cancel_at_period_end=subscription.cancel_at_period_end,
                canceled_at=(
                    datetime.fromtimestamp(subscription.canceled_at, tz=UTC)
                    if subscription.canceled_at
                    else None
                ),
                product_id=product_id,
                price_id=price_id,
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error getting subscription for user {user_id}: {e}")
            raise Exception(f"Failed to get subscription: {str(e)}") from e

        except Exception as e:
            logger.error(f"Error getting subscription for user {user_id}: {e}")
            raise

    # ==================== Tier-change allowance (delta, once per period) ====================

    def _allowance_baseline(self, subscription: Any, from_tier: str) -> tuple[str, float]:
        """(period_start, high_water_mark) of allowance already granted this period.

        The high-water mark is the largest tier allowance granted in the current
        billing period. It lives in the Stripe subscription metadata so it
        survives across requests and resets automatically when the period rolls.
        """
        from src.db.subscription_products import get_allowance_from_tier

        metadata = self._metadata_to_dict(self._get_stripe_object_value(subscription, "metadata"))
        period_start = str(self._get_subscription_period_start(subscription) or "")
        hw_raw = metadata.get("allowance_hw")
        if hw_raw is not None and metadata.get("allowance_period_start") == period_start:
            try:
                return period_start, float(hw_raw)
            except (TypeError, ValueError):
                pass
        # New period (or first change): the user received the current tier's allowance.
        return period_start, float(get_allowance_from_tier(from_tier) or 0.0)

    def _allowance_baseline_metadata(self, subscription: Any, from_tier: str) -> dict[str, str]:
        period_start, hw = self._allowance_baseline(subscription, from_tier)
        return {"allowance_period_start": period_start, "allowance_hw": str(hw)}

    def _proration_settled(self, subscription: Any) -> bool:
        """True when the subscription's latest (proration) invoice is paid/void."""
        invoice = self._get_stripe_object_value(subscription, "latest_invoice")
        if isinstance(invoice, str):
            try:
                invoice = stripe.Invoice.retrieve(invoice)
            except stripe.StripeError as exc:
                logger.warning("Could not retrieve latest invoice %s: %s", invoice, exc)
                return False
        if not invoice:
            return False
        return self._get_stripe_object_value(invoice, "status") in ("paid", "void")

    def _resolve_tier_change_from_invoice(
        self, invoice: Any, sub_tier: str, user_tier: str | None
    ) -> tuple[str, str]:
        """(from_tier, to_tier) of a proration invoice, independent of event order.

        ``customer.subscription.updated`` may already have set ``users.tier`` to the
        new tier, so ``user.tier`` is not a reliable from-tier. The invoice's own
        proration lines are: credit lines (negative amount) belong to the old price,
        charge lines (positive) to the new one.
        """

        def _line_tier(line: Any) -> str | None:
            price = self._get_stripe_object_value(line, "price")
            product = self._get_stripe_object_value(price, "product") if price else None
            if not product:
                pricing = self._get_stripe_object_value(line, "pricing")
                details = (
                    self._get_stripe_object_value(pricing, "price_details") if pricing else None
                )
                product = self._get_stripe_object_value(details, "product") if details else None
            if product and not isinstance(product, str):
                product = self._get_stripe_object_value(product, "id")
            if not product:
                return None
            tier = get_tier_from_product_id(product)
            return tier if tier and tier != "basic" else None

        lines = self._get_stripe_object_value(invoice, "lines")
        lines_data = self._get_stripe_object_value(lines, "data") if lines else None
        old_tier: str | None = None
        new_tier: str | None = None
        for line in lines_data or []:
            amount = self._coerce_to_int(self._get_stripe_object_value(line, "amount")) or 0
            tier = _line_tier(line)
            if not tier:
                continue
            if amount < 0:
                old_tier = old_tier or tier
            elif amount > 0:
                new_tier = new_tier or tier
        to_tier = new_tier or sub_tier
        from_tier = old_tier
        if not from_tier and user_tier and user_tier != to_tier:
            from_tier = user_tier
        if not from_tier:
            paid = self._coerce_to_int(self._get_stripe_object_value(invoice, "amount_paid")) or 0
            # A positive charge with no identifiable old price is an upgrade: never
            # let an unknown baseline turn a paid upgrade into a zero grant.
            from_tier = "basic" if paid > 0 else to_tier
        return from_tier, to_tier

    def _apply_tier_change_allowance(
        self,
        user_id: int,
        subscription: Any,
        from_tier: str,
        to_tier: str,
        invoice_id: str | None = None,
    ) -> None:
        """Adjust subscription_allowance for a settled tier change.

        - Upgrade: grant only ``new_allowance - high_water_mark`` (never the full
          new allowance), so flipping tiers cannot refill the allowance.
        - Downgrade / re-upgrade within the same period: no grant; remaining is
          clipped to the new tier's allowance.
        Idempotent: the high-water mark is persisted before returning.
        """
        from src.db.credit_transactions import TransactionType, log_credit_transaction
        from src.db.subscription_products import get_allowance_from_tier
        from src.db.users import get_user_by_id as get_user_fresh
        from src.db.users import reset_subscription_allowance

        new_allowance = float(get_allowance_from_tier(to_tier) or 0.0)
        if new_allowance <= 0:
            return
        sub_meta = self._metadata_to_dict(self._get_stripe_object_value(subscription, "metadata"))
        if invoice_id and sub_meta.get("allowance_last_invoice") == invoice_id:
            logger.info("Invoice %s already applied for user %s; skipping", invoice_id, user_id)
            return
        period_start, hw = self._allowance_baseline(subscription, from_tier)

        user_fresh = get_user_fresh(user_id) or {}
        remaining = float(user_fresh.get("subscription_allowance") or 0)
        purchased = float(user_fresh.get("purchased_credits") or 0)

        if new_allowance > hw:
            grant = new_allowance - hw
            target = remaining + grant
            new_hw = new_allowance
        else:
            grant = 0.0
            target = min(remaining, new_allowance)
            new_hw = hw

        if abs(target - remaining) > 1e-9:
            if not reset_subscription_allowance(user_id, target, to_tier):
                raise Exception("Failed to update subscription allowance")
            is_upgrade = new_allowance > float(get_allowance_from_tier(from_tier) or 0.0)
            log_credit_transaction(
                user_id=user_id,
                amount=target - remaining,
                transaction_type=(
                    TransactionType.SUBSCRIPTION_UPGRADE
                    if is_upgrade
                    else TransactionType.SUBSCRIPTION_DOWNGRADE
                ),
                description=(
                    f"Subscription {from_tier} -> {to_tier}. Allowance delta ${grant} granted "
                    f"(once per billing period); allowance now ${target}."
                ),
                balance_before=remaining + purchased,
                balance_after=target + purchased,
                metadata={
                    "from_tier": from_tier,
                    "to_tier": to_tier,
                    "old_remaining_allowance": remaining,
                    "proration_method": "delta_once_per_period",
                    "subscription_id": self._get_stripe_object_value(subscription, "id"),
                },
                created_by="system:subscription_tier_change",
            )
        logger.info(
            "Tier change allowance for user %s (%s -> %s): remaining=%s hw=%s grant=%s target=%s",
            user_id,
            from_tier,
            to_tier,
            remaining,
            hw,
            grant,
            target,
        )

        sub_id = self._get_stripe_object_value(subscription, "id")
        if sub_id:
            stripe.Subscription.modify(
                sub_id,
                metadata={
                    "allowance_period_start": period_start,
                    "allowance_hw": str(new_hw),
                    **({"allowance_last_invoice": invoice_id} if invoice_id else {}),
                },
            )

    def _get_stripe_proration_amount(
        self, subscription_id: str, new_price_id: str, subscription_item_id: str
    ) -> float | None:
        """
        Fetch the proration amount from Stripe's upcoming invoice preview.

        This queries Stripe for what the proration charge would be, allowing us
        to verify our internal calculations and log the actual Stripe-side amount.

        Args:
            subscription_id: Stripe subscription ID
            new_price_id: The new price ID being switched to
            subscription_item_id: The subscription item being modified

        Returns:
            Proration amount in dollars, or None if unavailable
        """
        try:
            upcoming = stripe.Invoice.upcoming(
                subscription=subscription_id,
                subscription_items=[
                    {
                        "id": subscription_item_id,
                        "price": new_price_id,
                    }
                ],
                subscription_proration_behavior="create_prorations",
            )

            # Sum proration line items (they have type='invoiceitem' and proration=True)
            proration_total = 0
            for line in upcoming.lines.data:
                if getattr(line, "proration", False):
                    proration_total += line.amount

            # Convert from cents to dollars
            return round(proration_total / 100.0, 2) if proration_total else 0.0

        except stripe.StripeError as e:
            logger.warning(
                f"Could not fetch proration preview for subscription {subscription_id}: {e}. "
                f"Proceeding without Stripe proration verification."
            )
            return None
        except Exception as e:
            logger.warning(
                f"Unexpected error fetching proration preview for subscription {subscription_id}: {e}"
            )
            return None

    def upgrade_subscription(
        self, user_id: int, request: UpgradeSubscriptionRequest
    ) -> SubscriptionManagementResponse:
        """
        Upgrade a user's subscription to a higher tier (e.g., Pro -> Max).
        Uses Stripe's subscription update with proration to charge the difference immediately.

        PRORATION LOGIC:
        - On upgrade, subscription_allowance is SET to the new tier's allowance (not incremented).
        - The user's remaining unused allowance from the old tier is forfeited (replaced).
        - Purchased credits are never touched by tier changes.
        - A metadata flag 'allowance_handled_at' is set on the Stripe subscription to prevent
          webhook handlers from double-resetting the allowance.
        - Stripe handles the monetary proration (charging the price difference); we handle
          the credit allowance separately.

        Args:
            user_id: User ID
            request: Upgrade request with new price/product IDs

        Returns:
            SubscriptionManagementResponse with upgrade details
        """
        try:
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            stripe_subscription_id = user.get("stripe_subscription_id")
            if not stripe_subscription_id:
                raise ValueError("User does not have an active subscription to upgrade")

            # Get current subscription
            subscription = stripe.Subscription.retrieve(stripe_subscription_id)

            if subscription.status != "active":
                raise ValueError(f"Cannot upgrade subscription with status: {subscription.status}")

            # Get the subscription item to update
            items = self._get_stripe_object_value(subscription, "items")
            if not items:
                raise ValueError("Subscription has no items")

            items_data = self._get_stripe_object_value(items, "data")
            if not items_data or len(items_data) == 0:
                raise ValueError("Subscription has no item data")

            subscription_item_id = items_data[0].id

            # Bind the tier to the Stripe price being switched to (server-side):
            # the client's product_id must match the price's real product.
            new_product_id, new_tier = self._resolve_price_binding(
                request.new_price_id, request.new_product_id
            )
            if request.proration_behavior != "always_invoice":
                logger.info(
                    "Ignoring client proration_behavior=%r for user %s; forcing always_invoice",
                    request.proration_behavior,
                    user_id,
                )

            # Get current tier for audit logging
            current_tier = user.get("tier", "basic")

            logger.info(
                f"Upgrading subscription {stripe_subscription_id} for user {user_id} "
                f"from {current_tier} to tier {new_tier} (price_id: {request.new_price_id})"
            )

            # Fetch Stripe proration preview BEFORE modifying subscription
            # This gives us Stripe's calculated proration for audit/verification
            stripe_proration_amount = self._get_stripe_proration_amount(
                subscription_id=stripe_subscription_id,
                new_price_id=request.new_price_id,
                subscription_item_id=subscription_item_id,
            )

            if stripe_proration_amount is not None:
                logger.info(
                    f"Stripe proration preview for user {user_id} upgrade "
                    f"{current_tier} -> {new_tier}: ${stripe_proration_amount}"
                )

            # Generate a timestamp to mark when this endpoint handled the allowance.
            # Webhook handlers will check this to avoid double-resetting.
            allowance_handled_at = datetime.now(UTC).isoformat()

            # Update the subscription with the new price
            # proration_behavior='create_prorations' will charge the difference immediately
            updated_subscription = stripe.Subscription.modify(
                stripe_subscription_id,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": request.new_price_id,
                    }
                ],
                # Always invoice the proration immediately and fail the change if it
                # cannot be paid: no free tier changes via proration_behavior='none'.
                proration_behavior="always_invoice",
                payment_behavior="error_if_incomplete",
                expand=["latest_invoice"],
                metadata={
                    "user_id": str(user_id),
                    "product_id": new_product_id,
                    "price_id": request.new_price_id,
                    "tier": new_tier,
                    **self._allowance_baseline_metadata(subscription, current_tier),
                    # Signal to webhook handlers that allowance was already reset by this endpoint.
                    # The webhook handler checks this timestamp and skips allowance reset if it
                    # was set within the last 120 seconds.
                    "allowance_handled_at": allowance_handled_at,
                    "allowance_handled_by": "upgrade_subscription",
                },
            )

            # Update user's tier in database
            from src.config.supabase_config import get_supabase_client
            from src.db.plans import get_plan_id_by_tier

            client = get_supabase_client()

            client.table("users").update(
                {
                    "tier": new_tier,
                    "stripe_product_id": new_product_id,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ).eq("id", user_id).execute()

            # Update user_plans entry
            plan_id = get_plan_id_by_tier(new_tier)
            if plan_id:
                # Deactivate existing plans
                client.table("user_plans").update({"is_active": False}).eq(
                    "user_id", user_id
                ).execute()

                # Create new plan assignment
                start_date = datetime.now(UTC)
                _upd_period_end = self._get_subscription_period_end(updated_subscription)
                if _upd_period_end:
                    end_date = datetime.fromtimestamp(_upd_period_end, tz=UTC)
                else:
                    end_date = start_date + timedelta(days=30)

                client.table("user_plans").insert(
                    {
                        "user_id": user_id,
                        "plan_id": plan_id,
                        "started_at": start_date.isoformat(),
                        "expires_at": end_date.isoformat(),
                        "is_active": True,
                    }
                ).execute()

            # Update API keys
            client.table("api_keys_new").update(
                {
                    "subscription_plan": new_tier,
                }
            ).eq("user_id", user_id).execute()

            # Allowance: grant only the delta above what was already granted this
            # billing period, and only once the proration invoice is settled.
            if self._proration_settled(updated_subscription):
                self._apply_tier_change_allowance(
                    user_id, updated_subscription, current_tier, new_tier
                )
            else:
                logger.warning(
                    "Proration invoice for subscription %s is not paid yet; deferring the "
                    "%s allowance change to the invoice.paid webhook",
                    stripe_subscription_id,
                    "upgrade",
                )

            # Invalidate user cache
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)

            # Use Stripe proration amount if available, otherwise None
            proration_amount = stripe_proration_amount

            logger.info(
                f"Successfully upgraded subscription {stripe_subscription_id} to {new_tier} "
                f"for user {user_id}. Proration: ${proration_amount}"
            )

            return SubscriptionManagementResponse(
                success=True,
                subscription_id=updated_subscription.id,
                status=updated_subscription.status,
                current_tier=new_tier,
                message=f"Successfully upgraded to {new_tier} tier",
                proration_amount=proration_amount,
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error upgrading subscription for user {user_id}: {e}")
            capture_payment_error(
                e,
                operation="upgrade_subscription",
                user_id=str(user_id),
                details={"new_price_id": request.new_price_id},
            )
            raise Exception(f"Failed to upgrade subscription: {str(e)}") from e

        except Exception as e:
            logger.error(f"Error upgrading subscription for user {user_id}: {e}", exc_info=True)
            raise

    def downgrade_subscription(
        self, user_id: int, request: DowngradeSubscriptionRequest
    ) -> SubscriptionManagementResponse:
        """
        Downgrade a user's subscription to a lower tier (e.g., Max -> Pro).
        Uses Stripe's subscription update with proration to credit the unused time.

        PRORATION LOGIC:
        - On downgrade, subscription_allowance is SET to the new (lower) tier's allowance.
        - The user's remaining unused allowance from the old tier is forfeited (replaced).
        - Purchased credits are never touched by tier changes.
        - A metadata flag 'allowance_handled_at' is set on the Stripe subscription to prevent
          webhook handlers from double-resetting the allowance.
        - Stripe handles the monetary proration (crediting the price difference); we handle
          the credit allowance separately.

        Args:
            user_id: User ID
            request: Downgrade request with new price/product IDs

        Returns:
            SubscriptionManagementResponse with downgrade details
        """
        try:
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            stripe_subscription_id = user.get("stripe_subscription_id")
            if not stripe_subscription_id:
                raise ValueError("User does not have an active subscription to downgrade")

            # Get current subscription
            subscription = stripe.Subscription.retrieve(stripe_subscription_id)

            if subscription.status != "active":
                raise ValueError(
                    f"Cannot downgrade subscription with status: {subscription.status}"
                )

            # Get the subscription item to update
            items = self._get_stripe_object_value(subscription, "items")
            if not items:
                raise ValueError("Subscription has no items")

            items_data = self._get_stripe_object_value(items, "data")
            if not items_data or len(items_data) == 0:
                raise ValueError("Subscription has no item data")

            subscription_item_id = items_data[0].id

            # Bind the tier to the Stripe price being switched to (server-side):
            # the client's product_id must match the price's real product.
            new_product_id, new_tier = self._resolve_price_binding(
                request.new_price_id, request.new_product_id
            )
            if request.proration_behavior != "always_invoice":
                logger.info(
                    "Ignoring client proration_behavior=%r for user %s; forcing always_invoice",
                    request.proration_behavior,
                    user_id,
                )

            # Get current tier for audit logging
            current_tier = user.get("tier", "basic")

            logger.info(
                f"Downgrading subscription {stripe_subscription_id} for user {user_id} "
                f"from {current_tier} to tier {new_tier} (price_id: {request.new_price_id})"
            )

            # Generate a timestamp to mark when this endpoint handled the allowance.
            # Webhook handlers will check this to avoid double-resetting.
            allowance_handled_at = datetime.now(UTC).isoformat()

            # Update the subscription with the new price
            # proration_behavior='create_prorations' will credit the unused time
            updated_subscription = stripe.Subscription.modify(
                stripe_subscription_id,
                items=[
                    {
                        "id": subscription_item_id,
                        "price": request.new_price_id,
                    }
                ],
                # Always invoice the proration immediately and fail the change if it
                # cannot be paid: no free tier changes via proration_behavior='none'.
                proration_behavior="always_invoice",
                payment_behavior="error_if_incomplete",
                expand=["latest_invoice"],
                metadata={
                    "user_id": str(user_id),
                    "product_id": new_product_id,
                    "price_id": request.new_price_id,
                    "tier": new_tier,
                    **self._allowance_baseline_metadata(subscription, current_tier),
                    # Signal to webhook handlers that allowance was already reset by this endpoint.
                    "allowance_handled_at": allowance_handled_at,
                    "allowance_handled_by": "downgrade_subscription",
                },
            )

            # Update user's tier in database
            from src.config.supabase_config import get_supabase_client
            from src.db.plans import get_plan_id_by_tier

            client = get_supabase_client()

            client.table("users").update(
                {
                    "tier": new_tier,
                    "stripe_product_id": new_product_id,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ).eq("id", user_id).execute()

            # Update user_plans entry
            plan_id = get_plan_id_by_tier(new_tier)
            if plan_id:
                # Deactivate existing plans
                client.table("user_plans").update({"is_active": False}).eq(
                    "user_id", user_id
                ).execute()

                # Create new plan assignment
                start_date = datetime.now(UTC)
                _upd_period_end = self._get_subscription_period_end(updated_subscription)
                if _upd_period_end:
                    end_date = datetime.fromtimestamp(_upd_period_end, tz=UTC)
                else:
                    end_date = start_date + timedelta(days=30)

                client.table("user_plans").insert(
                    {
                        "user_id": user_id,
                        "plan_id": plan_id,
                        "started_at": start_date.isoformat(),
                        "expires_at": end_date.isoformat(),
                        "is_active": True,
                    }
                ).execute()

            # Update API keys
            client.table("api_keys_new").update(
                {
                    "subscription_plan": new_tier,
                }
            ).eq("user_id", user_id).execute()

            # Allowance: grant only the delta above what was already granted this
            # billing period, and only once the proration invoice is settled.
            if self._proration_settled(updated_subscription):
                self._apply_tier_change_allowance(
                    user_id, updated_subscription, current_tier, new_tier
                )
            else:
                logger.warning(
                    "Proration invoice for subscription %s is not paid yet; deferring the "
                    "%s allowance change to the invoice.paid webhook",
                    stripe_subscription_id,
                    "downgrade",
                )

            # Invalidate user cache
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)

            logger.info(
                f"Successfully downgraded subscription {stripe_subscription_id} to {new_tier} for user {user_id}"
            )

            return SubscriptionManagementResponse(
                success=True,
                subscription_id=updated_subscription.id,
                status=updated_subscription.status,
                current_tier=new_tier,
                message=f"Successfully downgraded to {new_tier} tier. Credit applied for unused time.",
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error downgrading subscription for user {user_id}: {e}")
            capture_payment_error(
                e,
                operation="downgrade_subscription",
                user_id=str(user_id),
                details={"new_price_id": request.new_price_id},
            )
            raise Exception(f"Failed to downgrade subscription: {str(e)}") from e

        except Exception as e:
            logger.error(f"Error downgrading subscription for user {user_id}: {e}", exc_info=True)
            raise

    def cancel_subscription(
        self, user_id: int, request: CancelSubscriptionRequest
    ) -> SubscriptionManagementResponse:
        """
        Cancel a user's subscription.
        By default, cancels at the end of the billing period (user keeps access until then).

        Args:
            user_id: User ID
            request: Cancel request with options

        Returns:
            SubscriptionManagementResponse with cancellation details
        """
        try:
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            stripe_subscription_id = user.get("stripe_subscription_id")
            if not stripe_subscription_id:
                raise ValueError("User does not have an active subscription to cancel")

            # Get current subscription
            subscription = stripe.Subscription.retrieve(stripe_subscription_id)

            if subscription.status not in ["active", "trialing", "past_due"]:
                raise ValueError(f"Cannot cancel subscription with status: {subscription.status}")

            current_tier = user.get("tier", "basic")

            logger.info(
                f"Canceling subscription {stripe_subscription_id} for user {user_id} "
                f"(cancel_at_period_end: {request.cancel_at_period_end})"
            )

            if request.cancel_at_period_end:
                # Cancel at end of billing period - user keeps access until then
                # Credit handling:
                # - subscription_allowance: remains active until period ends, then zeroed
                #   by the customer.subscription.deleted webhook handler
                # - purchased_credits: always preserved (paid for separately)
                updated_subscription = stripe.Subscription.modify(
                    stripe_subscription_id,
                    cancel_at_period_end=True,
                    metadata={
                        "cancellation_reason": request.reason or "User requested cancellation",
                    },
                )

                effective_date = (
                    datetime.fromtimestamp(_cancel_period_end, tz=UTC)
                    if (
                        _cancel_period_end := self._get_subscription_period_end(
                            updated_subscription
                        )
                    )
                    else None
                )

                # Fetch current credit balances for audit logging
                from src.db.users import get_user_by_id as get_user_fresh

                user_fresh = get_user_fresh(user_id)
                current_allowance = (
                    float(user_fresh.get("subscription_allowance") or 0) if user_fresh else 0.0
                )
                purchased_credits = (
                    float(user_fresh.get("purchased_credits") or 0) if user_fresh else 0.0
                )

                # Log pending cancellation in credit transactions for audit trail
                # Note: Allowance is NOT zeroed yet - it will be zeroed when the
                # customer.subscription.deleted webhook fires at period end
                from src.db.credit_transactions import TransactionType, log_credit_transaction

                log_credit_transaction(
                    user_id=user_id,
                    amount=0,  # No immediate credit change
                    transaction_type=TransactionType.SUBSCRIPTION_CANCELLATION,
                    description=(
                        f"Subscription cancellation scheduled at period end. "
                        f"Allowance (${current_allowance:.2f}) will be forfeited on "
                        f"{effective_date.strftime('%Y-%m-%d') if effective_date else 'period end'}. "
                        f"Purchased credits (${purchased_credits:.2f}) will be retained."
                    ),
                    balance_before=current_allowance + purchased_credits,
                    balance_after=current_allowance + purchased_credits,  # No change yet
                    metadata={
                        "forfeited_allowance": 0,  # Not forfeited yet
                        "pending_forfeiture_allowance": current_allowance,
                        "retained_purchased_credits": purchased_credits,
                        "effective_date": effective_date.isoformat() if effective_date else None,
                        "cancellation_type": "cancel_at_period_end",
                        "cancellation_reason": request.reason or "User requested cancellation",
                        "subscription_id": updated_subscription.id,
                        "current_tier": current_tier,
                    },
                    created_by="system:subscription_cancellation_scheduled",
                )

                # Update user's subscription status to indicate pending cancellation
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()

                # Keep the tier, allowance, and purchased_credits as-is.
                # The actual downgrade and allowance forfeiture will happen when
                # the customer.subscription.deleted webhook fires at period end.
                client.table("users").update(
                    {
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ).eq("id", user_id).execute()

                # Invalidate user cache
                from src.db.users import invalidate_user_cache_by_id

                invalidate_user_cache_by_id(user_id)

                logger.info(
                    f"Subscription {stripe_subscription_id} marked for cancellation at period end "
                    f"for user {user_id} (effective: {effective_date}, "
                    f"current_allowance=${current_allowance:.2f} will be forfeited, "
                    f"purchased_credits=${purchased_credits:.2f} will be retained)"
                )

                return SubscriptionManagementResponse(
                    success=True,
                    subscription_id=updated_subscription.id,
                    status="cancel_scheduled",
                    current_tier=current_tier,
                    message=f"Subscription will be canceled at the end of the billing period. You'll keep access until {effective_date.strftime('%B %d, %Y') if effective_date else 'end of period'}.",
                    effective_date=effective_date,
                )

            else:
                # Cancel immediately
                # Credit handling:
                # - subscription_allowance: zeroed immediately (forfeited)
                # - purchased_credits: preserved (paid for separately)
                canceled_subscription = stripe.Subscription.cancel(stripe_subscription_id)
                cancellation_effective_date = datetime.now(UTC).isoformat()

                # Forfeit remaining subscription allowance; purchased credits are preserved
                from src.db.users import forfeit_subscription_allowance

                forfeiture_result = forfeit_subscription_allowance(
                    user_id,
                    raise_on_error=False,
                    effective_date=cancellation_effective_date,
                    cancellation_context="immediate_cancellation",
                )

                forfeited = forfeiture_result.get("forfeited_allowance", 0)
                retained = forfeiture_result.get("retained_purchased_credits", 0)

                logger.info(
                    f"Immediate cancellation credit summary for user {user_id}: "
                    f"forfeited_allowance=${forfeited:.2f}, "
                    f"retained_purchased_credits=${retained:.2f}, "
                    f"subscription_id={stripe_subscription_id}, "
                    f"from_tier={current_tier}"
                )

                # Update user to basic tier
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()

                client.table("users").update(
                    {
                        "subscription_status": "canceled",
                        "tier": "basic",
                        "stripe_subscription_id": None,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ).eq("id", user_id).execute()

                # Update API keys
                client.table("api_keys_new").update(
                    {
                        "subscription_status": "canceled",
                        "subscription_plan": "basic",
                    }
                ).eq("user_id", user_id).execute()

                # Invalidate user cache
                from src.db.users import invalidate_user_cache_by_id

                invalidate_user_cache_by_id(user_id)

                logger.info(
                    f"Subscription {stripe_subscription_id} canceled immediately for user {user_id}, "
                    f"downgraded from {current_tier} to basic tier"
                )

                return SubscriptionManagementResponse(
                    success=True,
                    subscription_id=canceled_subscription.id,
                    status="canceled",
                    current_tier="basic",
                    message="Subscription canceled immediately. You have been downgraded to the free tier.",
                    effective_date=datetime.now(UTC),
                )

        except stripe.StripeError as e:
            logger.error(f"Stripe error canceling subscription for user {user_id}: {e}")
            capture_payment_error(
                e,
                operation="cancel_subscription",
                user_id=str(user_id),
                details={"cancel_at_period_end": request.cancel_at_period_end},
            )
            raise Exception(f"Failed to cancel subscription: {str(e)}") from e

        except Exception as e:
            logger.error(f"Error canceling subscription for user {user_id}: {e}", exc_info=True)
            raise
