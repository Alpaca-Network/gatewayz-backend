#!/usr/bin/env python3
"""
Enhanced Notification Service with Professional Email Templates
Adds welcome emails, password reset, usage reports, and more
"""

import logging
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

try:
    import resend  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - handled in send_email_notification
    resend = None

import src.config.supabase_config as supabase_config
from src.services.professional_email_templates import email_templates

logger = logging.getLogger(__name__)


class EnhancedNotificationService:
    """Enhanced notification service with professional email templates"""

    def __init__(self):
        try:
            self.supabase = supabase_config.get_supabase_client()
        except Exception as exc:
            logger.warning("Supabase client unavailable during notification service init: %s", exc)
            self.supabase = None
        self.resend_api_key = os.environ.get("RESEND_API_KEY")
        self.from_email = os.environ.get("FROM_EMAIL", "noreply@yourdomain.com")
        self.app_name = os.environ.get("APP_NAME", "AI Gateway")
        self.app_url = os.environ.get("APP_URL", "https://gatewayz.ai")

        # Initialize Resend client if dependency is available
        self.email_client_available = bool(self.resend_api_key and resend is not None)
        if self.email_client_available:
            resend.api_key = self.resend_api_key  # type: ignore[union-attr]
        elif self.resend_api_key:
            logger.warning(
                "RESEND_API_KEY configured but 'resend' package is not installed. "
                "Email notifications will be disabled."
            )

        # Rate limiting for Resend API (2 requests per second limit)
        # We use 0.6 seconds (600ms) between requests to safely stay under 2/sec
        self._email_rate_limit_lock = threading.Lock()
        self._last_email_send_time = 0.0
        self._min_email_interval = 0.6  # seconds between email sends

    def _is_valid_email_for_sending(self, email: str) -> bool:
        """Check if email is valid for sending (not a Privy fallback placeholder)."""
        if not email:
            return False
        # Reject Privy fallback emails that aren't real email addresses
        if email.endswith("@privy.user"):
            return False
        # Reject new placeholder format used for database storage when no valid email exists
        # These are RFC-valid but not deliverable (e.g., noemail+xxx@privy.placeholder)
        if email.endswith("@privy.placeholder"):
            return False
        # Basic email format check
        if "@" not in email or "." not in email.split("@")[-1]:
            return False
        return True

    def send_welcome_email(self, user_id: int, username: str, email: str, credits: int) -> bool:
        """Send welcome email to new users (API key not included for security)"""
        try:
            # Skip sending for invalid/placeholder emails
            if not self._is_valid_email_for_sending(email):
                logger.info(
                    f"Skipping welcome email for user {user_id}: "
                    f"'{email}' is a placeholder email (e.g., Privy fallback)"
                )
                return True  # Return True to prevent retry loops

            logger.info(f"Enhanced notification service - sending welcome email to: {email}")
            logger.info(f"Resend API key available: {bool(self.resend_api_key)}")
            logger.info(f"From email: {self.from_email}")
            logger.info(f"App name: {self.app_name}")

            # Use the simple welcome email template
            template = email_templates.simple_welcome_email(username, email, credits)
            logger.info("Email template generated successfully")

            success = self.send_email_notification(
                to_email=email,
                subject=template["subject"],
                html_content=template["html"],
                text_content=template["text"],
            )

            logger.info(f"Email notification result: {success}")

            if success:
                logger.info(f"Welcome email sent to {email}")

            return success
        except Exception as e:
            logger.error(f"Error sending welcome email: {e}")
            logger.error(f"Error details: {str(e)}", exc_info=True)
            return False

    def send_welcome_email_if_needed(
        self, user_id: int, username: str, email: str, credits: int
    ) -> bool:
        """Send welcome email only if the user hasn't received one yet"""
        try:
            # Check if user has already received a welcome email
            client = self.supabase or supabase_config.get_supabase_client()
            user_result = (
                client.table("users").select("welcome_email_sent").eq("id", user_id).execute()
            )

            if not user_result.data:
                logger.warning(f"User {user_id} not found, skipping welcome email")
                return False

            user_data = user_result.data[0]
            welcome_email_sent = user_data.get("welcome_email_sent", False)

            if welcome_email_sent:
                logger.info(f"User {user_id} has already received welcome email, skipping")
                return True

            # Send welcome email
            success = self.send_welcome_email(user_id, username, email, credits)

            if success:
                # Mark welcome email as sent
                from src.db.users import mark_welcome_email_sent

                mark_welcome_email_sent(user_id)
                logger.info(f"Welcome email sent and marked as sent for user {user_id}")

            return success

        except Exception as e:
            logger.error(f"Error checking/sending welcome email: {e}")
            return False

    def send_password_reset_email(self, user_id: int, username: str, email: str) -> str | None:
        """Send password reset email and return reset token"""
        try:
            # Validate email BEFORE creating token to prevent orphaned tokens
            if not self._is_valid_email_for_sending(email):
                logger.warning(
                    f"Skipping password reset for invalid email: {email}. "
                    "No token will be created."
                )
                return None

            # Generate reset token
            reset_token = secrets.token_urlsafe(32)

            # Store token in database with expiration
            expires_at = datetime.now(UTC) + timedelta(hours=1)
            client = self.supabase or supabase_config.get_supabase_client()
            client.table("password_reset_tokens").insert(
                {
                    "user_id": user_id,
                    "token": reset_token,
                    "expires_at": expires_at.isoformat(),
                    "used": False,
                }
            ).execute()

            # Send email
            template = email_templates.password_reset_email(username, email, reset_token)

            success = self.send_email_notification(
                to_email=email,
                subject=template["subject"],
                html_content=template["html"],
                text_content=template["text"],
            )

            if success:
                logger.info(f"Password reset email sent to {email}")
                return reset_token
            else:
                # If email failed after token creation, we should clean up the token
                logger.error(
                    f"Failed to send password reset email to {email}, "
                    "but token was already created. Consider cleanup."
                )
                return None

        except Exception as e:
            logger.error(f"Error sending password reset email: {e}")
            return None

    def send_plan_upgrade_confirmation(
        self,
        user_id: int,
        username: str,
        email: str,
        old_plan: str,
        new_plan: str,
        effective_date: str,
    ) -> bool:
        """Send plan upgrade confirmation email"""
        try:
            content = f"""
                <h2>🎉 Plan Upgraded Successfully!</h2>
                <p>Hi <strong>{username}</strong>,</p>
                <p>Congratulations! Your plan has been successfully upgraded.</p>

                <div class="success-box">
                    <h3 style="margin-bottom: 12px; color: #065f46;">Upgrade Details</h3>
                    <div class="info-grid">
                        <div class="info-item">
                            <div class="label">Previous Plan</div>
                            <div class="value">{old_plan}</div>
                        </div>
                        <div class="info-item">
                            <div class="label">New Plan</div>
                            <div class="value">{new_plan}</div>
                        </div>
                        <div class="info-item">
                            <div class="label">Effective Date</div>
                            <div class="value">{effective_date}</div>
                        </div>
                    </div>
                </div>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{self.app_url}/settings/credits" class="cta-button">📊 View Dashboard</a>
                    <a href="{self.app_url}/billing" class="cta-button secondary-button">💳 Billing Details</a>
                </div>

                <p>You now have access to all the features of your new plan. If you have any questions, contact our support team at <a href="mailto:{self.from_email}" style="color: #3b82f6;">{self.from_email}</a>.</p>
            """

            subject = f"Plan upgraded to {new_plan} - Welcome to your new features! 🚀"

            success = self.send_email_notification(
                to_email=email,
                subject=subject,
                html_content=email_templates.get_base_template().format(
                    subject="Plan Upgrade Confirmation",
                    header_subtitle="Welcome to your new plan",
                    content=content,
                    app_name=self.app_name,
                    app_url=self.app_url,
                    support_email=self.from_email,
                    email=email,
                ),
                text_content=f"""Plan Upgraded Successfully - {self.app_name}

Hi {username},

Congratulations! Your plan has been successfully upgraded.

Previous Plan: {old_plan}
New Plan: {new_plan}
Effective Date: {effective_date}

You now have access to all the features of your new plan.

Questions? Contact us: {self.from_email}

Best regards,
The {self.app_name} Team
""",
            )

            if success:
                logger.info(f"Plan upgrade confirmation sent to {email}")

            return success
        except Exception as e:
            logger.error(f"Error sending plan upgrade confirmation: {e}")
            return False

    def send_api_key_created_email(
        self, user_id: int, username: str, email: str, api_key: str, key_name: str
    ) -> bool:
        """Send email when new API key is created"""
        try:
            content = f"""
                <h2>🔑 New API Key Created</h2>
                <p>Hi <strong>{username}</strong>,</p>
                <p>A new API key has been created for your account.</p>

                <div class="highlight-box">
                    <h3 style="margin-bottom: 12px;">API Key Details</h3>
                    <div class="info-grid">
                        <div class="info-item">
                            <div class="label">Key Name</div>
                            <div class="value">{key_name}</div>
                        </div>
                        <div class="info-item">
                            <div class="label">Created</div>
                            <div class="value">{datetime.now(UTC).strftime('%Y-%m-%d %H:%M timezone.utc')}</div>
                        </div>
                    </div>
                    <p style="margin-bottom: 12px; margin-top: 16px;">Your new API key:</p>
                    <div class="api-key-box">{api_key}</div>
                    <p style="font-size: 14px; color: #6b7280; margin-top: 12px;">⚠️ Keep this key secure and never share it publicly.</p>
                </div>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{self.app_url}/settings/credits" class="cta-button">📊 Manage API Keys</a>
                    <a href="{self.app_url}/docs" class="cta-button secondary-button">📚 API Documentation</a>
                </div>

                <p>If you didn't create this API key, please contact our support team immediately at <a href="mailto:{self.from_email}" style="color: #3b82f6;">{self.from_email}</a>.</p>
            """

            subject = f"New API key '{key_name}' created - {self.app_name}"

            success = self.send_email_notification(
                to_email=email,
                subject=subject,
                html_content=email_templates.get_base_template().format(
                    subject="New API Key Created",
                    header_subtitle="Secure your new key",
                    content=content,
                    app_name=self.app_name,
                    app_url=self.app_url,
                    support_email=self.from_email,
                    email=email,
                ),
                text_content=f"""New API Key Created - {self.app_name}

Hi {username},

A new API key has been created for your account.

Key Name: {key_name}
Created: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M timezone.utc')}

API Key: {api_key}

Keep this key secure and never share it publicly.

If you didn't create this API key, please contact our support team immediately at {self.from_email}.

Best regards,
The {self.app_name} Team
""",
            )

            if success:
                logger.info(f"API key creation email sent to {email}")

            return success
        except Exception as e:
            logger.error(f"Error sending API key creation email: {e}")
            return False

    def _wait_for_rate_limit(self):
        """
        Ensure we respect Resend's rate limit of 2 requests per second.
        This method blocks until enough time has passed since the last email send.
        """
        with self._email_rate_limit_lock:
            current_time = time.time()
            time_since_last_send = current_time - self._last_email_send_time

            if time_since_last_send < self._min_email_interval:
                sleep_time = self._min_email_interval - time_since_last_send
                logger.debug(
                    f"Rate limiting: sleeping for {sleep_time:.3f}s to respect "
                    f"Resend API limit (2 req/sec)"
                )
                time.sleep(sleep_time)

            # Update last send time
            self._last_email_send_time = time.time()

    def _is_valid_email(self, email: str) -> bool:
        """
        Check if an email address is valid and deliverable.
        Rejects placeholder emails like Privy user IDs (did:privy:xxx@privy.user).
        """
        if not email or "@" not in email:
            return False

        # Reject Privy placeholder emails (did:privy:xxx@privy.user or xxx@privy.user)
        if email.endswith("@privy.user"):
            return False

        # Reject new placeholder format used for database storage when no valid email exists
        # These are RFC-valid but not deliverable (e.g., noemail+xxx@privy.placeholder)
        if email.endswith("@privy.placeholder"):
            return False

        # Reject emails with did: prefix (Privy decentralized identifiers)
        if email.startswith("did:"):
            return False

        # Basic email format validation
        # Must have: local-part@domain, domain must have at least one dot
        try:
            local_part, domain = email.rsplit("@", 1)
            if not local_part or not domain:
                return False
            if "." not in domain:
                return False
            # Domain must have valid TLD (at least 2 chars)
            # Also ensure all domain parts are non-empty (reject ".com", "a..b.com", etc.)
            domain_parts = domain.split(".")
            if len(domain_parts[-1]) < 2:
                return False
            # Check all domain parts are non-empty (catches domains starting with dot)
            if any(part == "" for part in domain_parts):
                return False
            return True
        except ValueError:
            return False

    def send_email_notification(
        self, to_email: str, subject: str, html_content: str, text_content: str = None
    ) -> bool:
        """Send email notification using Resend SDK (if available)"""
        try:
            # Validate email before attempting to send
            if not self._is_valid_email_for_sending(to_email):
                logger.info(f"Skipping email to '{to_email}': invalid or placeholder email address")
                return True  # Return True to avoid error logging for expected cases

            logger.info(f"Attempting to send email to: {to_email}")
            logger.info(f"Subject: {subject}")
            logger.info(f"Email client available: {self.email_client_available}")
            logger.info(f"From email: {self.from_email}")

            # Validate email address before attempting to send
            if not self._is_valid_email(to_email):
                logger.warning(
                    f"Skipping email to invalid/placeholder address: {to_email}. "
                    "User may need to provide a real email address."
                )
                return False

            if not self.email_client_available:
                logger.warning(
                    "❌ Email client is unavailable (missing dependency or API key). "
                    "Skipping email notification."
                )
                return False

            # Ensure API key is set before each send (in case it changed)
            resend.api_key = self.resend_api_key  # type: ignore[union-attr]

            # Apply rate limiting to respect Resend's 2 requests/second limit
            self._wait_for_rate_limit()

            # Use Resend SDK
            logger.info("Sending email via Resend SDK...")
            response = resend.Emails.send(
                {
                    "from": self.from_email,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_content,
                    "text": text_content,
                }
            )

            logger.info(f"Resend response: {response}")

            if response.get("id"):
                logger.info(f"✅ Email sent successfully to {to_email}, ID: {response['id']}")
                return True
            else:
                logger.error(f"❌ Failed to send email to {to_email}: {response}")
                return False

        except Exception as e:
            logger.error(f"❌ Error sending email to {to_email}: {e}")
            logger.error(f"Error details: {str(e)}", exc_info=True)
            return False

# Global enhanced notification service instance
enhanced_notification_service = EnhancedNotificationService()
