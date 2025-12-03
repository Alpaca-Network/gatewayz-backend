"""
Prometheus Remote Write integration for Railway Grafana stack.

This module handles pushing metrics to Prometheus via remote_write,
which is the recommended method for agent-based monitoring in Railway.

The Railway Grafana stack template comes with Prometheus pre-configured
to receive metrics via:
- HTTP remote_write endpoint: /api/v1/write
- Internal URL: http://prometheus:9090

This implementation uses protobuf format with Snappy compression as required
by the Prometheus remote write protocol.
"""

import asyncio
import logging
import socket
import time
from typing import Any

import httpx
import snappy
from prometheus_client import REGISTRY

from src.config import Config
from src.services.prometheus_pb2 import Label, Sample, TimeSeries, WriteRequest

logger = logging.getLogger(__name__)

# Protobuf is now always available via our custom implementation
PROTOBUF_AVAILABLE = True


def _get_instance_labels() -> dict[str, str]:
    """Get instance-identifying labels for all metrics."""
    hostname = socket.gethostname()
    return {
        "instance": hostname,
        "job": "gatewayz",
    }


def _serialize_metrics_to_protobuf(registry=REGISTRY) -> bytes:
    """
    Serialize metrics from registry to Prometheus remote write protobuf format.

    The remote write protocol requires:
    1. Metrics in protobuf format (WriteRequest message)
    2. Snappy compression of the protobuf data

    Args:
        registry: Prometheus metrics registry (default: REGISTRY)

    Returns:
        Snappy-compressed protobuf bytes ready for remote write
    """
    write_request = WriteRequest()
    instance_labels = _get_instance_labels()
    current_timestamp_ms = int(time.time() * 1000)

    # Iterate over all metrics in the registry
    for metric in registry.collect():
        for sample in metric.samples:
            # Create a new TimeSeries for each sample
            ts = TimeSeries()

            # Collect all labels for this sample
            all_labels: dict[str, str] = {}

            # Add the metric name as __name__ label
            all_labels["__name__"] = sample.name

            # Add instance labels
            for label_name, label_value in instance_labels.items():
                all_labels[label_name] = str(label_value)

            # Add sample-specific labels
            for label_name, label_value in sample.labels.items():
                all_labels[label_name] = str(label_value)

            # Sort labels lexicographically by name as required by Prometheus
            for label_name in sorted(all_labels.keys()):
                ts.labels.append(Label(name=label_name, value=all_labels[label_name]))

            # Add the sample value with current timestamp
            sample_obj = Sample(
                value=float(sample.value),
                timestamp=current_timestamp_ms,
            )
            ts.samples.append(sample_obj)

            write_request.timeseries.append(ts)

    # Serialize to protobuf bytes
    protobuf_data = write_request.SerializeToString()

    # Compress with Snappy (block format, not framed)
    compressed_data = snappy.compress(protobuf_data)

    return compressed_data


