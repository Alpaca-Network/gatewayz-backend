#!/usr/bin/env python3
"""
Stripe Service
Handles all Stripe payment operations
"""

import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone, timedelta

import stripe


# Import Stripe SDK with alias to avoid conflict with schema module

from src.db.payments import (
    create_payment,
    update_payment_status,
    get_payment_by_stripe_intent
)
from src.db.users import get_user_by_id, add_credits_to_user
from src.schemas.payments import CreateCheckoutSessionRequest, CheckoutSessionResponse, StripeCurrency, \
    CreatePaymentIntentRequest, PaymentIntentResponse, WebhookProcessingResult, CreditPackagesResponse, CreditPackage, \
    RefundResponse, CreateRefundRequest, PaymentStatus

logger = logging.getLogger(__name__)



class StripeService:
    """Service class for handling Stripe payment operations"""

    def __init__(self):
        """Initialize Stripe with API key from environment"""
        self.api_key = os.getenv('STRIPE_SECRET_KEY')
        self.webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
        self.publishable_key = os.getenv('STRIPE_PUBLISHABLE_KEY')

        if not self.api_key:
            raise ValueError("STRIPE_SECRET_KEY not found in environment variables")

        # Set Stripe API key
        stripe.api_key = self.api_key

        # Configuration
        self.default_currency = StripeCurrency.USD
        self.min_amount = 50  # $0.50 minimum
        self.max_amount = 99999999  # ~$1M maximum
        self.frontend_url = os.getenv('FRONTEND_URL', 'https://gatewayz.ai')

        logger.info("Stripe service initialized")

    @staticmethod
    def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    @staticmethod
    def _get_metadata(obj: Any) -> Dict[str, Any]:
        metadata = StripeService._get_attr(obj, 'metadata', {}) or {}
        if isinstance(metadata, dict):
            return metadata
        try:
            return dict(metadata)
        except Exception:
            return {}

    # ==================== Checkout Sessions ====================

    def create_checkout_session(
            self,
            user_id: int,
            request: CreateCheckoutSessionRequest
    ) -> CheckoutSessionResponse:
        """Create a Stripe checkout session"""
        try:
            # Get user details
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            # Extract real email if stored email is a Privy DID
            user_email = user.get('email', '')
            if user_email.startswith('did:privy:'):
                logger.warning(f"User {user_id} has Privy DID as email: {user_email}")
                if request.customer_email:
                    user_email = request.customer_email
                else:
                    # Try to get email from Privy linked accounts via Supabase
                    from src.config.supabase_config import get_supabase_client
                    client = get_supabase_client()
                    user_result = client.table('users').select('privy_user_id').eq('id', user_id).execute()
                    if user_result.data and user_result.data[0].get('privy_user_id'):
                        privy_user_id = user_result.data[0]['privy_user_id']
                        logger.info(f"Found privy_user_id for user {user_id}: {privy_user_id}")
                        user_email = None  # Without Privy token we cannot fetch real email yet
                        logger.warning(f"No customer_email in request for user {user_id} with Privy DID")
                    else:
                        user_email = None

            # Create payment record
            payment = create_payment(
                user_id=user_id,
                amount=request.amount / 100,  # Convert cents to dollars
                currency=request.currency.value,
                payment_method="stripe",
                status="pending",
                metadata={
                    "description": request.description,
                    **(request.metadata or {})
                }
            )

            if not payment:
                raise Exception("Failed to create payment record")

            # Prepare URLs - ALWAYS use request URLs if provided
            success_url = request.success_url if request.success_url else f"{self.frontend_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
            cancel_url = request.cancel_url if request.cancel_url else f"{self.frontend_url}/payment/cancel"

            logger.info(f"=== CHECKOUT SESSION URL DEBUG ===")
            logger.info(f"Frontend URL from env: {self.frontend_url}")
            logger.info(f"Request success_url: {request.success_url}")
            logger.info(f"Request cancel_url: {request.cancel_url}")
            logger.info(f"Final success_url being sent to Stripe: {success_url}")
            logger.info(f"Final cancel_url being sent to Stripe: {cancel_url}")
            logger.info(f"=== END URL DEBUG ===")

            # Calculate credits
            credits = request.amount

            # Create Stripe checkout session
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': request.currency.value,
                        'unit_amount': request.amount,
                        'product_data': {
                            'name': 'Gatewayz Credits',
                            'description': f'{credits:,} credits for your account',
                        },
                    },
                    'quantity': 1,
                }],
                mode='payment',
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=request.customer_email or user_email,
                client_reference_id=str(user_id),
                metadata={
                    'user_id': str(user_id),
                    'payment_id': str(payment['id']),
                    'credits': str(credits),
                    **(request.metadata or {})
                },
                expires_at=int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())
            )

            # Update payment with session ID
            update_payment_status(
                payment_id=payment['id'],
                status='pending',
                stripe_payment_intent_id=session.id
            )

            logger.info(f"Checkout session created: {session.id} for user {user_id}")

            return CheckoutSessionResponse(
                session_id=session.id,
                url=session.url,
                payment_id=payment['id'],
                status=PaymentStatus.PENDING,
                amount=request.amount,
                currency=request.currency.value,
                expires_at=datetime.fromtimestamp(session.expires_at, tz=timezone.utc)
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            raise Exception(f"Payment processing error: {str(e)}")

        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            raise

    def retrieve_checkout_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieve checkout session details"""
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            return {
                'id': session.id,
                'payment_status': session.payment_status,
                'status': session.status,
                'amount_total': session.amount_total,
                'currency': session.currency,
                'customer_email': session.customer_email,
                'payment_intent': session.payment_intent,
                'metadata': session.metadata
            }
        except stripe.StripeError as e:
            logger.error(f"Error retrieving checkout session: {e}")
            raise Exception(f"Failed to retrieve session: {str(e)}")

    # ==================== Payment Intents ====================

    def create_payment_intent(
            self,
            user_id: int,
            request: CreatePaymentIntentRequest
    ) -> PaymentIntentResponse:
        """Create a Stripe payment intent"""
        try:
            user = get_user_by_id(user_id)
            if not user:
                raise ValueError(f"User {user_id} not found")

            payment = create_payment(
                user_id=user_id,
                amount=request.amount / 100,
                currency=request.currency.value,
                payment_method="stripe",
                status="pending",
                metadata={"description": request.description, **(request.metadata or {})}
            )

            intent_params = {
                'amount': request.amount,
                'currency': request.currency.value,
                'metadata': {
                    'user_id': str(user_id),
                    'payment_id': str(payment['id']),
                    'credits': str(request.amount),
                    **(request.metadata or {})
                },
                'description': request.description,
            }

            if request.automatic_payment_methods:
                intent_params['automatic_payment_methods'] = {'enabled': True}
            else:
                intent_params['payment_method_types'] = [
                    pm.value for pm in request.payment_method_types
                ]

            intent = stripe.PaymentIntent.create(**intent_params)

            update_payment_status(
                payment_id=payment['id'],
                status='pending',
                stripe_payment_intent_id=intent.id
            )

            logger.info(f"Payment intent created: {intent.id} for user {user_id}")

            return PaymentIntentResponse(
                payment_intent_id=intent.id,
                client_secret=intent.client_secret,
                payment_id=payment['id'],
                status=PaymentStatus(intent.status),
                amount=intent.amount,
                currency=intent.currency,
                next_action=intent.next_action
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error creating payment intent: {e}")
            raise Exception(f"Payment processing error: {str(e)}")

        except Exception as e:
            logger.error(f"Error creating payment intent: {e}")
            raise

    def retrieve_payment_intent(self, payment_intent_id: str) -> Dict[str, Any]:
        """Retrieve payment intent details"""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            return {
                'id': intent.id,
                'status': intent.status,
                'amount': intent.amount,
                'currency': intent.currency,
                'customer': intent.customer,
                'payment_method': intent.payment_method,
                'metadata': intent.metadata
            }
        except stripe.StripeError as e:
            logger.error(f"Error retrieving payment intent: {e}")
            raise Exception(f"Failed to retrieve payment intent: {str(e)}")

    # ==================== Webhooks ====================

    def handle_webhook(self, payload: bytes, signature: str) -> WebhookProcessingResult:
        """Handle Stripe webhook events"""
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.webhook_secret
            )

            logger.info(f"Processing webhook: {event['type']}")

            if event['type'] == 'checkout.session.completed':
                self._handle_checkout_completed(event['data']['object'])
            elif event['type'] == 'payment_intent.succeeded':
                self._handle_payment_succeeded(event['data']['object'])
            elif event['type'] == 'payment_intent.payment_failed':
                self._handle_payment_failed(event['data']['object'])

            return WebhookProcessingResult(
                success=True,
                event_type=event['type'],
                event_id=event['id'],
                message=f"Event {event['type']} processed successfully",
                processed_at=datetime.now(timezone.utc)
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
            metadata = self._get_metadata(session)
            user_id = int(metadata.get('user_id', 0) or 0)
            payment_id = int(metadata.get('payment_id', 0) or 0)
            credits_raw = metadata.get('credits', 0) or 0
            credits = float(credits_raw)

            if not user_id or not payment_id:
                raise ValueError("Missing required metadata for checkout session")

            amount_dollars = credits / 100  # credits metadata is stored in cents

            session_id = self._get_attr(session, 'id')
            payment_intent_id = self._get_attr(session, 'payment_intent')

            # Add credits and log transaction
            add_credits_to_user(
                user_id=user_id,
                credits=amount_dollars,
                transaction_type='purchase',
                description=f"Stripe checkout - ${amount_dollars}",
                payment_id=payment_id,
                metadata={
                    'stripe_session_id': session_id,
                    'stripe_payment_intent_id': payment_intent_id
                }
            )

            # Update payment
            update_payment_status(
                payment_id=payment_id,
                status='completed',
                stripe_payment_intent_id=payment_intent_id
            )

            logger.info(f"Checkout completed: Added {amount_dollars} credits to user {user_id}")

            # Check for referral bonus (first purchase of $10+)
            try:
                from src.services.referral import apply_referral_bonus, mark_first_purchase
                from src.config.supabase_config import get_supabase_client

                client = get_supabase_client()
                user_result = client.table('users').select('*').eq('id', user_id).execute()

                if user_result.data:
                    user = user_result.data[0]
                    has_made_first_purchase = user.get('has_made_first_purchase', False)
                    referred_by_code = user.get('referred_by_code')

                    # Apply referral bonus if:
                    # 1. This is first purchase
                    # 2. User was referred by someone
                    # 3. Purchase is $10 or more
                    if not has_made_first_purchase and referred_by_code and amount_dollars >= 10.0:
                        success, error_msg, bonus_data = apply_referral_bonus(
                            user_id=user_id,
                            referral_code=referred_by_code,
                            purchase_amount=amount_dollars
                        )

                        if success:
                            logger.info(
                                f"Referral bonus applied! User {user_id} and referrer both received "
                                f"${bonus_data['user_bonus']} (code: {referred_by_code})"
                            )
                        else:
                            logger.warning(f"Failed to apply referral bonus for user {user_id}: {error_msg}")

                    # Mark first purchase regardless of referral
                    if not has_made_first_purchase:
                        mark_first_purchase(user_id)

            except Exception as referral_error:
                # Don't fail the payment if referral bonus fails
                logger.error(f"Error processing referral bonus: {referral_error}", exc_info=True)

        except Exception as e:
            logger.error(f"Error handling checkout completed: {e}")
            raise

    def _handle_payment_succeeded(self, payment_intent):
        """Handle successful payment"""
        try:
            intent_id = self._get_attr(payment_intent, 'id')
            if not intent_id:
                raise ValueError("Missing payment intent id")

            payment = get_payment_by_stripe_intent(intent_id)
            if payment:
                update_payment_status(
                    payment_id=payment['id'],
                    status='completed'
                )
                # Add credits and log transaction
                amount = payment.get('amount_usd', payment.get('amount', 0))
                add_credits_to_user(
                    user_id=payment['user_id'],
                    credits=amount,
                    transaction_type='purchase',
                    description=f"Stripe payment - ${amount}",
                    payment_id=payment['id'],
                    metadata={'stripe_payment_intent_id': intent_id}
                )
                logger.info(f"Payment succeeded: {intent_id}")
        except Exception as e:
            logger.error(f"Error handling payment succeeded: {e}")

    def _handle_payment_failed(self, payment_intent):
        """Handle failed payment"""
        try:
            intent_id = self._get_attr(payment_intent, 'id')
            if not intent_id:
                raise ValueError("Missing payment intent id")

            payment = get_payment_by_stripe_intent(intent_id)
            if payment:
                update_payment_status(
                    payment_id=payment['id'],
                    status='failed'
                )
                logger.info(f"Payment failed: {intent_id}")
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
                features=["1,000 credits", "~100,000 tokens", "Valid for 30 days"]
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
                features=["5,000 credits", "~500,000 tokens", "10% discount", "Valid for 90 days"]
            ),
        ]

        return CreditPackagesResponse(
            packages=packages,
            currency=StripeCurrency.USD
        )

    # ==================== Refunds ====================

    def create_refund(self, request: CreateRefundRequest) -> RefundResponse:
        """Create a refund"""
        try:
            refund = stripe.Refund.create(
                payment_intent=request.payment_intent_id,
                amount=request.amount,
                reason=request.reason
            )

            return RefundResponse(
                refund_id=refund.id,
                payment_intent_id=refund.payment_intent,
                amount=refund.amount,
                currency=refund.currency,
                status=refund.status,
                reason=refund.reason,
                created_at=datetime.fromtimestamp(refund.created, tz=timezone.utc)
            )

        except stripe.StripeError as e:
            logger.error(f"Stripe error creating refund: {e}")
            raise Exception(f"Refund failed: {str(e)}")
