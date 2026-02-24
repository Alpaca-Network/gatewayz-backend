#!/usr/bin/env python3
"""
Trial Utilities
Shared utilities for trial access validation and tracking
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

# Import the actual trial module to avoid duplication
from src.services import trial_validation as trial_module

logger = logging.getLogger(__name__)


def validate_trial_access(*args, **kwargs) -> Any:
    """
    Validate trial access for a user.

    This is a shared wrapper around the trial validation service
    to avoid code duplication across multiple route modules.

    Args:
        *args: Positional arguments passed to the trial validation service
        **kwargs: Keyword arguments passed to the trial validation service

    Returns:
        Result from the trial validation service
    """
    return trial_module.validate_trial_access(*args, **kwargs)


def track_trial_usage(*args, **kwargs) -> Any:
    """
    Track trial usage for a user.

    This is a shared wrapper around the trial tracking service
    to avoid code duplication across multiple route modules.

    Args:
        *args: Positional arguments passed to the trial tracking service
        **kwargs: Keyword arguments passed to the trial tracking service

    Returns:
        Result from the trial tracking service
    """
    return trial_module.track_trial_usage(*args, **kwargs)


def validate_trial_expiration(user: dict) -> None:
    """
    Validate trial expiration and raise HTTPException if expired.

    This function centralizes the trial expiration checking logic to avoid duplication
    across multiple auth/security modules.

    Args:
        user: User dictionary containing subscription_status and trial_expires_at

    Raises:
        HTTPException: 402 Payment Required if trial has expired

    Returns:
        None if trial is valid or not applicable
    """
    subscription_status = user.get("subscription_status", "")
    trial_expires_at = user.get("trial_expires_at")

    if subscription_status == "trial" and trial_expires_at:
        try:
            # Parse trial_expires_at and compare to current time
            if isinstance(trial_expires_at, str):
                # Handle ISO format with or without timezone
                trial_expires_at_formatted = trial_expires_at
                if trial_expires_at.endswith("Z"):
                    trial_expires_at_formatted = trial_expires_at.replace("Z", "+00:00")
                expiry_date = datetime.fromisoformat(trial_expires_at_formatted)
            else:
                expiry_date = trial_expires_at

            # Ensure both datetimes are timezone-aware for comparison
            if expiry_date.tzinfo is None:
                expiry_date = expiry_date.replace(tzinfo=UTC)

            current_time = datetime.now(UTC)

            if current_time > expiry_date:
                logger.info(
                    f"Trial expired for user {user.get('id')}. "
                    f"Expired at: {expiry_date.isoformat()}, Current time: {current_time.isoformat()}"
                )
                raise HTTPException(
                    status_code=402,
                    detail="Your 3-day trial period has expired. Please upgrade to continue using the service.",
                )
        except HTTPException:
            # Re-raise HTTPException
            raise
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Failed to parse trial_expires_at for user {user.get('id')}: {e}. "
                "Allowing request to proceed."
            )
