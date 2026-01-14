"""
Release tracking utilities for Sentry integration.

This module provides functions to track application releases, manage release
health, and associate errors with specific releases in Sentry.
"""

import logging
from typing import Any

try:
    import sentry_sdk
    from sentry_sdk import capture_message, set_context, set_tag

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

logger = logging.getLogger(__name__)


def get_release_info() -> dict[str, str]:
    """
    Get current release information from Sentry SDK.

    Returns:
        dict: Release information including version and environment
    """
    if not SENTRY_AVAILABLE:
        return {}

    try:
        client = sentry_sdk.get_client()
        if client and client.options:
            return {
                "release": client.options.get("release", "unknown"),
                "environment": client.options.get("environment", "unknown"),
            }
    except Exception as e:
        logger.warning(f"Failed to get release info from Sentry: {e}")

    return {}


def capture_release_event(
    message: str,
    level: str = "info",
    release_metadata: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture a release-related event to Sentry.

    Useful for tracking deployment events, release deployments, and release health.

    Args:
        message: Event message (e.g., "Release 2.0.3 deployed to production")
        level: Log level (info, warning, error, etc.)
        release_metadata: Additional metadata about the release

    Returns:
        Event ID if captured, None if Sentry is disabled

    Example:
        capture_release_event(
            "Release 2.0.3 deployed to production",
            level="info",
            release_metadata={
                "deploy_duration_seconds": 45,
                "affected_services": ["api", "worker"],
                "previous_version": "2.0.2"
            }
        )
    """
    if not SENTRY_AVAILABLE:
        return None

    try:
        if release_metadata:
            set_context("release", release_metadata)

        set_tag("event_type", "release")

        return capture_message(message, level=level)
    except Exception as e:
        logger.warning(f"Failed to capture release event to Sentry: {e}")
        return None


def set_release_context(
    version: str,
    commit: str | None = None,
    environment: str | None = None,
) -> None:
    """
    Set release context for all subsequent errors.

    Args:
        version: Release version (e.g., "2.0.3")
        commit: Git commit hash if available
        environment: Environment name (development, staging, production)

    Example:
        set_release_context(
            version="2.0.3",
            commit="abc123def456",
            environment="production"
        )
    """
    if not SENTRY_AVAILABLE:
        return

    try:
        context_data = {"version": version}
        if commit:
            context_data["commit"] = commit
        if environment:
            context_data["environment"] = environment

        set_context("release", context_data)

        if commit:
            set_tag("commit", commit)
        if environment:
            set_tag("release_environment", environment)
    except Exception as e:
        logger.warning(f"Failed to set release context: {e}")


def capture_deployment_event(
    version: str,
    environment: str,
    status: str = "succeeded",
    details: dict[str, Any] | None = None,
) -> str | None:
    """
    Capture a deployment event for release tracking.

    Sentry uses deployment events to track which versions are deployed to which
    environments. This enables release health monitoring and version tracking.

    Args:
        version: Release version (e.g., "2.0.3")
        environment: Target environment (development, staging, production)
        status: Deployment status (succeeded, failed, in_progress)
        details: Additional deployment details

    Returns:
        Event ID if captured, None if Sentry is disabled

    Example:
        capture_deployment_event(
            version="2.0.3",
            environment="production",
            status="succeeded",
            details={
                "duration_seconds": 45,
                "deployed_by": "ci-system",
                "services": ["api", "worker"]
            }
        )
    """
    if not SENTRY_AVAILABLE:
        return None

    try:
        message = f"Deployed version {version} to {environment}: {status}"

        deployment_context = {
            "version": version,
            "environment": environment,
            "status": status,
        }
        if details:
            deployment_context.update(details)

        set_context("deployment", deployment_context)
        set_tag("deployment_status", status)
        set_tag("deployment_environment", environment)
        set_tag("deployed_version", version)

        level = "info" if status == "succeeded" else "warning"
        return capture_message(message, level=level)
    except Exception as e:
        logger.warning(f"Failed to capture deployment event: {e}")
        return None


def capture_release_health(
    version: str,
    metric: str,
    value: float | int,
    unit: str | None = None,
) -> None:
    """
    Capture release health metrics to Sentry.

    Args:
        version: Release version
        metric: Metric name (e.g., "error_rate", "session_count", "response_time")
        value: Metric value
        unit: Unit of measurement (e.g., "percentage", "milliseconds")

    Example:
        capture_release_health(
            version="2.0.3",
            metric="error_rate",
            value=0.5,
            unit="percentage"
        )
    """
    if not SENTRY_AVAILABLE:
        return

    try:
        context_data = {"metric": metric, "value": value}
        if unit:
            context_data["unit"] = unit

        set_context(f"release_health_{version}", context_data)
        set_tag(f"health_{metric}", str(value))
    except Exception as e:
        logger.warning(f"Failed to capture release health metric: {e}")


def get_current_release() -> str | None:
    """
    Get the currently active release version.

    Returns:
        Release version string or None if not set
    """
    if not SENTRY_AVAILABLE:
        return None

    try:
        client = sentry_sdk.get_client()
        if client and client.options:
            return client.options.get("release")
    except Exception as e:
        logger.warning(f"Failed to get current release: {e}")

    return None
