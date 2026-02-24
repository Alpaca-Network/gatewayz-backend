#!/usr/bin/env python3
"""
Stripe Payment Routes
Endpoints for handling Stripe webhooks and payment operations
"""

import inspect
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.db.payments import (
    get_payment,
    get_user_payments,
)
from src.schemas.payments import (
    CancelSubscriptionRequest,
    CreateCheckoutSessionRequest,
    CreatePaymentIntentRequest,
    CreateRefundRequest,
    CreateSubscriptionCheckoutRequest,
    DowngradeSubscriptionRequest,
    UpgradeSubscriptionRequest,
    WebhookProcessingResult,
)
from src.security import deps as security_deps
from src.security.deps import security as bearer_security
from src.services.payments import StripeService
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stripe", tags=["Stripe Payments"])

# Initialize Stripe service
stripe_service = StripeService()


async def _execute_user_override(override, request: Request):
    try:
        result = override(request)
    except TypeError:
        result = override()

    if inspect.isawaitable(result):
        return await result
    return result


async def _get_current_user_dependency(request: Request):
    override = globals().get("get_current_user")
    if override is not _get_current_user_dependency:
        return await _execute_user_override(override, request)

    credentials = await bearer_security(request)
    api_key = await security_deps.get_api_key(credentials=credentials, request=request)
    return await security_deps.get_current_user(api_key=api_key)


# Expose name expected by tests for patching
get_current_user = _get_current_user_dependency


# ==================== Webhook Endpoint ====================


@router.post("/webhook", status_code=200)
async def stripe_webhook(
    request: Request, stripe_signature: str = Header(None, alias="stripe-signature")
):
    """
    Stripe webhook endpoint - handles all Stripe events

    This endpoint receives webhooks from Stripe for payment and subscription events:

    Payment Events:
    - checkout.session.completed - User completed checkout, add credits
    - checkout.session.expired - Checkout expired, mark payment as canceled
    - payment_intent.succeeded - Payment succeeded, add credits
    - payment_intent.payment_failed - Payment failed, update status
    - payment_intent.canceled - Payment canceled by user
    - charge.refunded - Charge was refunded, deduct credits

    Subscription Events:
    - customer.subscription.created - Subscription created, upgrade user tier
    - customer.subscription.updated - Subscription updated, sync status
    - customer.subscription.deleted - Subscription canceled, downgrade tier
    - invoice.paid - Subscription renewed, add monthly credits
    - invoice.payment_failed - Payment failed, mark subscription past_due

    IMPORTANT: This endpoint must be configured in your Stripe Dashboard:
    1. Go to Stripe Dashboard > Developers > Webhooks
    2. Add endpoint: https://your-domain.com/api/stripe/webhook
    3. Select events to listen for (listed above)
    4. Copy webhook signing secret to STRIPE_WEBHOOK_SECRET env variable

    IMPORTANT: This endpoint ALWAYS returns HTTP 200 status code to Stripe, even when
    processing fails. Errors are logged for investigation but do not cause HTTP errors,
    as this prevents Stripe from retrying the webhook. Stripe expects webhooks to be
    delivered asynchronously and will handle retries automatically.

    Args:
        request: FastAPI request object containing raw webhook payload
        stripe_signature: Stripe signature header for verification

    Returns:
        JSONResponse with HTTP 200 and processing result (always)
    """
    payload = await request.body()
    event_type = "unknown"
    event_id = "unknown"
    success = False
    message = "Webhook received"

    try:
        if not stripe_signature:
            logger.error("Missing Stripe signature header")
            raise ValueError("Missing stripe-signature header")

        # Process webhook through Stripe service
        result: WebhookProcessingResult = stripe_service.handle_webhook(
            payload=payload, signature=stripe_signature
        )

        event_type = result.event_type
        event_id = result.event_id
        success = result.success
        message = result.message

        logger.info(f"Webhook processed: {result.event_type} - {result.message}")

        return JSONResponse(
            status_code=200,
            content={
                "success": result.success,
                "event_type": result.event_type,
                "event_id": result.event_id,
                "message": result.message,
                "processed_at": result.processed_at.isoformat(),
            },
        )

    except ValueError as e:
        # Signature verification failed or missing header
        logger.error(f"Webhook validation failed: {e}", exc_info=True)
        message = f"Validation failed: {str(e)}"
        success = False

    except Exception as e:
        # Log the error with full context for investigation
        logger.error(
            f"Webhook processing error for event_type={event_type}, event_id={event_id}: {e}",
            exc_info=True,
        )
        message = f"Processing error: {str(e)}"
        success = False

    # Always return 200 OK to Stripe, even on errors
    # Stripe will retry failed webhooks automatically
    return JSONResponse(
        status_code=200,
        content={
            "success": success,
            "event_type": event_type,
            "event_id": event_id,
            "message": message,
            "processed_at": datetime.now(UTC).isoformat(),
        },
    )