class PrometheusRemoteWriter:
    """
    Client for pushing Prometheus metrics via remote_write.

    This follows the Prometheus remote write protocol:
    - Metrics are collected from the local registry
    - Serialized to protobuf format (WriteRequest message)
    - Compressed with Snappy compression
    - Sent to the remote Prometheus instance via HTTP POST
    """

    def __init__(
        self,
        remote_write_url: str = None,
        push_interval: int = 30,
        enabled: bool = True,
    ):
        """
        Initialize the Prometheus remote writer.

        Args:
            remote_write_url: URL of Prometheus remote_write endpoint
                            Default: http://prometheus:9090/api/v1/write
            push_interval: Interval in seconds between metric pushes (default: 30s)
            enabled: Whether to enable remote write (default: True)
        """
        self.remote_write_url = remote_write_url or Config.PROMETHEUS_REMOTE_WRITE_URL
        self.push_interval = push_interval
        self.enabled = enabled and PROTOBUF_AVAILABLE
        self.client = None
        self._push_task = None
        self._last_push_time = 0
        self._push_count = 0
        self._push_errors = 0

        if enabled and not PROTOBUF_AVAILABLE:
            logger.warning(
                "Prometheus remote write requested but protobuf support not available. "
                "Install with: pip install python-snappy"
            )

        logger.info("Prometheus Remote Writer initialized")
        logger.info(f"  URL: {self.remote_write_url}")
        logger.info(f"  Push interval: {self.push_interval}s")
        logger.info(f"  Enabled: {self.enabled}")
        logger.info(f"  Protobuf support: {PROTOBUF_AVAILABLE}")

    async def start(self):
        """Start the background push task."""
        if not self.enabled:
            logger.info("Prometheus remote write is disabled")
            return

        self.client = httpx.AsyncClient(timeout=10.0)
        self._push_task = asyncio.create_task(self._push_loop())
        logger.info("Prometheus remote write task started")

    async def stop(self):
        """Stop the background push task and cleanup."""
        if self._push_task:
            self._push_task.cancel()
            try:
                await self._push_task
            except asyncio.CancelledError:
                pass

        if self.client:
            await self.client.aclose()

        logger.info(
            f"Prometheus remote write stopped "
            f"(pushed {self._push_count} times, {self._push_errors} errors)"
        )

    async def _push_loop(self):
        """Background task that pushes metrics at regular intervals."""
        while True:
            try:
                await asyncio.sleep(self.push_interval)
                await self.push_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in prometheus push loop: {e}")
                self._push_errors += 1

    async def push_metrics(self) -> bool:
        """
        Push current metrics to Prometheus remote_write endpoint.

        Serializes metrics to protobuf format with Snappy compression
        and sends them via HTTP POST to the remote write endpoint.

        Returns:
            True if push was successful, False otherwise
        """
        if not self.enabled or not self.client:
            return False

        try:
            # Serialize and compress metrics
            compressed_data = _serialize_metrics_to_protobuf(REGISTRY)

            # Send to remote write endpoint
            response = await self.client.post(
                self.remote_write_url,
                content=compressed_data,
                headers={
                    "Content-Encoding": "snappy",
                    "Content-Type": "application/x-protobuf",
                    "X-Prometheus-Remote-Write-Version": "0.1.0",
                },
            )

            response.raise_for_status()

            self._push_count += 1
            self._last_push_time = time.time()

            logger.debug(
                f"Successfully pushed metrics to {self.remote_write_url} "
                f"(status: {response.status_code})"
            )
            return True

        except httpx.HTTPStatusError as e:
            self._push_errors += 1
            logger.error(
                f"HTTP error pushing metrics to {self.remote_write_url}: "
                f"{e.response.status_code} - {e.response.text}"
            )
            return False
        except Exception as e:
            self._push_errors += 1
            logger.error(f"Error pushing metrics to {self.remote_write_url}: {e}")
            return False

    def get_stats(self) -> dict[str, Any]:
        """Get remote write statistics."""
        return {
            "enabled": self.enabled,
            "url": self.remote_write_url,
            "push_interval": self.push_interval,
            "push_count": self._push_count,
            "push_errors": self._push_errors,
            "last_push_time": self._last_push_time,
            "success_rate": (
                (self._push_count - self._push_errors) / self._push_count * 100
                if self._push_count > 0
                else 0
            ),
        }


# Global instance
prometheus_writer: PrometheusRemoteWriter | None = None


async def init_prometheus_remote_write():
    """Initialize Prometheus remote write on startup."""
    global prometheus_writer

    if not Config.PROMETHEUS_ENABLED:
        logger.info("Prometheus monitoring is disabled")
        return

    # Enable remote write if protobuf dependencies are available
    # Falls back to scraping via /metrics endpoint if protobuf is not available
    prometheus_writer = PrometheusRemoteWriter(
        remote_write_url=Config.PROMETHEUS_REMOTE_WRITE_URL,
        push_interval=30,  # Push every 30 seconds
        enabled=True,  # Enabled with protobuf support
    )

    if prometheus_writer.enabled:
        await prometheus_writer.start()
        logger.info(
            "Prometheus remote write enabled with protobuf support. "
            "Metrics will be pushed to remote write endpoint."
        )
    else:
        logger.info(
            "Prometheus remote write disabled (protobuf dependencies not available). "
            "Metrics available via /metrics scrape endpoint."
        )


async def shutdown_prometheus_remote_write():
    """Shutdown Prometheus remote write on shutdown."""
    global prometheus_writer

    if prometheus_writer:
        await prometheus_writer.stop()
        stats = prometheus_writer.get_stats()
        logger.info(f"Prometheus remote write stats: {stats}")


def get_prometheus_writer() -> PrometheusRemoteWriter | None:
    """Get the global Prometheus remote writer instance."""
    return prometheus_writer
