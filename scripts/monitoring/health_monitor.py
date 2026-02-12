#!/usr/bin/env python3
"""
Gatewayz Health Monitor

This script continuously monitors the health of the Gatewayz API by checking
the /health endpoint every minute. When downtime is detected, it:
1. Creates a downtime incident record
2. Captures logs from 5 minutes before to 5 minutes after the downtime
3. Stores the logs for debugging and analysis

Usage:
    python scripts/monitoring/health_monitor.py --url https://api.gatewayz.ai --interval 60

    Or with systemd:
    sudo systemctl start gatewayz-health-monitor.service

Features:
- Continuous health monitoring with configurable interval
- Automatic downtime incident creation
- Log capture from Grafana Loki
- Email notifications (optional)
- Metrics export for Prometheus
- Graceful shutdown handling
"""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.db.downtime_incidents import (
    create_incident,
    get_ongoing_incidents,
    resolve_incident,
    update_incident,
)
from src.services.downtime_log_capture import (
    capture_logs_for_ongoing_incident,
    capture_logs_for_resolved_incident,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/health_monitor.log"),
    ],
)

logger = logging.getLogger(__name__)

# Global state
shutdown_requested = False
ongoing_incident_id = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True


class HealthMonitor:
    """
    Health monitoring service for Gatewayz API.

    Continuously checks the health endpoint and tracks downtime incidents.
    """

    def __init__(
        self,
        base_url: str,
        check_interval: int = 60,
        timeout: int = 10,
        enable_log_capture: bool = True,
        enable_notifications: bool = False,
    ):
        """
        Initialize the health monitor.

        Args:
            base_url: Base URL of the API (e.g., https://api.gatewayz.ai)
            check_interval: Seconds between health checks (default: 60)
            timeout: HTTP request timeout in seconds (default: 10)
            enable_log_capture: Enable automatic log capture on downtime
            enable_notifications: Enable email notifications
        """
        self.base_url = base_url.rstrip("/")
        self.check_interval = check_interval
        self.timeout = timeout
        self.enable_log_capture = enable_log_capture
        self.enable_notifications = enable_notifications

        self.health_url = f"{self.base_url}/health"
        self.ongoing_incident_id: str | None = None
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.last_check_time: datetime | None = None

        # Statistics
        self.total_checks = 0
        self.total_failures = 0
        self.total_incidents = 0

        logger.info(f"Initialized HealthMonitor for {self.base_url}")
        logger.info(f"Check interval: {self.check_interval}s, Timeout: {self.timeout}s")

    def check_health(self) -> tuple[bool, dict[str, Any]]:
        """
        Check the health endpoint.

        Returns:
            Tuple of (is_healthy, details)
            - is_healthy: True if endpoint is healthy
            - details: Dict with status code, response, error message, etc.
        """
        try:
            start_time = time.time()

            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(self.health_url)

            response_time = time.time() - start_time

            # Check if response is healthy
            is_healthy = response.status_code == 200

            details = {
                "status_code": response.status_code,
                "response_time": response_time,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            try:
                details["response_body"] = response.text
            except Exception:
                details["response_body"] = None

            if is_healthy:
                logger.debug(f"Health check passed (HTTP {response.status_code})")
            else:
                logger.warning(
                    f"Health check failed (HTTP {response.status_code}): {response.text[:200]}"
                )

            return is_healthy, details

        except httpx.TimeoutException as e:
            logger.error(f"Health check timed out after {self.timeout}s: {e}")
            return False, {
                "error": "timeout",
                "error_message": f"Request timed out after {self.timeout}s",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except httpx.ConnectError as e:
            logger.error(f"Health check connection failed: {e}")
            return False, {
                "error": "connection_error",
                "error_message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Health check error: {e}", exc_info=True)
            return False, {
                "error": "unknown",
                "error_message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def handle_downtime(self, details: dict[str, Any]) -> None:
        """
        Handle detection of downtime.

        Creates a new downtime incident if one doesn't exist.

        Args:
            details: Details from health check failure
        """
        if self.ongoing_incident_id:
            # Already tracking this downtime
            self.consecutive_failures += 1
            logger.warning(
                f"Downtime continues (consecutive failures: {self.consecutive_failures})"
            )
            return

        # New downtime detected
        self.consecutive_failures = 1
        self.consecutive_successes = 0

        logger.error("🚨 NEW DOWNTIME DETECTED - Creating incident record")

        # Determine severity based on error type
        error_type = details.get("error", "unknown")
        severity = "critical" if error_type == "timeout" else "high"

        # Create incident record
        incident = create_incident(
            started_at=datetime.now(timezone.utc),
            health_endpoint="/health",
            error_message=details.get("error_message"),
            http_status_code=details.get("status_code"),
            response_body=details.get("response_body"),
            severity=severity,
            environment=os.environ.get("APP_ENV", "production"),
            server_info={
                "monitor_url": self.base_url,
                "timeout": self.timeout,
            },
        )

        if incident:
            self.ongoing_incident_id = incident["id"]
            self.total_incidents += 1

            logger.error(
                f"Created downtime incident {self.ongoing_incident_id} "
                f"(severity: {severity})"
            )

            # Send notification if enabled
            if self.enable_notifications:
                self._send_downtime_notification(incident)

        else:
            logger.error("Failed to create downtime incident record")

    def handle_recovery(self, details: dict[str, Any]) -> None:
        """
        Handle recovery from downtime.

        Resolves the ongoing incident and captures logs.

        Args:
            details: Details from successful health check
        """
        if not self.ongoing_incident_id:
            # No ongoing incident
            self.consecutive_successes += 1
            return

        # Recovery detected
        self.consecutive_successes += 1

        # Wait for a few consecutive successes before marking as recovered
        # This prevents flapping
        required_successes = 3

        if self.consecutive_successes < required_successes:
            logger.info(
                f"Service recovering ({self.consecutive_successes}/{required_successes} "
                "successful checks)"
            )
            return

        # Service has recovered
        logger.info(
            f"✅ SERVICE RECOVERED - Resolving incident {self.ongoing_incident_id}"
        )

        # Get incident details for log capture
        from src.db.downtime_incidents import get_incident

        incident = get_incident(self.ongoing_incident_id)

        if incident:
            # Resolve the incident
            resolve_incident(
                incident_id=self.ongoing_incident_id,
                resolved_by="health_monitor",
                notes=f"Service recovered after {self.consecutive_failures} failed checks",
            )

            # Capture logs if enabled
            if self.enable_log_capture:
                logger.info("Capturing logs for resolved incident...")

                try:
                    started_at = datetime.fromisoformat(incident["started_at"])
                    ended_at = datetime.now(timezone.utc)

                    result = capture_logs_for_resolved_incident(
                        incident_id=self.ongoing_incident_id,
                        started_at=started_at,
                        ended_at=ended_at,
                        save_to_file=True,  # Save to file for resolved incidents
                    )

                    if result["success"]:
                        logger.info(
                            f"Captured {result['log_count']} logs "
                            f"(storage: {result['storage']})"
                        )
                    else:
                        logger.warning(f"Log capture failed: {result.get('error')}")

                except Exception as e:
                    logger.error(f"Error capturing logs: {e}", exc_info=True)

            # Send recovery notification if enabled
            if self.enable_notifications:
                self._send_recovery_notification(incident)

        # Reset state
        self.ongoing_incident_id = None
        self.consecutive_failures = 0

    def run_check_cycle(self) -> None:
        """Run a single health check cycle."""
        self.total_checks += 1
        self.last_check_time = datetime.now(timezone.utc)

        is_healthy, details = self.check_health()

        if is_healthy:
            self.handle_recovery(details)
        else:
            self.total_failures += 1
            self.handle_downtime(details)

    def run(self) -> None:
        """
        Main monitoring loop.

        Runs continuously until shutdown is requested.
        """
        logger.info("🚀 Starting health monitoring service...")
        logger.info(f"Monitoring: {self.health_url}")
        logger.info(f"Check interval: {self.check_interval}s")

        # Check for existing ongoing incidents
        try:
            existing_incidents = get_ongoing_incidents()
            if existing_incidents:
                logger.warning(
                    f"Found {len(existing_incidents)} existing ongoing incidents"
                )
                # Use the most recent one
                self.ongoing_incident_id = existing_incidents[0]["id"]
                logger.info(f"Resuming monitoring of incident {self.ongoing_incident_id}")
        except Exception as e:
            logger.error(f"Error checking for ongoing incidents: {e}")

        # Main loop
        while not shutdown_requested:
            try:
                self.run_check_cycle()

            except Exception as e:
                logger.error(f"Error in check cycle: {e}", exc_info=True)

            # Wait for next check
            time.sleep(self.check_interval)

        # Shutdown
        logger.info("Health monitor shutting down...")

        # If there's an ongoing incident, capture logs one last time
        if self.ongoing_incident_id and self.enable_log_capture:
            logger.info("Capturing logs for ongoing incident before shutdown...")

            try:
                from src.db.downtime_incidents import get_incident

                incident = get_incident(self.ongoing_incident_id)
                if incident:
                    started_at = datetime.fromisoformat(incident["started_at"])

                    capture_logs_for_ongoing_incident(
                        incident_id=self.ongoing_incident_id,
                        started_at=started_at,
                        save_to_file=True,
                    )
            except Exception as e:
                logger.error(f"Error capturing logs during shutdown: {e}")

        logger.info(
            f"Monitor statistics: {self.total_checks} checks, "
            f"{self.total_failures} failures, {self.total_incidents} incidents"
        )

    def _send_downtime_notification(self, incident: dict[str, Any]) -> None:
        """Send notification about downtime (placeholder)."""
        # TODO: Implement email notification
        logger.info(f"Notification: Downtime incident {incident['id']} created")

    def _send_recovery_notification(self, incident: dict[str, Any]) -> None:
        """Send notification about recovery (placeholder)."""
        # TODO: Implement email notification
        logger.info(f"Notification: Downtime incident {incident['id']} resolved")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Monitor Gatewayz API health and track downtime incidents"
    )

    parser.add_argument(
        "--url",
        default=os.environ.get("GATEWAYZ_URL", "https://api.gatewayz.ai"),
        help="Base URL of the API to monitor (default: https://api.gatewayz.ai)",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("HEALTH_CHECK_INTERVAL", "60")),
        help="Seconds between health checks (default: 60)",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("HEALTH_CHECK_TIMEOUT", "10")),
        help="HTTP request timeout in seconds (default: 10)",
    )

    parser.add_argument(
        "--no-log-capture",
        action="store_true",
        help="Disable automatic log capture on downtime",
    )

    parser.add_argument(
        "--enable-notifications",
        action="store_true",
        help="Enable email notifications for downtime/recovery",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )

    args = parser.parse_args()

    # Set log level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create logs directory
    Path("logs").mkdir(exist_ok=True)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and run monitor
    monitor = HealthMonitor(
        base_url=args.url,
        check_interval=args.interval,
        timeout=args.timeout,
        enable_log_capture=not args.no_log_capture,
        enable_notifications=args.enable_notifications,
    )

    try:
        monitor.run()
    except Exception as e:
        logger.error(f"Fatal error in health monitor: {e}", exc_info=True)
        sys.exit(1)

    logger.info("Health monitor stopped")
    sys.exit(0)


if __name__ == "__main__":
    main()
