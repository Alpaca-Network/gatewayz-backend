#!/usr/bin/env python3
"""
Stripe Service
Handles all Stripe payment operations
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import stripe

from src.db.payments import create_payment, get_payment_by_stripe_intent, update_payment_status
from src.db.subscription_products import get_tier_from_product_id
from src.db.users import add_credits_to_user, get_user_by_id
from src.db.webhook_events import is_event_processed, record_processed_event
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

        # If tier is missing or defaulted to basic, try to determine from subscription items
        if not tier or tier == "basic":
            items = self._get_stripe_object_value(subscription, "items")
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
                                logger.info(
                                    f"Resolved tier '{looked_up_tier}' from subscription item product_id={item_product_id}"
                                )
                                tier = looked_up_tier
                            else:
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
            refreshed_session = stripe.checkout.Session.retrieve(session_id, expand=["metadata"])
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
            intent = stripe.PaymentIntent.retrieve(payment_intent_id, expand=["metadata"])
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
                metadata={"description": request.description, **(request.metadata or {})},
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

            # Calculate credits to add:
            # - If credit_value is provided (for discounted packages), use that
            # - Otherwise, fall back to amount/100 (converting cents to dollars)
            # credit_value is in dollars, credits_cents is used for metadata (in cents)
            if request.credit_value is not None:
                # credit_value is in dollars, convert to cents for metadata
                # Use Decimal for precise financial calculations
                credit_value_decimal = Decimal(str(request.credit_value))
                credits_cents = int(credit_value_decimal * 100)
                credits_display = f"${request.credit_value:.0f}"
                logger.info(
                    f"Using discounted credit_value: ${request.credit_value} "
                    f"(payment amount: ${request.amount / 100})"
                )
            else:
                # Fall back to payment amount
                credits_cents = request.amount
                credits_display = f"${request.amount / 100:.0f}"

            checkout_metadata = {
                "user_id": str(user_id),
                "payment_id": str(payment["id"]),
                "credits_cents": str(credits_cents),
                "credits": str(credits_cents),  # Keep for backward compatibility
                **(request.metadata or {}),
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

            # Check for duplicate event (idempotency)
            if is_event_processed(event["id"]):
                logger.warning(f"Duplicate webhook event detected, skipping: {event['id']}")
                return WebhookProcessingResult(
                    success=True,
                    event_type=event["type"],
                    event_id=event["id"],
                    message=f"Event {event['id']} already processed (duplicate)",
                    processed_at=datetime.now(UTC),
                )

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

            # Record event as processed immediately after duplicate check to ensure
            # idempotency even if handlers raise exceptions. This prevents duplicate
            # processing when Stripe retries the webhook.
            record_processed_event(
                event_id=event["id"],
                event_type=event["type"],
                user_id=user_id,
                metadata={"stripe_account": event.get("account")},
            )

            # One-time payment events
            if event["type"] == "checkout.session.completed":
                self._handle_checkout_completed(event["data"]["object"])
            elif event["type"] == "payment_intent.succeeded":
                self._handle_payment_succeeded(event["data"]["object"])
            elif event["type"] == "payment_intent.payment_failed":
                self._handle_payment_failed(event["data"]["object"])

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

            amount_dollars = float(Decimal(credits_cents) / 100)  # Convert cents to dollars

            # Add credits and log transaction
            add_credits_to_user(
                user_id=user_id,
                credits=amount_dollars,
                transaction_type="purchase",
                description=f"Stripe checkout - ${amount_dollars}",
                payment_id=payment_id,
                metadata={
                    "stripe_session_id": session_id,
                    "stripe_payment_intent_id": payment_intent_id,
                },
            )

            # Update payment
            update_payment_status(
                payment_id=payment_id,
                status="completed",
                stripe_payment_intent_id=payment_intent_id,
                stripe_session_id=session_id,
            )

            logger.info(f"Checkout completed: Added {amount_dollars} credits to user {user_id}")

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

            # Check for referral bonus (first purchase of $10+)
            try:
                from src.config.supabase_config import get_supabase_client
                from src.services.referral import apply_referral_bonus, mark_first_purchase

                client = get_supabase_client()
                user_result = client.table("users").select("*").eq("id", user_id).execute()

                if user_result.data:
                    user = user_result.data[0]
                    has_made_first_purchase = user.get("has_made_first_purchase", False)
                    referred_by_code = user.get("referred_by_code")

                    # Apply referral bonus if:
                    # 1. This is first purchase
                    # 2. User was referred by someone
                    # 3. Purchase is $10 or more
                    if not has_made_first_purchase and referred_by_code and amount_dollars >= 10.0:
                        success, error_msg, bonus_data = apply_referral_bonus(
                            user_id=user_id,
                            referral_code=referred_by_code,
                            purchase_amount=amount_dollars,
                        )

                        if success:
                            logger.info(
                                f"Referral bonus applied! User {user_id} and referrer both received "
                                f"${bonus_data['user_bonus']} (code: {referred_by_code})"
                            )
                        else:
                            logger.warning(
                                f"Failed to apply referral bonus for user {user_id}: {error_msg}"
                            )

                    # Mark first purchase regardless of referral
                    if not has_made_first_purchase:
                        mark_first_purchase(user_id)

            except Exception as referral_error:
                # Don't fail the payment if referral bonus fails
                logger.error(f"Error processing referral bonus: {referral_error}", exc_info=True)

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

    def _handle_payment_succeeded(self, payment_intent):
        """Handle successful payment"""
        try:
            payment = get_payment_by_stripe_intent(payment_intent.id)
            if payment:
                update_payment_status(payment_id=payment["id"], status="completed")
                # Add credits and log transaction
                amount = payment.get("amount_usd", payment.get("amount", 0))
                add_credits_to_user(
                    user_id=payment["user_id"],
                    credits=amount,
                    transaction_type="purchase",
                    description=f"Stripe payment - ${amount}",
                    payment_id=payment["id"],
                    metadata={"stripe_payment_intent_id": payment_intent.id},
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

    # ==================== Refunds ====================

    def create_refund(self, request: CreateRefundRequest) -> RefundResponse:
        """Create a refund"""
        try:
            refund = stripe.Refund.create(
                payment_intent=request.payment_intent_id,
                amount=request.amount,
                reason=request.reason,
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

            # Determine tier from product_id using database configuration
            tier = get_tier_from_product_id(request.product_id)

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
                    "user_id": str(user_id),
                    "product_id": request.product_id,
                    "tier": tier,
                    **(request.metadata or {}),
                },
            }

            # Add subscription_data for subscription mode
            if request.mode == "subscription":
                session_params["subscription_data"] = {
                    "metadata": {
                        "user_id": str(user_id),
                        "product_id": request.product_id,
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
            if subscription.current_period_end:
                update_data["subscription_end_date"] = subscription.current_period_end

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
                if subscription.current_period_end:
                    end_date = datetime.fromtimestamp(subscription.current_period_end, tz=UTC)
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

            if subscription.current_period_end:
                update_data["subscription_end_date"] = subscription.current_period_end

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
                    if subscription.current_period_end:
                        end_date = datetime.fromtimestamp(subscription.current_period_end, tz=UTC)
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

                # Update subscription allowance when tier changes (for upgrades/downgrades)
                # IMPORTANT: Check if allowance was already handled by the upgrade/downgrade
                # endpoint to prevent double-resetting. The endpoint sets 'allowance_handled_at'
                # in the subscription metadata when it resets the allowance.
                allowance_handled_at = metadata.get("allowance_handled_at") if metadata else None
                allowance_handled_by = metadata.get("allowance_handled_by") if metadata else None
                should_skip_allowance_reset = False

                if allowance_handled_at:
                    try:
                        handled_time = datetime.fromisoformat(allowance_handled_at)
                        seconds_since_handled = (datetime.now(UTC) - handled_time).total_seconds()
                        # If allowance was handled within the last 120 seconds by the upgrade/downgrade
                        # endpoint, skip re-resetting to avoid double-crediting
                        if seconds_since_handled < 120:
                            should_skip_allowance_reset = True
                            logger.info(
                                f"Skipping allowance reset in subscription.updated webhook for user {user_id}: "
                                f"allowance was already handled {seconds_since_handled:.1f}s ago "
                                f"by {allowance_handled_by or 'unknown'}. "
                                f"This prevents double-crediting on upgrade/downgrade."
                            )
                        else:
                            logger.info(
                                f"allowance_handled_at is stale ({seconds_since_handled:.1f}s ago) "
                                f"for user {user_id}. Proceeding with allowance reset in webhook."
                            )
                    except (ValueError, TypeError) as e:
                        logger.warning(
                            f"Could not parse allowance_handled_at '{allowance_handled_at}' "
                            f"for user {user_id}: {e}. Proceeding with allowance reset."
                        )

                if not should_skip_allowance_reset:
                    from src.db.subscription_products import get_allowance_from_tier
                    from src.db.users import reset_subscription_allowance

                    new_allowance = get_allowance_from_tier(tier)
                    if new_allowance > 0:
                        # Reset allowance to new tier's amount on tier change
                        reset_result = reset_subscription_allowance(user_id, new_allowance, tier)
                        if not reset_result:
                            logger.error(
                                f"Failed to reset allowance for user {user_id} during subscription update webhook. "
                                f"Tier: {tier}, allowance: ${new_allowance}. "
                                f"Raising exception to trigger Stripe retry."
                            )
                            raise Exception(
                                f"Failed to update subscription allowance for user {user_id}"
                            )
                        logger.info(
                            f"Updated allowance to ${new_allowance} for user {user_id} ({tier} tier) "
                            f"on subscription update webhook"
                        )

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
        """Handle subscription deleted/canceled event"""
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

            logger.info(f"Subscription deleted for user {user_id}: {subscription.id}")

            # Forfeit subscription allowance before downgrading
            # Use raise_on_error=True to ensure data consistency - if forfeiture fails,
            # Stripe will retry the webhook
            from src.db.users import forfeit_subscription_allowance

            forfeited = forfeit_subscription_allowance(user_id, raise_on_error=True)
            if forfeited > 0:
                logger.info(
                    f"Forfeited ${forfeited} allowance for user {user_id} on subscription cancellation"
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

            # CRITICAL: Invalidate user cache so profile API returns fresh data
            from src.db.users import invalidate_user_cache_by_id

            invalidate_user_cache_by_id(user_id)

            logger.info(
                f"User {user_id} subscription canceled, downgraded to basic tier, cache invalidated"
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
            # Get subscription from invoice
            if not invoice.subscription:
                logger.info(f"Invoice {invoice.id} is not for a subscription, skipping")
                return

            # Check billing_reason to determine if this is a proration invoice
            billing_reason = getattr(invoice, "billing_reason", None)
            logger.info(
                f"Processing invoice.paid: invoice_id={invoice.id}, "
                f"billing_reason={billing_reason}, subscription={invoice.subscription}"
            )

            # Skip allowance reset for proration invoices from upgrades/downgrades.
            # When a user upgrades/downgrades, Stripe fires an invoice.paid event for the
            # proration charge/credit. The upgrade/downgrade endpoint already reset the
            # allowance, so we must NOT reset it again here.
            if billing_reason == "subscription_update":
                logger.info(
                    f"Invoice {invoice.id} is a proration invoice (billing_reason=subscription_update). "
                    f"Skipping allowance reset - it was already handled by the upgrade/downgrade endpoint. "
                    f"This prevents double-crediting."
                )
                return

            subscription = stripe.Subscription.retrieve(invoice.subscription)

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
            if not invoice.subscription:
                logger.info(f"Invoice {invoice.id} is not for a subscription, skipping")
                return

            subscription = stripe.Subscription.retrieve(invoice.subscription)

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
                    datetime.fromtimestamp(subscription.current_period_start, tz=UTC)
                    if subscription.current_period_start
                    else None
                ),
                current_period_end=(
                    datetime.fromtimestamp(subscription.current_period_end, tz=UTC)
                    if subscription.current_period_end
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

            # Determine the new tier from product ID
            new_tier = get_tier_from_product_id(request.new_product_id)
            if not new_tier or new_tier == "basic":
                raise ValueError(
                    f"Invalid product ID for upgrade: {request.new_product_id}. "
                    "Cannot resolve to a valid paid tier."
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
                proration_behavior=request.proration_behavior,
                metadata={
                    "user_id": str(user_id),
                    "product_id": request.new_product_id,
                    "tier": new_tier,
                    # Signal to webhook handlers that allowance was already reset by this endpoint.
                    # The webhook handler checks this timestamp and skips allowance reset if it
                    # was set within the last 60 seconds.
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
                    "stripe_product_id": request.new_product_id,
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
                if updated_subscription.current_period_end:
                    end_date = datetime.fromtimestamp(
                        updated_subscription.current_period_end, tz=UTC
                    )
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

            # =====================================================================
            # PRORATION FIX: SET allowance to new tier level, do NOT add difference
            # =====================================================================
            # On upgrade, subscription_allowance is REPLACED with the new tier's
            # allowance. The user's remaining old allowance is forfeited because:
            #   1. Stripe charges the prorated price difference for the upgrade
            #   2. The new tier's full allowance replaces the old one
            #   3. purchased_credits remain untouched
            # This prevents double-crediting where users would get old remaining + new full.
            from src.db.credit_transactions import TransactionType, log_credit_transaction
            from src.db.subscription_products import get_allowance_from_tier
            from src.db.users import get_user_by_id as get_user_fresh
            from src.db.users import reset_subscription_allowance

            new_allowance = get_allowance_from_tier(new_tier)
            old_tier_allowance = get_allowance_from_tier(current_tier)

            if new_allowance > 0:
                # Get current balance for audit logging (this is what the user has remaining)
                user_fresh = get_user_fresh(user_id)
                old_remaining_allowance = (
                    float(user_fresh.get("subscription_allowance", 0)) if user_fresh else 0.0
                )
                purchased_credits = (
                    float(user_fresh.get("purchased_credits", 0)) if user_fresh else 0.0
                )

                # Calculate how much of the old allowance was used
                old_used = max(0.0, old_tier_allowance - old_remaining_allowance)

                # Calculate what is being forfeited (unused old allowance that won't carry over)
                forfeited_allowance = old_remaining_allowance

                logger.info(
                    f"Proration calculation for user {user_id} upgrade {current_tier} -> {new_tier}: "
                    f"old_tier_allowance=${old_tier_allowance}, "
                    f"old_remaining=${old_remaining_allowance}, "
                    f"old_used=${old_used}, "
                    f"forfeited=${forfeited_allowance}, "
                    f"new_allowance=${new_allowance}, "
                    f"purchased_credits=${purchased_credits} (unchanged), "
                    f"stripe_proration=${stripe_proration_amount}"
                )

                # Reset allowance to new tier's full amount
                # This REPLACES the old allowance (does not add to it)
                reset_result = reset_subscription_allowance(user_id, new_allowance, new_tier)
                if not reset_result:
                    logger.error(f"Failed to reset allowance for user {user_id} during upgrade")
                    raise Exception("Failed to update subscription allowance")

                logger.info(
                    f"Allowance SET to ${new_allowance} for user {user_id} ({new_tier} tier). "
                    f"Previous remaining ${old_remaining_allowance} was replaced (not added)."
                )

                # Log detailed audit trail for subscription upgrade
                # NOTE: reset_subscription_allowance() already logs a SUBSCRIPTION_RENEWAL
                # transaction internally. This additional SUBSCRIPTION_UPGRADE transaction
                # captures the upgrade-specific context (tier change, proration details).
                log_credit_transaction(
                    user_id=user_id,
                    amount=new_allowance - old_remaining_allowance,
                    transaction_type=TransactionType.SUBSCRIPTION_UPGRADE,
                    description=(
                        f"Subscription upgraded from {current_tier} to {new_tier}. "
                        f"Allowance SET to ${new_allowance} (not incremented). "
                        f"Forfeited ${forfeited_allowance} unused from old tier."
                    ),
                    balance_before=old_remaining_allowance + purchased_credits,
                    balance_after=new_allowance + purchased_credits,
                    metadata={
                        "from_tier": current_tier,
                        "to_tier": new_tier,
                        "old_tier_allowance": old_tier_allowance,
                        "old_remaining_allowance": old_remaining_allowance,
                        "old_used_allowance": old_used,
                        "forfeited_allowance": forfeited_allowance,
                        "new_allowance": new_allowance,
                        "purchased_credits_unchanged": purchased_credits,
                        "proration_method": "set_not_increment",
                        "stripe_proration_amount": stripe_proration_amount,
                        "subscription_id": updated_subscription.id,
                        "product_id": request.new_product_id,
                        "price_id": request.new_price_id,
                        "allowance_handled_at": allowance_handled_at,
                    },
                    created_by="system:subscription_upgrade",
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

            # Determine the new tier from product ID
            new_tier = get_tier_from_product_id(request.new_product_id)
            if not new_tier or new_tier == "basic":
                raise ValueError(
                    f"Invalid product ID for downgrade: {request.new_product_id}. "
                    "Cannot resolve to a valid paid tier."
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
                proration_behavior=request.proration_behavior,
                metadata={
                    "user_id": str(user_id),
                    "product_id": request.new_product_id,
                    "tier": new_tier,
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
                    "stripe_product_id": request.new_product_id,
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
                if updated_subscription.current_period_end:
                    end_date = datetime.fromtimestamp(
                        updated_subscription.current_period_end, tz=UTC
                    )
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

            # =====================================================================
            # PRORATION FIX: SET allowance to new tier level, do NOT add difference
            # =====================================================================
            # On downgrade, subscription_allowance is REPLACED with the new tier's
            # (lower) allowance. Stripe credits the monetary difference; we reset
            # the credit allowance to match the new tier.
            from src.db.credit_transactions import TransactionType, log_credit_transaction
            from src.db.subscription_products import get_allowance_from_tier
            from src.db.users import get_user_by_id as get_user_fresh
            from src.db.users import reset_subscription_allowance

            new_allowance = get_allowance_from_tier(new_tier)
            old_tier_allowance = get_allowance_from_tier(current_tier)

            if new_allowance > 0:
                # Get current balance for audit logging (this is what the user has remaining)
                user_fresh = get_user_fresh(user_id)
                old_remaining_allowance = (
                    float(user_fresh.get("subscription_allowance", 0)) if user_fresh else 0.0
                )
                purchased_credits = (
                    float(user_fresh.get("purchased_credits", 0)) if user_fresh else 0.0
                )

                # Calculate how much of the old allowance was used
                old_used = max(0.0, old_tier_allowance - old_remaining_allowance)

                # Calculate what is being forfeited (unused old allowance exceeding new tier)
                forfeited_allowance = max(0.0, old_remaining_allowance - new_allowance)

                logger.info(
                    f"Proration calculation for user {user_id} downgrade {current_tier} -> {new_tier}: "
                    f"old_tier_allowance=${old_tier_allowance}, "
                    f"old_remaining=${old_remaining_allowance}, "
                    f"old_used=${old_used}, "
                    f"forfeited=${forfeited_allowance}, "
                    f"new_allowance=${new_allowance}, "
                    f"purchased_credits=${purchased_credits} (unchanged)"
                )

                # Reset allowance to new tier's amount (matches what user is now paying for)
                reset_result = reset_subscription_allowance(user_id, new_allowance, new_tier)
                if not reset_result:
                    logger.error(f"Failed to reset allowance for user {user_id} during downgrade")
                    raise Exception("Failed to update subscription allowance")

                logger.info(
                    f"Allowance SET to ${new_allowance} for user {user_id} ({new_tier} tier). "
                    f"Previous remaining ${old_remaining_allowance} was replaced (not carried over)."
                )

                # Log detailed audit trail for subscription downgrade
                log_credit_transaction(
                    user_id=user_id,
                    amount=new_allowance - old_remaining_allowance,
                    transaction_type=TransactionType.SUBSCRIPTION_DOWNGRADE,
                    description=(
                        f"Subscription downgraded from {current_tier} to {new_tier}. "
                        f"Allowance SET to ${new_allowance} (not incremented). "
                        f"Forfeited ${forfeited_allowance} unused from old tier."
                    ),
                    balance_before=old_remaining_allowance + purchased_credits,
                    balance_after=new_allowance + purchased_credits,
                    metadata={
                        "from_tier": current_tier,
                        "to_tier": new_tier,
                        "old_tier_allowance": old_tier_allowance,
                        "old_remaining_allowance": old_remaining_allowance,
                        "old_used_allowance": old_used,
                        "forfeited_allowance": forfeited_allowance,
                        "new_allowance": new_allowance,
                        "purchased_credits_unchanged": purchased_credits,
                        "proration_method": "set_not_increment",
                        "subscription_id": updated_subscription.id,
                        "product_id": request.new_product_id,
                        "price_id": request.new_price_id,
                        "allowance_handled_at": allowance_handled_at,
                    },
                    created_by="system:subscription_downgrade",
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
                updated_subscription = stripe.Subscription.modify(
                    stripe_subscription_id,
                    cancel_at_period_end=True,
                    metadata={
                        "cancellation_reason": request.reason or "User requested cancellation",
                    },
                )

                effective_date = (
                    datetime.fromtimestamp(updated_subscription.current_period_end, tz=UTC)
                    if updated_subscription.current_period_end
                    else None
                )

                # Update user's subscription status to indicate pending cancellation
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()

                # Keep the tier and status as-is, but mark cancellation in metadata
                # The actual downgrade will happen when subscription.deleted webhook fires
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
                    f"(effective: {effective_date}) for user {user_id}"
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
                canceled_subscription = stripe.Subscription.cancel(stripe_subscription_id)

                # Forfeit remaining allowance and downgrade tier
                from src.db.users import forfeit_subscription_allowance

                forfeited = forfeit_subscription_allowance(user_id, raise_on_error=False)
                if forfeited > 0:
                    logger.info(
                        f"Forfeited ${forfeited} allowance for user {user_id} on immediate cancellation"
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
                    f"downgraded to basic tier"
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
