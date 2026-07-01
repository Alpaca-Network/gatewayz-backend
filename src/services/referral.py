import logging
import secrets
import string
from datetime import UTC, datetime
from typing import Any

from src.config.supabase_config import get_supabase_client
from src.constants import SETTINGS_CREDITS_URL

logger = logging.getLogger(__name__)


def send_referral_signup_notification(
    referrer_id: int, referrer_email: str, referrer_username: str, referee_username: str
) -> bool:
    """Send email notification to referrer when someone signs up with their code"""
    try:
        from src.enhanced_notification_service import enhanced_notification_service

        subject = "Someone used your referral code! - AI Gateway"

        content = f"""
            <h2>🎉 Great News!</h2>
            <p>Hi <strong>{referrer_username}</strong>,</p>
            <p><strong>{referee_username}</strong> just signed up using your referral code!</p>

            <div class="highlight-box" style="background-color: #f0f9ff; border-left: 4px solid #3b82f6; padding: 16px; margin: 20px 0;">
                <h3 style="margin-bottom: 12px; color: #1e40af;">Thanks for spreading the word!</h3>
                <p style="margin-bottom: 8px;">We've linked <strong>{referee_username}</strong> to your referral code.</p>
            </div>

            <p>Keep sharing your referral code with friends!</p>
        """

        from src.services.professional_email_templates import email_templates

        html_content = email_templates.get_base_template().format(
            subject="New Referral Signup!",
            header_subtitle="Someone used your code",
            content=content,
            app_name="AI Gateway",
            app_url="https://gatewayz.ai",
            support_email="noreply@gatewayz.ai",
            email=referrer_email,
        )

        text_content = f"""New Referral Signup - AI Gateway

Hi {referrer_username},

{referee_username} just signed up using your referral code!

Thanks for spreading the word - we've linked {referee_username} to your referral code.

Keep sharing your referral code with friends!

Best regards,
The AI Gateway Team
"""

        logger.info(
            f"Attempting to send referral signup notification to user {referrer_id} "
            f"at email {referrer_email}"
        )

        success = enhanced_notification_service.send_email_notification(
            to_email=referrer_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )

        if success:
            logger.info(
                f"Successfully sent referral signup notification to user {referrer_id} "
                f"at email {referrer_email}"
            )
        else:
            logger.warning(
                f"Failed to send referral signup notification to user {referrer_id} "
                f"at email {referrer_email} - email service returned False"
            )

        return success

    except Exception as e:
        logger.error(f"Error sending referral signup notification: {e}")
        return False


