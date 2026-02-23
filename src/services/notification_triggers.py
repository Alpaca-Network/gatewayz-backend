"""
Notification triggers for automatic notification creation
Integrates with various system events to create notifications
"""

import logging

from ..models.notification_models import NotificationCategory, NotificationCreate, NotificationType
from ..services.notification_service import notification_service

logger = logging.getLogger(__name__)


class NotificationTriggers:
    """Manages automatic notification creation based on system events"""

    @staticmethod
    async def notify_new_user_signup(
        user_email: str, user_id: int, admin_user_ids: list[int]
    ) -> None:
        """
        Notify admins when a new user signs up

        Args:
            user_email: Email of the new user
            user_id: ID of the new user
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="New User Signup",
                message=f"User {user_email} (ID: {user_id}) just signed up for a trial account",
                type=NotificationType.INFO,
                category=NotificationCategory.USER,
                link=f"/admin/users/{user_id}",
                metadata={"user_email": user_email, "new_user_id": user_id},
                expires_in_days=7,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created new user signup notification for admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to create new user notification for admin {admin_id}: {e}")

    @staticmethod
    async def notify_payment_failure(
        user_id: int, user_email: str, amount: float, admin_user_ids: list[int]
    ) -> None:
        """
        Notify admins of payment failures

        Args:
            user_id: ID of the user with failed payment
            user_email: Email of the user
            amount: Payment amount that failed
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="Payment Failed",
                message=f"Payment of ${amount:.2f} failed for user {user_email} (ID: {user_id})",
                type=NotificationType.ERROR,
                category=NotificationCategory.PAYMENT,
                link=f"/admin/payments?user_id={user_id}",
                metadata={"amount": amount, "failed_user_id": user_id, "user_email": user_email},
                expires_in_days=30,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created payment failure notification for admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to create payment failure notification for admin {admin_id}: {e}")

    @staticmethod
    async def notify_model_outage(
        model_name: str, provider: str, gateway: str, admin_user_ids: list[int]
    ) -> None:
        """
        Notify admins when a model goes down

        Args:
            model_name: Name of the model experiencing issues
            provider: Provider name
            gateway: Gateway name
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="Model Outage",
                message=f"Model '{model_name}' on {provider} ({gateway}) is experiencing downtime",
                type=NotificationType.ERROR,
                category=NotificationCategory.HEALTH,
                link="/admin/health/models",
                metadata={"model": model_name, "provider": provider, "gateway": gateway},
                expires_in_days=3,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created model outage notification for admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to create model outage notification for admin {admin_id}: {e}")

    @staticmethod
    async def notify_high_error_rate(
        error_rate: float, threshold: float, admin_user_ids: list[int]
    ) -> None:
        """
        Notify admins when error rate exceeds threshold

        Args:
            error_rate: Current error rate percentage
            threshold: Threshold that was exceeded
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="High Error Rate Detected",
                message=f"System error rate is {error_rate:.1f}% (threshold: {threshold:.1f}%)",
                type=NotificationType.WARNING,
                category=NotificationCategory.SYSTEM,
                link="/admin/monitoring/errors",
                metadata={"error_rate": error_rate, "threshold": threshold},
                expires_in_days=1,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created high error rate notification for admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to create high error rate notification for admin {admin_id}: {e}")

    @staticmethod
    async def notify_credit_depletion(
        user_id: int,
        user_email: str,
        remaining_credits: float,
        admin_user_ids: list[int],
    ) -> None:
        """
        Notify admins when a user's credits are low

        Args:
            user_id: ID of the user
            user_email: Email of the user
            remaining_credits: Remaining credit balance
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="User Credits Low",
                message=f"User {user_email} (ID: {user_id}) has only ${remaining_credits:.2f} credits remaining",
                type=NotificationType.WARNING,
                category=NotificationCategory.USER,
                link=f"/admin/users/{user_id}",
                metadata={
                    "affected_user_id": user_id,
                    "user_email": user_email,
                    "remaining_credits": remaining_credits,
                },
                expires_in_days=7,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created credit depletion notification for admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to create credit depletion notification for admin {admin_id}: {e}")

    @staticmethod
    async def notify_api_key_breach_attempt(
        api_key_id: int, ip_address: str, admin_user_ids: list[int]
    ) -> None:
        """
        Notify admins of potential API key security breach

        Args:
            api_key_id: ID of the compromised API key
            ip_address: IP address attempting unauthorized access
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="Security Alert: API Key Breach Attempt",
                message=f"Suspicious activity detected for API key {api_key_id} from IP {ip_address}",
                type=NotificationType.ERROR,
                category=NotificationCategory.SECURITY,
                link="/admin/security/api-keys",
                metadata={"api_key_id": api_key_id, "ip_address": ip_address},
                expires_in_days=30,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created security breach notification for admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to create security breach notification for admin {admin_id}: {e}")

    @staticmethod
    async def notify_trial_expiring_soon(
        user_id: int, user_email: str, days_remaining: int, admin_user_ids: list[int]
    ) -> None:
        """
        Notify admins when a user's trial is expiring soon

        Args:
            user_id: ID of the user
            user_email: Email of the user
            days_remaining: Days until trial expires
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="Trial Expiring Soon",
                message=f"User {user_email}'s trial expires in {days_remaining} day(s)",
                type=NotificationType.INFO,
                category=NotificationCategory.USER,
                link=f"/admin/users/{user_id}",
                metadata={
                    "affected_user_id": user_id,
                    "user_email": user_email,
                    "days_remaining": days_remaining,
                },
                expires_in_days=days_remaining + 1,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created trial expiring notification for admin {admin_id}")
            except Exception as e:
                logger.error(f"Failed to create trial expiring notification for admin {admin_id}: {e}")

    @staticmethod
    async def notify_subscription_payment_success(
        user_id: int, user_email: str, amount: float, plan_name: str, admin_user_ids: list[int]
    ) -> None:
        """
        Notify admins of successful subscription payment

        Args:
            user_id: ID of the user
            user_email: Email of the user
            amount: Payment amount
            plan_name: Name of the subscription plan
            admin_user_ids: List of admin user IDs to notify
        """
        for admin_id in admin_user_ids:
            notification = NotificationCreate(
                user_id=admin_id,
                title="Subscription Payment Received",
                message=f"User {user_email} paid ${amount:.2f} for {plan_name} plan",
                type=NotificationType.SUCCESS,
                category=NotificationCategory.PAYMENT,
                link=f"/admin/users/{user_id}",
                metadata={
                    "user_id": user_id,
                    "user_email": user_email,
                    "amount": amount,
                    "plan_name": plan_name,
                },
                expires_in_days=30,
            )
            try:
                await notification_service.create_notification(notification)
                logger.debug(f"Created subscription payment notification for admin {admin_id}")
            except Exception as e:
                logger.error(
                    f"Failed to create subscription payment notification for admin {admin_id}: {e}"
                )


# Global instance
notification_triggers = NotificationTriggers()
