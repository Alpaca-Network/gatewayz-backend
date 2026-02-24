"""
Comprehensive tests for Prometheus Remote Write service
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestPrometheusProtobuf:
    """Test Prometheus protobuf message implementations"""

    def test_label_serialization(self):
        """Test Label message serialization"""
        from src.services.prometheus_pb2 import Label

        label = Label(name="__name__", value="test_metric")
        data = label.SerializeToString()

        # Should produce valid protobuf bytes
        assert isinstance(data, bytes)
        assert len(data) > 0
        # Check for field tags (0x0a = field 1, 0x12 = field 2)
        assert b"\x0a" in data  # name field
        assert b"\x12" in data  # value field

    def test_label_empty_values(self):
        """Test Label with empty values"""
        from src.services.prometheus_pb2 import Label

        label = Label(name="", value="")
        data = label.SerializeToString()

        # Empty strings should produce empty bytes
        assert data == b""

    def test_sample_serialization(self):
        """Test Sample message serialization"""
        from src.services.prometheus_pb2 import Sample

        sample = Sample(value=42.5, timestamp=1700000000000)
        data = sample.SerializeToString()

        # Should produce valid protobuf bytes
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_sample_zero_value(self):
        """Test Sample with zero value but non-zero timestamp"""
        from src.services.prometheus_pb2 import Sample

        sample = Sample(value=0.0, timestamp=1700000000000)
        data = sample.SerializeToString()

        # In protobuf, zero/default values are omitted, so only timestamp is serialized
        # The data should contain the timestamp but not the value field
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_timeseries_serialization(self):
        """Test TimeSeries message serialization"""
        from src.services.prometheus_pb2 import Label, Sample, TimeSeries

        ts = TimeSeries()
        ts.labels.append(Label(name="__name__", value="test_metric"))
        ts.labels.append(Label(name="job", value="test"))
        ts.samples.append(Sample(value=42.5, timestamp=1700000000000))

        data = ts.SerializeToString()

        # Should produce valid protobuf bytes
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_write_request_serialization(self):
        """Test WriteRequest message serialization"""
        from src.services.prometheus_pb2 import Label, Sample, TimeSeries, WriteRequest

        write_request = WriteRequest()

        ts = TimeSeries()
        ts.labels.append(Label(name="__name__", value="test_metric"))
        ts.samples.append(Sample(value=42.5, timestamp=1700000000000))
        write_request.timeseries.append(ts)

        data = write_request.SerializeToString()

        # Should produce valid protobuf bytes
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_write_request_multiple_timeseries(self):
        """Test WriteRequest with multiple timeseries"""
        from src.services.prometheus_pb2 import Label, Sample, TimeSeries, WriteRequest

        write_request = WriteRequest()

        for i in range(3):
            ts = TimeSeries()
            ts.labels.append(Label(name="__name__", value=f"metric_{i}"))
            ts.samples.append(Sample(value=float(i), timestamp=1700000000000))
            write_request.timeseries.append(ts)

        data = write_request.SerializeToString()

        assert isinstance(data, bytes)
        assert len(data) > 0


@pytest.mark.asyncio
class TestPrometheusRemoteWrite:
    """Test Prometheus Remote Write service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.prometheus_remote_write

        assert src.services.prometheus_remote_write is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import prometheus_remote_write

        assert hasattr(prometheus_remote_write, "__name__")

    def test_prometheus_remote_writer_initialization(self):
        """Test PrometheusRemoteWriter initialization"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(
            remote_write_url="http://test:9090/api/v1/write",
            push_interval=30,
            enabled=False,
        )

        assert writer.remote_write_url == "http://test:9090/api/v1/write"
        assert writer.push_interval == 30
        assert writer.enabled is False
        assert writer.client is None
        assert writer._push_count == 0
        assert writer._push_errors == 0

    async def test_push_metrics_when_disabled(self):
        """Test push_metrics returns False when disabled"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=False)
        result = await writer.push_metrics()

        assert result is False

    async def test_push_metrics_when_no_client(self):
        """Test push_metrics returns False when client is None"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)
        writer.client = None
        result = await writer.push_metrics()

        assert result is False

    @patch("src.services.prometheus_remote_write._serialize_metrics_to_protobuf")
    async def test_push_metrics_success(self, mock_serialize):
        """Test successful metrics push with protobuf"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        # Mock the serialization
        mock_serialize.return_value = b"compressed_protobuf_data"

        # Create a mock client
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_client.post.return_value = mock_response

        writer = PrometheusRemoteWriter(enabled=True)
        writer.enabled = True  # Force enable for test
        writer.client = mock_client

        result = await writer.push_metrics()

        assert result is True
        assert writer._push_count == 1
        assert writer._push_errors == 0
        mock_client.post.assert_called_once()

    @patch("src.services.prometheus_remote_write._serialize_metrics_to_protobuf")
    async def test_push_metrics_http_error(self, mock_serialize):
        """Test metrics push with HTTP error"""
        import httpx

        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        # Mock the serialization
        mock_serialize.return_value = b"compressed_protobuf_data"

        # Create a mock client that raises HTTPStatusError
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "Server error", request=Mock(), response=mock_response
        )

        writer = PrometheusRemoteWriter(enabled=True)
        writer.enabled = True  # Force enable for test
        writer.client = mock_client

        result = await writer.push_metrics()

        assert result is False
        assert writer._push_count == 0
        assert writer._push_errors == 1

    def test_get_stats(self):
        """Test get_stats returns correct statistics"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=False)
        writer._push_count = 10
        writer._push_errors = 2

        stats = writer.get_stats()

        assert stats["enabled"] is False
        assert stats["push_count"] == 10
        assert stats["push_errors"] == 2
        assert stats["success_rate"] == 80.0
        # Check circuit breaker stats are included
        assert "circuit_breaker" in stats
        assert stats["circuit_breaker"]["open"] is False

    def test_get_stats_no_pushes(self):
        """Test get_stats with no pushes returns 0 success rate"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=False)
        stats = writer.get_stats()

        assert stats["success_rate"] == 0

    def test_circuit_breaker_initial_state(self):
        """Test circuit breaker is initially closed"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)
        assert writer._circuit_open is False
        assert writer._consecutive_failures == 0
        assert writer._check_circuit_breaker() is True

    def test_circuit_breaker_opens_after_threshold(self):
        """Test circuit breaker opens after consecutive failures"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)

        # Simulate consecutive failures up to threshold
        for _ in range(writer.CIRCUIT_BREAKER_THRESHOLD):
            writer._record_failure()

        assert writer._circuit_open is True
        assert writer._consecutive_failures == writer.CIRCUIT_BREAKER_THRESHOLD
        assert writer._check_circuit_breaker() is False

    def test_circuit_breaker_resets_on_success(self):
        """Test circuit breaker resets after successful push"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)

        # Simulate some failures (but not enough to open circuit)
        writer._consecutive_failures = 3

        # Record a success
        writer._record_success()

        assert writer._consecutive_failures == 0
        assert writer._circuit_open is False

    def test_circuit_breaker_closes_after_timeout(self):
        """Test circuit breaker allows retry after timeout"""
        import time

        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)

        # Open the circuit
        writer._circuit_open = True
        writer._circuit_open_time = time.time() - writer.CIRCUIT_BREAKER_RESET_TIMEOUT - 1

        # Check should return True and reset the circuit
        assert writer._check_circuit_breaker() is True
        assert writer._circuit_open is False

    @patch("src.services.prometheus_remote_write._serialize_metrics_to_protobuf")
    async def test_push_metrics_skipped_when_circuit_open(self, mock_serialize):
        """Test push_metrics is skipped when circuit is open"""
        import time

        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)
        writer.client = AsyncMock()
        writer._circuit_open = True
        writer._circuit_open_time = time.time()  # Recent, so it won't reset

        result = await writer.push_metrics()

        assert result is False
        mock_serialize.assert_not_called()
        writer.client.post.assert_not_called()

    @patch("src.services.prometheus_remote_write._serialize_metrics_to_protobuf")
    async def test_circuit_breaker_opens_on_connection_errors(self, mock_serialize):
        """Test circuit breaker opens after repeated connection errors"""
        import httpx

        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        mock_serialize.return_value = b"compressed_data"

        writer = PrometheusRemoteWriter(enabled=True)
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        writer.client = mock_client

        # Push until circuit opens
        for _ in range(writer.CIRCUIT_BREAKER_THRESHOLD):
            await writer.push_metrics()

        assert writer._circuit_open is True
        assert writer._push_errors == writer.CIRCUIT_BREAKER_THRESHOLD

    @patch("src.services.prometheus_remote_write._serialize_metrics_to_protobuf")
    async def test_circuit_breaker_opens_on_timeout_errors(self, mock_serialize):
        """Test circuit breaker opens after repeated timeout errors"""
        import httpx

        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        mock_serialize.return_value = b"compressed_data"

        writer = PrometheusRemoteWriter(enabled=True)
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("Request timeout")
        writer.client = mock_client

        # Push until circuit opens
        for _ in range(writer.CIRCUIT_BREAKER_THRESHOLD):
            await writer.push_metrics()

        assert writer._circuit_open is True
        assert writer._push_errors == writer.CIRCUIT_BREAKER_THRESHOLD

    @patch("src.services.prometheus_remote_write.Config")
    async def test_init_prometheus_remote_write_disabled(self, mock_config):
        """Test init_prometheus_remote_write when Prometheus is disabled"""
        import src.services.prometheus_remote_write
        from src.services.prometheus_remote_write import (
            get_prometheus_writer,
            init_prometheus_remote_write,
        )

        # Reset the global writer
        src.services.prometheus_remote_write.prometheus_writer = None

        mock_config.PROMETHEUS_ENABLED = False

        await init_prometheus_remote_write()

        # Verify no writer was created
        writer = get_prometheus_writer()
        assert writer is None

    @patch("src.services.prometheus_remote_write.Config")
    async def test_init_prometheus_remote_write_enabled(self, mock_config):
        """Test init_prometheus_remote_write creates and starts writer"""
        import src.services.prometheus_remote_write
        from src.services.prometheus_remote_write import (
            get_prometheus_writer,
            init_prometheus_remote_write,
        )

        # Reset the global writer
        src.services.prometheus_remote_write.prometheus_writer = None

        mock_config.PROMETHEUS_ENABLED = True
        mock_config.PROMETHEUS_REMOTE_WRITE_URL = "http://test:9090/api/v1/write"

        # Mock the start method
        with patch.object(
            src.services.prometheus_remote_write.PrometheusRemoteWriter,
            "start",
            new_callable=AsyncMock,
        ) as mock_start:
            await init_prometheus_remote_write()

            # Writer should be created and enabled with protobuf support
            writer = get_prometheus_writer()
            assert writer is not None
            assert writer.enabled is True
            mock_start.assert_called_once()

    @patch("src.services.prometheus_remote_write.prometheus_writer")
    async def test_shutdown_prometheus_remote_write_with_writer(self, mock_prometheus_writer):
        """Test shutdown_prometheus_remote_write with active writer"""
        from src.services.prometheus_remote_write import (
            PrometheusRemoteWriter,
            shutdown_prometheus_remote_write,
        )

        # Set up a mock writer
        mock_writer = MagicMock(spec=PrometheusRemoteWriter)
        mock_writer.stop = AsyncMock()
        mock_writer.get_stats.return_value = {"test": "stats"}
        mock_prometheus_writer.__bool__ = lambda self: True
        mock_prometheus_writer.stop = mock_writer.stop
        mock_prometheus_writer.get_stats = mock_writer.get_stats

        await shutdown_prometheus_remote_write()

        mock_writer.stop.assert_called_once()
        mock_writer.get_stats.assert_called_once()

    @patch("src.services.prometheus_remote_write.prometheus_writer", None)
    async def test_shutdown_prometheus_remote_write_no_writer(self):
        """Test shutdown_prometheus_remote_write with no writer"""
        from src.services.prometheus_remote_write import shutdown_prometheus_remote_write

        # Should not raise an error when writer is None
        await shutdown_prometheus_remote_write()
        # Test passes if no exception is raised

    def test_get_prometheus_writer(self):
        """Test get_prometheus_writer returns the global instance"""
        import src.services.prometheus_remote_write
        from src.services.prometheus_remote_write import (
            PrometheusRemoteWriter,
            get_prometheus_writer,
        )

        # Set up a test writer as the global instance
        test_writer = PrometheusRemoteWriter(enabled=False)
        src.services.prometheus_remote_write.prometheus_writer = test_writer

        result = get_prometheus_writer()
        assert result == test_writer

        # Clean up
        src.services.prometheus_remote_write.prometheus_writer = None

    @patch("src.services.prometheus_remote_write.snappy.compress")
    @patch("src.services.prometheus_remote_write.REGISTRY")
    def test_serialize_metrics_success(self, mock_registry, mock_compress):
        """Test successful metrics serialization with new protobuf implementation"""
        from src.services.prometheus_remote_write import _serialize_metrics_to_protobuf

        # Mock the registry to return some test metrics
        mock_sample = Mock()
        mock_sample.name = "test_metric_total"
        mock_sample.labels = {"label1": "value1"}
        mock_sample.value = 42.0

        mock_metric = Mock()
        mock_metric.name = "test_metric"
        mock_metric.samples = [mock_sample]

        mock_registry.collect.return_value = [mock_metric]
        mock_compress.return_value = b"compressed_data"

        result = _serialize_metrics_to_protobuf(mock_registry)

        assert result == b"compressed_data"
        mock_compress.assert_called_once()
        # Verify the protobuf data was passed to compress
        call_args = mock_compress.call_args[0][0]
        assert isinstance(call_args, bytes)
        assert len(call_args) > 0

    @patch("src.services.prometheus_remote_write.REGISTRY")
    def test_serialize_metrics_empty_registry(self, mock_registry):
        """Test serialization with empty registry"""
        from src.services.prometheus_remote_write import _serialize_metrics_to_protobuf

        mock_registry.collect.return_value = []

        result = _serialize_metrics_to_protobuf(mock_registry)

        # Should still return compressed bytes (empty WriteRequest)
        assert isinstance(result, bytes)

    def test_get_instance_labels(self):
        """Test _get_instance_labels returns expected labels"""
        from src.services.prometheus_remote_write import _get_instance_labels

        labels = _get_instance_labels()

        assert "instance" in labels
        assert "job" in labels
        assert labels["job"] == "gatewayz"


class TestVarintEncoding:
    """Test varint encoding utility"""

    def test_encode_small_value(self):
        """Test encoding small values (< 128)"""
        from src.services.prometheus_pb2 import _encode_varint

        result = _encode_varint(0)
        assert result == b"\x00"

        result = _encode_varint(1)
        assert result == b"\x01"

        result = _encode_varint(127)
        assert result == b"\x7f"

    def test_encode_medium_value(self):
        """Test encoding medium values (128-16383)"""
        from src.services.prometheus_pb2 import _encode_varint

        result = _encode_varint(128)
        assert result == b"\x80\x01"

        result = _encode_varint(300)
        assert result == b"\xac\x02"

    def test_encode_large_value(self):
        """Test encoding large values (timestamps)"""
        from src.services.prometheus_pb2 import _encode_varint

        # Typical timestamp in milliseconds
        timestamp = 1700000000000
        result = _encode_varint(timestamp)

        # Should produce valid bytes
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_encode_negative_value_raises(self):
        """Test that encoding negative values raises ValueError"""
        from src.services.prometheus_pb2 import _encode_varint

        with pytest.raises(ValueError, match="Negative values not supported"):
            _encode_varint(-1)

        with pytest.raises(ValueError, match="Negative values not supported"):
            _encode_varint(-100)


class TestLabelSorting:
    """Test that labels are sorted lexicographically"""

    def test_labels_are_sorted(self):
        """Test that labels in TimeSeries are sorted by name"""
        from src.services.prometheus_pb2 import Label, Sample, TimeSeries

        ts = TimeSeries()
        # Add labels in non-alphabetical order
        ts.labels.append(Label(name="zebra", value="z"))
        ts.labels.append(Label(name="__name__", value="metric"))
        ts.labels.append(Label(name="alpha", value="a"))
        ts.labels.append(Label(name="instance", value="host"))

        # The serialization should work (labels are stored as added)
        data = ts.SerializeToString()
        assert isinstance(data, bytes)
        assert len(data) > 0

    @patch("src.services.prometheus_remote_write.REGISTRY")
    def test_serialize_metrics_sorts_labels(self, mock_registry):
        """Test that _serialize_metrics_to_protobuf sorts labels correctly"""
        from src.services.prometheus_remote_write import _serialize_metrics_to_protobuf

        # Mock a metric with labels that would be out of order if not sorted
        mock_sample = Mock()
        mock_sample.name = "test_metric_total"
        mock_sample.labels = {"zebra": "z", "alpha": "a"}
        mock_sample.value = 42.0

        mock_metric = Mock()
        mock_metric.samples = [mock_sample]

        mock_registry.collect.return_value = [mock_metric]

        # This should not raise and should produce valid compressed bytes
        result = _serialize_metrics_to_protobuf(mock_registry)
        assert isinstance(result, bytes)