def send_referral_bonus_notification(
    referrer_id: int,
    referrer_email: str,
    referrer_username: str,
    referrer_new_balance: float,
    referee_username: str,
    referee_email: str,
    referee_new_balance: float,
) -> tuple[bool, bool]:
    """
    Send email notifications to both referrer and referee when bonus is applied.

    Returns: (referrer_success, referee_success)
    """
    try:
        from src.enhanced_notification_service import enhanced_notification_service

        # Send notification to referrer
        referrer_subject = "Your referral was completed! - AI Gateway"

        referrer_content = f"""
            <h2>🎉 Nice work!</h2>
            <p>Hi <strong>{referrer_username}</strong>,</p>
            <p>Great news! <strong>{referee_username}</strong> just made their first purchase. Thanks for the referral!</p>

            <p>Keep sharing your referral code with friends!</p>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{SETTINGS_CREDITS_URL}" style="display: inline-block; background-color: #3b82f6; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px;">View Your Balance</a>
            </div>
        """

        from src.services.professional_email_templates import email_templates

        referrer_html = email_templates.get_base_template().format(
            subject="Referral Completed!",
            header_subtitle="Your referral just made their first purchase",
            content=referrer_content,
            app_name="AI Gateway",
            app_url="https://gatewayz.ai",
            support_email="noreply@gatewayz.ai",
            email=referrer_email,
        )

        referrer_text = f"""Referral Completed - AI Gateway

Hi {referrer_username},

Great news! {referee_username} just made their first purchase. Thanks for the referral!

Keep sharing your referral code with friends!

View your balance: {SETTINGS_CREDITS_URL}

Best regards,
The AI Gateway Team
"""

        referrer_success = enhanced_notification_service.send_email_notification(
            to_email=referrer_email,
            subject=referrer_subject,
            html_content=referrer_html,
            text_content=referrer_text,
        )

        # Send notification to referee
        referee_subject = "Thanks for your first purchase! - AI Gateway"

        referee_content = f"""
            <h2>🎉 Thank you!</h2>
            <p>Hi <strong>{referee_username}</strong>,</p>
            <p>Thank you for your purchase and for joining through a referral code.</p>

            <div style="text-align: center; margin: 30px 0;">
                <a href="{SETTINGS_CREDITS_URL}" style="display: inline-block; background-color: #3b82f6; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px;">View Your Balance</a>
            </div>
        """

        referee_html = email_templates.get_base_template().format(
            subject="Thanks for your purchase!",
            header_subtitle="Welcome to AI Gateway",
            content=referee_content,
            app_name="AI Gateway",
            app_url="https://gatewayz.ai",
            support_email="noreply@gatewayz.ai",
            email=referee_email,
        )

        referee_text = f"""Thanks for your purchase - AI Gateway

Hi {referee_username},

Thank you for your purchase and for joining through a referral code.

View your balance: {SETTINGS_CREDITS_URL}

Best regards,
The AI Gateway Team
"""

        referee_success = enhanced_notification_service.send_email_notification(
            to_email=referee_email,
            subject=referee_subject,
            html_content=referee_html,
            text_content=referee_text,
        )

        if referrer_success:
            logger.info(f"Sent referral bonus notification to referrer {referrer_id}")
        if referee_success:
            logger.info("Sent referral bonus notification to referee")

        return referrer_success, referee_success

    except Exception as e:
        logger.error(f"Error sending referral bonus notifications: {e}")
        return False, False


# Constants
REFERRAL_CODE_LENGTH = 8
MAX_REFERRAL_USES = 10  # Each referral code can be used by 10 different users
MIN_PURCHASE_AMOUNT = 10.0  # $10 minimum
REFERRAL_BONUS = 10.0  # Legacy constant; referrals no longer grant any credits (kept for back-compat/tests)


def generate_referral_code() -> str:
    """Generate a unique 8-character referral code"""
    characters = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(characters) for _ in range(REFERRAL_CODE_LENGTH))


def create_user_referral_code(user_id: int) -> str:
    """Create a unique referral code for a user"""
    try:
        client = get_supabase_client()

        # Generate unique code
        max_attempts = 10
        for _ in range(max_attempts):
            code = generate_referral_code()

            # Check if code already exists
            existing = client.table("users").select("id").eq("referral_code", code).execute()

            if not existing.data:
                # Update user with new referral code
                result = (
                    client.table("users")
                    .update({"referral_code": code})
                    .eq("id", user_id)
                    .execute()
                )

                if result.data:
                    logger.info(f"Created referral code {code} for user {user_id}")
                    return code

        raise RuntimeError("Failed to generate unique referral code after max attempts")

    except Exception as e:
        logger.error(f"Failed to create referral code: {e}")
        raise