# ==================== Checkout Sessions ====================


@router.post("/checkout-session", response_model=dict[str, Any])
async def create_checkout_session(
    request: CreateCheckoutSessionRequest, current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Create a Stripe checkout session for hosted payment page

    This creates a Stripe-hosted payment page where users can complete their purchase.
    After payment, Stripe redirects to success_url or cancel_url.

    Args:
        request: Checkout session parameters (amount, currency, URLs)
        current_user: Authenticated user from token

    Returns:
        Checkout session with URL and session ID

    Example request body:
    {
        "amount": 1000,  # $10.00 in cents
        "currency": "usd",
        "description": "1000 credits purchase",
        "success_url": "https://your-app.com/payment/success",
        "cancel_url": "https://your-app.com/payment/cancel"
    }
    """
    try:
        user_id = current_user["id"]
        logger.info(
            "Creating checkout session for user %s, amount: %s, currency: %s",
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(str(request.amount)),
            sanitize_for_logging(request.currency),
        )

        session = stripe_service.create_checkout_session(user_id=user_id, request=request)

        logger.info(
            "Checkout session created for user %s: %s",
            sanitize_for_logging(str(user_id)),
            sanitize_for_logging(session.session_id),
        )

        return {
            "session_id": session.session_id,
            "url": session.url,
            "payment_id": session.payment_id,
            "status": session.status.value,
            "amount": session.amount,
            "currency": session.currency,
            "expires_at": session.expires_at.isoformat(),
        }

    except ValueError as e:
        logger.error(f"Validation error creating checkout session: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Error creating checkout session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create checkout session: {str(e)}")


@router.get("/checkout-session/{session_id}")
async def get_checkout_session(
    session_id: str, current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Retrieve checkout session details

    Args:
        session_id: Stripe checkout session ID
        current_user: Authenticated user

    Returns:
        Checkout session details
    """
    try:
        session = stripe_service.retrieve_checkout_session(session_id)

        return {
            "session_id": session["id"],
            "payment_status": session["payment_status"],
            "status": session["status"],
            "amount_total": session["amount_total"],
            "currency": session["currency"],
            "customer_email": session["customer_email"],
        }

    except Exception as e:
        logger.error(f"Error retrieving checkout session: {e}")
        raise HTTPException(status_code=404, detail=str(e))


# ==================== Payment Intents ====================


@router.post("/payment-intent", response_model=dict[str, Any])
async def create_payment_intent(
    request: CreatePaymentIntentRequest, current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Create a Stripe payment intent for custom payment flows

    Use this for building your own payment UI with Stripe Elements.
    Returns a client_secret that you use on the frontend.

    Args:
        request: Payment intent parameters
        current_user: Authenticated user

    Returns:
        Payment intent with client_secret

    Example request body:
    {
        "amount": 1000,
        "currency": "usd",
        "description": "1000 credits",
        "automatic_payment_methods": true
    }
    """
    try:
        user_id = current_user["id"]

        intent = stripe_service.create_payment_intent(user_id=user_id, request=request)

        logger.info(f"Payment intent created for user {user_id}: {intent.payment_intent_id}")

        return {
            "payment_intent_id": intent.payment_intent_id,
            "client_secret": intent.client_secret,
            "payment_id": intent.payment_id,
            "status": intent.status.value,
            "amount": intent.amount,
            "currency": intent.currency,
            "next_action": intent.next_action,
        }

    except ValueError as e:
        logger.error(f"Validation error creating payment intent: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Error creating payment intent: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create payment intent: {str(e)}")


@router.get("/payment-intent/{payment_intent_id}")
async def get_payment_intent(
    payment_intent_id: str, current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Retrieve payment intent details

    Args:
        payment_intent_id: Stripe payment intent ID
        current_user: Authenticated user

    Returns:
        Payment intent details
    """
    try:
        intent = stripe_service.retrieve_payment_intent(payment_intent_id)

        return {
            "payment_intent_id": intent["id"],
            "status": intent["status"],
            "amount": intent["amount"],
            "currency": intent["currency"],
            "customer": intent["customer"],
            "payment_method": intent["payment_method"],
        }

    except Exception as e:
        logger.error(f"Error retrieving payment intent: {e}")
        raise HTTPException(status_code=404, detail=str(e))


# ==================== Credit Packages ====================


@router.get("/credit-packages")
async def get_credit_packages():
    """
    Get available credit packages for purchase

    Returns:
        List of available credit packages with pricing

    Example response:
    {
        "packages": [
            {
                "id": "starter",
                "name": "Starter Pack",
                "credits": 1000,
                "amount": 1000,
                "currency": "usd",
                "description": "Perfect for trying out the platform"
            }
        ]
    }
    """
    try:
        packages = stripe_service.get_credit_packages()

        return {
            "packages": [
                {
                    "id": pkg.id,
                    "name": pkg.name,
                    "credits": pkg.credits,
                    "amount": pkg.amount,
                    "currency": pkg.currency.value,
                    "description": pkg.description,
                    "features": pkg.features,
                    "popular": pkg.popular,
                    "discount_percentage": pkg.discount_percentage,
                }
                for pkg in packages.packages
            ]
        }

    except Exception as e:
        logger.error(f"Error getting credit packages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Refunds ====================


@router.post("/refund", response_model=dict[str, Any])
async def create_refund(
    request: CreateRefundRequest, current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Create a refund for a payment (admin only)

    Args:
        request: Refund parameters
        current_user: Authenticated user (must be admin)

    Returns:
        Refund details
    """
    try:
        # Check if user is admin (implement your admin check logic)
        if not current_user.get("is_admin", False):
            raise HTTPException(status_code=403, detail="Only administrators can create refunds")

        refund = stripe_service.create_refund(request)

        logger.info(f"Refund created: {refund.refund_id}")

        return {
            "refund_id": refund.refund_id,
            "payment_intent_id": refund.payment_intent_id,
            "amount": refund.amount,
            "currency": refund.currency,
            "status": refund.status,
            "reason": refund.reason,
            "created_at": refund.created_at.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating refund: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Payment History ====================


@router.get("/payments")
async def get_payment_history(
    limit: int = 50, offset: int = 0, current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Get payment history for the authenticated user

    Args:
        limit: Maximum number of payments to return
        offset: Number of payments to skip
        current_user: Authenticated user

    Returns:
        List of user's payment records
    """
    try:
        user_id = current_user["id"]
        payments = get_user_payments(user_id, limit=limit, offset=offset)

        return {
            "payments": [
                {
                    "id": payment["id"],
                    "amount": payment.get("amount_usd", payment.get("amount", 0)),
                    "currency": payment["currency"],
                    "status": payment["status"],
                    "payment_method": payment["payment_method"],
                    "stripe_payment_intent_id": payment.get("stripe_payment_intent_id"),
                    "created_at": payment["created_at"],
                    "completed_at": payment.get("completed_at"),
                    "metadata": payment.get("metadata", {}),
                }
                for payment in payments
            ],
            "total": len(payments),
            "limit": limit,
            "offset": offset,
        }

    except Exception as e:
        logger.error(f"Error getting payment history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/payments/{payment_id}")
async def get_payment_details(
    payment_id: int, current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Get details of a specific payment

    Args:
        payment_id: Payment record ID
        current_user: Authenticated user

    Returns:
        Payment details
    """
    try:
        payment = get_payment(payment_id)

        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        # Verify payment belongs to user
        if payment["user_id"] != current_user["id"]:
            raise HTTPException(
                status_code=403, detail="You don't have permission to view this payment"
            )

        return {
            "id": payment["id"],
            "amount": payment.get("amount_usd", payment.get("amount", 0)),
            "currency": payment["currency"],
            "status": payment["status"],
            "payment_method": payment["payment_method"],
            "stripe_payment_intent_id": payment.get("stripe_payment_intent_id"),
            "stripe_session_id": payment.get("stripe_checkout_session_id")
            or payment.get("stripe_session_id"),
            "stripe_customer_id": payment.get("stripe_customer_id"),
            "created_at": payment["created_at"],
            "updated_at": payment.get("updated_at"),
            "completed_at": payment.get("completed_at"),
            "failed_at": payment.get("failed_at"),
            "metadata": payment.get("metadata", {}),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting payment details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Subscription Checkout ====================


@router.post("/subscription-checkout", response_model=dict[str, Any])
async def create_subscription_checkout(
    request: CreateSubscriptionCheckoutRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Create a Stripe checkout session for subscription

    This endpoint creates a Stripe-hosted checkout page for recurring subscriptions.
    After payment, Stripe redirects to success_url or cancel_url.

    Args:
        request: Subscription checkout parameters (price_id, product_id, URLs)
        current_user: Authenticated user from token

    Returns:
        Checkout session with URL and session ID

    Example request body:
    {
        "price_id": "price_1SNk2KLVT8n4vaEn7lHNPYWB",
        "product_id": "prod_TKOqQPhVRxNp4Q",
        "customer_email": "user@example.com",
        "success_url": "https://beta.gatewayz.ai/settings/credits?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": "https://beta.gatewayz.ai/settings/credits",
        "mode": "subscription"
    }

    Example response:
    {
        "session_id": "cs_test_xxxxx",
        "url": "https://checkout.stripe.com/pay/cs_test_xxxxx",
        "customer_id": "cus_xxxxx",
        "status": "open"
    }
    """
    try:
        user_id = current_user["id"]
        logger.info(
            f"Creating subscription checkout for user {user_id}, price_id: {request.price_id}, product_id: {request.product_id}"
        )

        session = stripe_service.create_subscription_checkout(user_id=user_id, request=request)

        logger.info(
            f"Subscription checkout session created for user {user_id}: {session.session_id}"
        )

        return {
            "session_id": session.session_id,
            "url": session.url,
            "customer_id": session.customer_id,
            "status": session.status,
        }

    except ValueError as e:
        logger.error(f"Validation error creating subscription checkout: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Error creating subscription checkout: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to create subscription checkout: {str(e)}"
        )


# ==================== Subscription Management ====================


@router.get("/subscription", response_model=dict[str, Any])
async def get_current_subscription(
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Get the current user's subscription status

    Returns:
        Current subscription details including tier, status, and billing period

    Example response:
    {
        "has_subscription": true,
        "subscription_id": "sub_xxxxx",
        "status": "active",
        "tier": "pro",
        "current_period_start": "2024-01-01T00:00:00Z",
        "current_period_end": "2024-02-01T00:00:00Z",
        "cancel_at_period_end": false,
        "product_id": "prod_TKOqQPhVRxNp4Q",
        "price_id": "price_xxxxx"
    }
    """
    try:
        user_id = current_user["id"]
        subscription = stripe_service.get_current_subscription(user_id)

        return {
            "has_subscription": subscription.has_subscription,
            "subscription_id": subscription.subscription_id,
            "status": subscription.status,
            "tier": subscription.tier,
            "current_period_start": (
                subscription.current_period_start.isoformat()
                if subscription.current_period_start
                else None
            ),
            "current_period_end": (
                subscription.current_period_end.isoformat()
                if subscription.current_period_end
                else None
            ),
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "canceled_at": (
                subscription.canceled_at.isoformat() if subscription.canceled_at else None
            ),
            "product_id": subscription.product_id,
            "price_id": subscription.price_id,
        }

    except Exception as e:
        logger.error(f"Error getting subscription: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get subscription: {str(e)}")


@router.post("/subscription/upgrade", response_model=dict[str, Any])
async def upgrade_subscription(
    request: UpgradeSubscriptionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Upgrade subscription to a higher tier (e.g., Pro -> Max)

    This uses Stripe's subscription update with proration to charge the difference immediately.

    Args:
        request: Upgrade request with new price/product IDs
        current_user: Authenticated user from token

    Returns:
        Upgrade result with new subscription status

    Example request body:
    {
        "new_price_id": "price_xxxxx",
        "new_product_id": "prod_TKOraBpWMxMAIu",
        "proration_behavior": "create_prorations"
    }

    Example response:
    {
        "success": true,
        "subscription_id": "sub_xxxxx",
        "status": "active",
        "current_tier": "max",
        "message": "Successfully upgraded to max tier"
    }
    """
    try:
        user_id = current_user["id"]
        logger.info(
            f"Upgrading subscription for user {user_id} to product {request.new_product_id}"
        )

        result = stripe_service.upgrade_subscription(user_id=user_id, request=request)

        return {
            "success": result.success,
            "subscription_id": result.subscription_id,
            "status": result.status,
            "current_tier": result.current_tier,
            "message": result.message,
            "effective_date": result.effective_date.isoformat() if result.effective_date else None,
            "proration_amount": result.proration_amount,
        }

    except ValueError as e:
        logger.error(f"Validation error upgrading subscription: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Error upgrading subscription: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to upgrade subscription: {str(e)}")


@router.post("/subscription/downgrade", response_model=dict[str, Any])
async def downgrade_subscription(
    request: DowngradeSubscriptionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Downgrade subscription to a lower tier (e.g., Max -> Pro)

    This uses Stripe's subscription update with proration to credit the unused time.

    Args:
        request: Downgrade request with new price/product IDs
        current_user: Authenticated user from token

    Returns:
        Downgrade result with new subscription status

    Example request body:
    {
        "new_price_id": "price_xxxxx",
        "new_product_id": "prod_TKOqQPhVRxNp4Q",
        "proration_behavior": "create_prorations"
    }

    Example response:
    {
        "success": true,
        "subscription_id": "sub_xxxxx",
        "status": "active",
        "current_tier": "pro",
        "message": "Successfully downgraded to pro tier. Credit applied for unused time."
    }
    """
    try:
        user_id = current_user["id"]
        logger.info(
            f"Downgrading subscription for user {user_id} to product {request.new_product_id}"
        )

        result = stripe_service.downgrade_subscription(user_id=user_id, request=request)

        return {
            "success": result.success,
            "subscription_id": result.subscription_id,
            "status": result.status,
            "current_tier": result.current_tier,
            "message": result.message,
            "effective_date": result.effective_date.isoformat() if result.effective_date else None,
            "proration_amount": result.proration_amount,
        }

    except ValueError as e:
        logger.error(f"Validation error downgrading subscription: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Error downgrading subscription: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to downgrade subscription: {str(e)}")


@router.post("/subscription/cancel", response_model=dict[str, Any])
async def cancel_subscription(
    request: CancelSubscriptionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """
    Cancel subscription

    By default, cancels at the end of the billing period (user keeps access until then).
    Set cancel_at_period_end=false for immediate cancellation.

    Args:
        request: Cancel request with options
        current_user: Authenticated user from token

    Returns:
        Cancellation result

    Example request body (cancel at period end):
    {
        "cancel_at_period_end": true,
        "reason": "Switching to another service"
    }

    Example response:
    {
        "success": true,
        "subscription_id": "sub_xxxxx",
        "status": "cancel_scheduled",
        "current_tier": "pro",
        "message": "Subscription will be canceled at the end of the billing period...",
        "effective_date": "2024-02-01T00:00:00Z"
    }
    """
    try:
        user_id = current_user["id"]
        logger.info(
            f"Canceling subscription for user {user_id} "
            f"(cancel_at_period_end: {request.cancel_at_period_end})"
        )

        result = stripe_service.cancel_subscription(user_id=user_id, request=request)

        return {
            "success": result.success,
            "subscription_id": result.subscription_id,
            "status": result.status,
            "current_tier": result.current_tier,
            "message": result.message,
            "effective_date": result.effective_date.isoformat() if result.effective_date else None,
        }

    except ValueError as e:
        logger.error(f"Validation error canceling subscription: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"Error canceling subscription: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to cancel subscription: {str(e)}")
