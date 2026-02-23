#!/usr/bin/env python3
"""
Downtime Log Capture Service

This service captures application logs from Grafana Loki during downtime incidents.
It queries Loki for logs from 5 minutes before to 5 minutes after a downtime event,
and stores them in the database for debugging and analysis.

Features:
- Query Loki for logs in a specific time range
- Capture logs 5 minutes before and after downtime
- Store logs in database or optionally in files
- Support for filtering by log level, logger, etc.
"""

import json
import logging
from collections import deque
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

import httpx

from src.config.config import Config
from src.db.downtime_incidents import update_incident

logger = logging.getLogger(__name__)

# Time windows for log capture
PRE_DOWNTIME_MINUTES = 5
POST_DOWNTIME_MINUTES = 5

# Maximum logs to capture (to prevent memory issues)
MAX_LOGS_TO_CAPTURE = 10000


class LogBuffer:
    """
    Rolling buffer for maintaining recent logs in memory.

    This buffer keeps the last N minutes of logs, allowing us to capture
    logs from before a downtime incident was detected.
    """

    def __init__(self, max_minutes: int = 10, max_size: int = 5000):
        """
        Initialize the log buffer.

        Args:
            max_minutes: Maximum minutes of logs to keep
            max_size: Maximum number of log entries to keep
        """
        self.max_minutes = max_minutes
        self.max_size = max_size
        self._buffer: deque = deque(maxlen=max_size)

    def add(self, log_entry: dict[str, Any]) -> None:
        """
        Add a log entry to the buffer.

        Args:
            log_entry: Log entry with timestamp and message
        """
        try:
            self._buffer.append(log_entry)

            # Clean old entries if needed
            self._clean_old_entries()

        except Exception as e:
            logger.error(f"Error adding log to buffer: {e}")

    def _clean_old_entries(self) -> None:
        """Remove entries older than max_minutes."""
        if not self._buffer:
            return

        cutoff = datetime.now(UTC) - timedelta(minutes=self.max_minutes)

        # Remove old entries from the left
        while self._buffer:
            try:
                entry = self._buffer[0]
                entry_time = datetime.fromisoformat(entry.get("timestamp", ""))

                if entry_time < cutoff:
                    self._buffer.popleft()
                else:
                    break
            except Exception:
                # If we can't parse timestamp, remove it
                self._buffer.popleft()

    def get_logs_in_range(
        self, start_time: datetime, end_time: datetime
    ) -> list[dict[str, Any]]:
        """
        Get all logs within a time range.

        Args:
            start_time: Start of time range
            end_time: End of time range

        Returns:
            List of log entries in the time range
        """
        result = []

        for entry in self._buffer:
            try:
                entry_time = datetime.fromisoformat(entry.get("timestamp", ""))

                if start_time <= entry_time <= end_time:
                    result.append(entry)

            except Exception:
                continue

        return result

    def get_recent_logs(self, minutes: int = 5) -> list[dict[str, Any]]:
        """
        Get logs from the last N minutes.

        Args:
            minutes: Number of minutes to retrieve

        Returns:
            List of recent log entries
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
        now = datetime.now(UTC)
        return self.get_logs_in_range(cutoff, now)


def query_loki_logs(
    start_time: datetime,
    end_time: datetime,
    query: str = '{app="gatewayz-api"}',
    limit: int = MAX_LOGS_TO_CAPTURE,
) -> list[dict[str, Any]]:
    """
    Query Grafana Loki for logs in a specific time range.

    Args:
        start_time: Start of time range
        end_time: End of time range
        query: LogQL query string (default: all app logs)
        limit: Maximum number of logs to return

    Returns:
        List of log entries
    """
    if not Config.LOKI_ENABLED:
        logger.warning("Loki is not enabled - cannot query logs")
        return []

    try:
        # Build Loki query URL
        loki_url = Config.LOKI_QUERY_URL
        if not loki_url:
            logger.error("LOKI_QUERY_URL not configured")
            return []

        # Convert times to nanoseconds (Loki format)
        start_ns = int(start_time.timestamp() * 1_000_000_000)
        end_ns = int(end_time.timestamp() * 1_000_000_000)

        # Build query parameters
        params = {
            "query": query,
            "start": start_ns,
            "end": end_ns,
            "limit": limit,
            "direction": "forward",  # Chronological order
        }

        # Add authentication if using Grafana Cloud
        auth = None
        if Config.GRAFANA_LOKI_USERNAME and Config.GRAFANA_LOKI_API_KEY:
            auth = (Config.GRAFANA_LOKI_USERNAME, Config.GRAFANA_LOKI_API_KEY)

        # Query Loki
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{loki_url}/loki/api/v1/query_range",
                params=params,
                auth=auth,
            )
            response.raise_for_status()

            data = response.json()

            # Parse Loki response
            logs = []
            for stream in data.get("data", {}).get("result", []):
                stream_labels = stream.get("stream", {})

                for value in stream.get("values", []):
                    timestamp_ns, log_line = value

                    # Convert timestamp to datetime
                    timestamp = datetime.fromtimestamp(
                        int(timestamp_ns) / 1_000_000_000, tz=UTC
                    )

                    # Try to parse JSON log line
                    try:
                        log_data = json.loads(log_line)
                    except json.JSONDecodeError:
                        # If not JSON, store as plain text
                        log_data = {"message": log_line}

                    # Add metadata
                    log_entry = {
                        "timestamp": timestamp.isoformat(),
                        "labels": stream_labels,
                        **log_data,
                    }

                    logs.append(log_entry)

            logger.info(
                f"Queried Loki: {len(logs)} logs from "
                f"{start_time.isoformat()} to {end_time.isoformat()}"
            )

            return logs

    except httpx.HTTPStatusError as e:
        logger.error(f"Loki query failed with status {e.response.status_code}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error querying Loki: {e}", exc_info=True)
        return []


def capture_downtime_logs(
    incident_id: str,
    downtime_start: datetime,
    downtime_end: datetime | None = None,
    save_to_file: bool = False,
    file_directory: str | None = None,
) -> dict[str, Any]:
    """
    Capture logs for a downtime incident from Loki.

    Captures logs from 5 minutes before the downtime started to 5 minutes
    after it ended (or current time if still ongoing).

    Args:
        incident_id: UUID of the downtime incident
        downtime_start: When the downtime started
        downtime_end: When the downtime ended (None if ongoing)
        save_to_file: If True, save logs to a file instead of database
        file_directory: Directory to save log files (default: logs/downtime)

    Returns:
        Dict with capture results (success, log_count, file_path, etc.)
    """
    try:
        # Calculate time range: 5 min before to 5 min after (or now)
        start_time = downtime_start - timedelta(minutes=PRE_DOWNTIME_MINUTES)

        if downtime_end:
            end_time = downtime_end + timedelta(minutes=POST_DOWNTIME_MINUTES)
        else:
            # Still ongoing - capture up to now
            end_time = datetime.now(UTC)

        logger.info(
            f"Capturing logs for incident {incident_id} from "
            f"{start_time.isoformat()} to {end_time.isoformat()}"
        )

        # Query Loki for logs
        logs = query_loki_logs(start_time, end_time)

        if not logs:
            logger.warning(f"No logs found for incident {incident_id}")
            return {
                "success": False,
                "log_count": 0,
                "error": "No logs found in Loki",
            }

        # Save logs
        if save_to_file:
            # Save to file
            file_path = _save_logs_to_file(
                incident_id, logs, file_directory or "logs/downtime"
            )

            # Update incident with file path
            update_incident(
                incident_id=incident_id,
                logs_file_path=file_path,
            )

            result = {
                "success": True,
                "log_count": len(logs),
                "file_path": file_path,
                "storage": "file",
            }

        else:
            # Save to database (as JSONB)
            # Limit to MAX_LOGS_TO_CAPTURE to prevent database issues
            logs_to_save = logs[:MAX_LOGS_TO_CAPTURE]

            update_incident(
                incident_id=incident_id,
                logs_captured=logs_to_save,
            )

            result = {
                "success": True,
                "log_count": len(logs_to_save),
                "truncated": len(logs) > MAX_LOGS_TO_CAPTURE,
                "storage": "database",
            }

        logger.info(
            f"Captured {result['log_count']} logs for incident {incident_id} "
            f"(storage: {result['storage']})"
        )

        return result

    except Exception as e:
        logger.error(f"Error capturing logs for incident {incident_id}: {e}", exc_info=True)
        return {
            "success": False,
            "log_count": 0,
            "error": str(e),
        }


def _save_logs_to_file(
    incident_id: str, logs: list[dict[str, Any]], directory: str
) -> str:
    """
    Save logs to a JSON file.

    Args:
        incident_id: Incident UUID
        logs: List of log entries
        directory: Directory to save file

    Returns:
        Path to saved file
    """
    try:
        # Create directory if it doesn't exist
        Path(directory).mkdir(parents=True, exist_ok=True)

        # Generate filename with timestamp
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"incident_{incident_id}_{timestamp}.json"
        file_path = Path(directory) / filename

        # Save logs as JSON
        with open(file_path, "w") as f:
            json.dump(
                {
                    "incident_id": incident_id,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "log_count": len(logs),
                    "logs": logs,
                },
                f,
                indent=2,
            )

        logger.info(f"Saved {len(logs)} logs to {file_path}")
        return str(file_path)

    except Exception as e:
        logger.error(f"Error saving logs to file: {e}", exc_info=True)
        raise


def capture_logs_for_ongoing_incident(
    incident_id: str, started_at: datetime, save_to_file: bool = False
) -> dict[str, Any]:
    """
    Capture logs for an ongoing incident (up to current time).

    Args:
        incident_id: Incident UUID
        started_at: When the incident started
        save_to_file: Whether to save to file

    Returns:
        Capture results
    """
    return capture_downtime_logs(
        incident_id=incident_id,
        downtime_start=started_at,
        downtime_end=None,  # Still ongoing
        save_to_file=save_to_file,
    )


def capture_logs_for_resolved_incident(
    incident_id: str,
    started_at: datetime,
    ended_at: datetime,
    save_to_file: bool = True,  # Default to file for resolved incidents
) -> dict[str, Any]:
    """
    Capture logs for a resolved incident.

    Args:
        incident_id: Incident UUID
        started_at: When the incident started
        ended_at: When the incident ended
        save_to_file: Whether to save to file

    Returns:
        Capture results
    """
    return capture_downtime_logs(
        incident_id=incident_id,
        downtime_start=started_at,
        downtime_end=ended_at,
        save_to_file=save_to_file,
    )


def get_filtered_logs(
    logs: list[dict[str, Any]],
    level: str | None = None,
    logger_name: str | None = None,
    search_term: str | None = None,
) -> list[dict[str, Any]]:
    """
    Filter captured logs by various criteria.

    Args:
        logs: List of log entries
        level: Filter by log level (ERROR, WARNING, INFO, DEBUG)
        logger_name: Filter by logger name
        search_term: Search in log message

    Returns:
        Filtered log entries
    """
    filtered = logs

    if level:
        filtered = [log for log in filtered if log.get("level") == level]

    if logger_name:
        filtered = [log for log in filtered if log.get("logger") == logger_name]

    if search_term:
        filtered = [
            log
            for log in filtered
            if search_term.lower() in log.get("message", "").lower()
        ]

    return filtered


def analyze_logs_for_errors(logs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Analyze captured logs to identify errors and patterns.

    Args:
        logs: List of log entries

    Returns:
        Dict with analysis results (error_count, top_errors, etc.)
    """
    errors = [log for log in logs if log.get("level") == "ERROR"]
    warnings = [log for log in logs if log.get("level") == "WARNING"]

    # Count error types
    error_types: dict[str, int] = {}
    for error in errors:
        error_type = error.get("error_type", "Unknown")
        error_types[error_type] = error_types.get(error_type, 0) + 1

    # Find top error messages
    error_messages: dict[str, int] = {}
    for error in errors:
        msg = error.get("message", "")[:200]  # Truncate long messages
        error_messages[msg] = error_messages.get(msg, 0) + 1

    top_errors = sorted(error_messages.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_logs": len(logs),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "error_types": error_types,
        "top_errors": top_errors,
    }