def validate_referral_code(
    referral_code: str, user_id: int
) -> tuple[bool, str | None, dict[str, Any]] | None:
    """
    Validate if a referral code can be used by a user.

    Returns: (is_valid, error_message, referrer_data)
    """
    try:
        client = get_supabase_client()

        # Get the referrer
        referrer_result = (
            client.table("users").select("*").eq("referral_code", referral_code).execute()
        )

        if not referrer_result.data:
            return False, "Invalid referral code", None

        referrer = referrer_result.data[0]

        # Get the user trying to use the code
        user_result = client.table("users").select("*").eq("id", user_id).execute()

        if not user_result.data:
            return False, "User not found", None

        user = user_result.data[0]

        # Check if user is trying to use their own code
        if user.get("referral_code") == referral_code:
            return False, "Cannot use your own referral code", None

        # Check if user has already made a purchase
        if user.get("has_made_first_purchase", False):
            return False, "Referral code can only be used on first purchase", None

        # Check if user already used a DIFFERENT referral code
        # Note: If they're using the same code they registered with, that's OK for first purchase bonus
        if user.get("referred_by_code") and user.get("referred_by_code") != referral_code:
            return False, "You have already used a different referral code", None

        # Check how many times this referral code has been used
        usage_count_result = (
            client.table("referrals")
            .select("id", count="exact")
            .eq("referral_code", referral_code)
            .eq("status", "completed")
            .execute()
        )

        usage_count = usage_count_result.count if usage_count_result.count else 0

        if usage_count >= MAX_REFERRAL_USES:
            return (
                False,
                f"This referral code has reached its usage limit ({MAX_REFERRAL_USES} uses)",
                None,
            )

        return True, None, referrer

    except Exception as e:
        logger.error(f"Error validating referral code: {e}")
        return False, f"Validation error: {str(e)}", None


def apply_referral_bonus(
    user_id: int, referral_code: str, purchase_amount: float
) -> tuple[bool, str | None, dict[str, Any]] | None:
    """
    Record a referral relationship after a qualifying purchase.

    Per product policy, no free credits are granted for referrals. This function
    validates the referral code and marks the referral record as completed (for
    attribution/analytics) but does NOT credit the referee or the referrer.

    Returns: (success, error_message, bonus_data) where bonus amounts are always 0.
    """
    try:
        client = get_supabase_client()

        # Validate purchase amount
        if purchase_amount < MIN_PURCHASE_AMOUNT:
            return (
                False,
                f"Referral code requires a minimum purchase of ${MIN_PURCHASE_AMOUNT}",
                None,
            )

        # Validate referral code
        is_valid, error_message, referrer = validate_referral_code(referral_code, user_id)

        if not is_valid:
            return False, error_message, None

        # Get user
        user_result = client.table("users").select("*").eq("id", user_id).execute()
        if not user_result.data:
            return False, "User not found", None

        # Check if there's already a pending referral record from signup
        existing_referral = (
            client.table("referrals")
            .select("*")
            .eq("referred_user_id", user_id)
            .eq("referral_code", referral_code)
            .eq("status", "pending")
            .execute()
        )

        if existing_referral.data:
            # Update existing pending referral to completed
            referral_result = (
                client.table("referrals")
                .update({"status": "completed", "completed_at": datetime.now(UTC).isoformat()})
                .eq("id", existing_referral.data[0]["id"])
                .execute()
            )

            if not referral_result.data:
                return False, "Failed to update referral record", None
        else:
            # Create new referral record (for cases where they didn't sign up with the code)
            referral_data = {
                "referrer_id": referrer["id"],
                "referred_user_id": user_id,
                "referral_code": referral_code,
                "bonus_amount": 0,
                "status": "completed",
                "completed_at": datetime.now(UTC).isoformat(),
            }

            referral_result = client.table("referrals").insert(referral_data).execute()

            if not referral_result.data:
                return False, "Failed to create referral record", None

        # Per policy, no free credits are granted for referrals. We only record
        # the referral relationship for attribution/analytics; neither the referee
        # nor the referrer receives any bonus credits here.

        # Update referred_by_code for the new user
        client.table("users").update({"referred_by_code": referral_code}).eq(
            "id", user_id
        ).execute()

        logger.info(
            f"Recorded referral attribution for user {user_id} "
            f"(referrer {referrer['id']}, code {referral_code}) - no credits granted"
        )

        bonus_data = {
            "user_bonus": 0,
            "referrer_bonus": 0,
            "referrer_username": referrer.get("username", "Unknown"),
            "referrer_email": referrer.get("email", "Unknown"),
        }

        return True, None, bonus_data

    except Exception as e:
        logger.error(f"Error applying referral bonus: {e}", exc_info=True)
        return False, f"Failed to apply referral bonus: {str(e)}", None


def get_referral_stats(user_id: int) -> dict[str, Any] | None:
    """Get referral statistics for a user"""
    try:
        client = get_supabase_client()

        # Get user
        user_result = client.table("users").select("*").eq("id", user_id).execute()

        if not user_result.data:
            return None

        user = user_result.data[0]
        referral_code = user.get("referral_code")

        # If user doesn't have a referral code, create one
        if not referral_code:
            referral_code = create_user_referral_code(user_id)
            user["referral_code"] = referral_code

        # Get users who signed up with this referral code (from users table)
        referred_users_result = (
            client.table("users")
            .select("id", "username", "email", "created_at")
            .eq("referred_by_code", referral_code)
            .execute()
        )

        referred_users = referred_users_result.data if referred_users_result.data else []

        # Get successful referrals (from referrals table)
        referrals_result = (
            client.table("referrals")
            .select("*")
            .eq("referrer_id", user_id)
            .eq("status", "completed")
            .execute()
        )

        completed_referrals = referrals_result.data if referrals_result.data else []

        # Calculate stats
        total_uses = len(referred_users)  # Total people who used the code
        completed_bonuses = len(completed_referrals)  # How many got bonuses
        total_earned = sum(r.get("bonus_amount", 0) for r in completed_referrals)
        remaining_uses = max(0, MAX_REFERRAL_USES - total_uses)

        # Get details of referred users
        referral_details = []
        for ref_user in referred_users:
            # Check if this user got a bonus (completed referral)
            bonus_info = None
            for completed_ref in completed_referrals:
                if completed_ref["referred_user_id"] == ref_user["id"]:
                    bonus_info = {
                        "bonus_earned": completed_ref.get("bonus_amount", 0),
                        "bonus_date": completed_ref.get(
                            "completed_at", completed_ref.get("created_at")
                        ),
                    }
                    break

            referral_details.append(
                {
                    "id": ref_user["id"],  # Frontend expects 'id' for React key prop
                    "user_id": ref_user["id"],
                    "referee_id": ref_user["id"],  # Frontend normalizes to referee_id
                    "username": ref_user.get("username", "Unknown"),
                    "email": ref_user.get("email", "Unknown"),
                    "referee_email": ref_user.get(
                        "email", "Unknown"
                    ),  # Frontend expects referee_email
                    "created_at": ref_user.get("created_at"),  # Frontend expects created_at
                    "date": ref_user.get("created_at"),
                    "signed_up_at": ref_user.get("created_at"),
                    "status": "completed" if bonus_info else "pending",
                    "bonus_earned": bonus_info.get("bonus_earned", 0) if bonus_info else 0,
                    "bonus_date": bonus_info.get("bonus_date") if bonus_info else None,
                    "completed_at": (
                        bonus_info.get("bonus_date") if bonus_info else None
                    ),  # Frontend expects completed_at
                    "reward": (bonus_info.get("bonus_earned", 0) if bonus_info else 0),
                    "reward_amount": (
                        bonus_info.get("bonus_earned", 0) if bonus_info else 0
                    ),  # Referrals no longer grant credits; kept for frontend field compatibility
                }
            )

        return {
            "referral_code": referral_code,
            "total_uses": total_uses,
            "completed_bonuses": completed_bonuses,
            "pending_bonuses": total_uses - completed_bonuses,
            "remaining_uses": remaining_uses,
            "max_uses": MAX_REFERRAL_USES,
            "total_earned": float(total_earned),
            "current_balance": float(user.get("subscription_allowance", 0) or 0) + float(user.get("purchased_credits", 0) or 0),
            "referred_by_code": user.get("referred_by_code"),
            "referrals": referral_details,
        }

    except Exception as e:
        logger.error(f"Error getting referral stats: {e}")
        return None


def track_referral_signup(
    referral_code: str, referred_user_id: int
) -> tuple[bool, str | None, dict[str, Any]] | None:
    """
    Track when a user signs up with a referral code (creates pending referral record).

    Returns: (success, error_message, referrer_data)
    """
    try:
        client = get_supabase_client()

        # Get the referrer
        referrer_result = (
            client.table("users").select("*").eq("referral_code", referral_code).execute()
        )

        if not referrer_result.data:
            return False, "Invalid referral code", None

        referrer = referrer_result.data[0]

        # Check if user is trying to use their own code
        if referrer["id"] == referred_user_id:
            return False, "Cannot use your own referral code", None

        # Check if there's already a referral record for this user BEFORE checking usage limits
        # This allows idempotent retries even if the code has since reached its limit
        existing_referral = (
            client.table("referrals").select("*").eq("referred_user_id", referred_user_id).execute()
        )

        if existing_referral.data:
            # User already has a referral record - use the ORIGINAL referral code
            # to maintain data consistency (don't let them switch referrers)
            original_code = existing_referral.data[0].get("referral_code")
            if original_code != referral_code:
                logger.warning(
                    f"User {referred_user_id} already referred by code {original_code}, "
                    f"ignoring new code {referral_code}"
                )
                return False, "You have already been referred by another user", None

            # Same code, just ensure referred_by_code is set (idempotent)
            # Skip usage limit check since this user is already counted
            logger.info(
                f"Referral record already exists for user {referred_user_id} with same code, "
                f"ensuring referred_by_code is set"
            )
            code_to_set = original_code
        else:
            # New referral - check usage limits before creating record
            usage_count_result = (
                client.table("referrals")
                .select("id", count="exact")
                .eq("referral_code", referral_code)
                .execute()
            )

            usage_count = usage_count_result.count if usage_count_result.count else 0

            if usage_count >= MAX_REFERRAL_USES:
                return (
                    False,
                    f"This referral code has reached its usage limit ({MAX_REFERRAL_USES} uses)",
                    None,
                )

            # Create pending referral record (will be completed when they make first purchase)
            referral_data = {
                "referrer_id": referrer["id"],
                "referred_user_id": referred_user_id,
                "referral_code": referral_code,
                "bonus_amount": 0,
                "status": "pending",
                "created_at": datetime.now(UTC).isoformat(),
            }

            referral_result = client.table("referrals").insert(referral_data).execute()

            if not referral_result.data:
                return False, "Failed to create referral record", None

            code_to_set = referral_code

        # CRITICAL: Set referred_by_code on the user record so they appear in referral stats
        # This must be done here to ensure the user appears on the referrer's page
        update_result = (
            client.table("users")
            .update({"referred_by_code": code_to_set})
            .eq("id", referred_user_id)
            .execute()
        )

        if not update_result.data:
            logger.error(
                f"Failed to set referred_by_code for user {referred_user_id}, "
                f"they will not appear in referral stats"
            )
            return False, "Failed to update user referral information", None

        logger.info(
            f"Tracked referral signup: user {referred_user_id} used code {code_to_set} "
            f"from user {referrer['id']}"
        )

        return True, None, referrer

    except Exception as e:
        logger.error(f"Error tracking referral signup: {e}", exc_info=True)
        return False, f"Failed to track referral signup: {str(e)}", None


def mark_first_purchase(user_id: int) -> bool:
    """Mark that a user has made their first purchase"""
    try:
        client = get_supabase_client()

        result = (
            client.table("users")
            .update({"has_made_first_purchase": True})
            .eq("id", user_id)
            .execute()
        )

        if result.data:
            logger.info(f"Marked first purchase for user {user_id}")
            return True

        return False

    except Exception as e:
        logger.error(f"Error marking first purchase: {e}")
        return False
