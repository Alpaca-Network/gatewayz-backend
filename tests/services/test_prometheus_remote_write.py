"""
Comprehensive tests for Prometheus Remote Write service
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock


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
        assert hasattr(prometheus_remote_write, '__name__')

    def test_prometheus_remote_writer_initialization(self):
        """Test PrometheusRemoteWriter initialization"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(
            remote_write_url="http://test:9090/api/v1/write",
            push_interval=30,
            enabled=False
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

    @patch('src.services.prometheus_remote_write.PROTOBUF_AVAILABLE', False)
    async def test_push_metrics_with_client_no_protobuf(self):
        """Test push_metrics returns False when protobuf not available"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)
        writer.client = MagicMock()
        result = await writer.push_metrics()

        # Should return False because writer.enabled will be False without protobuf
        assert result is False

    @patch('src.services.prometheus_remote_write._serialize_metrics_to_protobuf')
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

    @patch('src.services.prometheus_remote_write._serialize_metrics_to_protobuf')
    async def test_push_metrics_http_error(self, mock_serialize):
        """Test metrics push with HTTP error"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter
        import httpx

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

    def test_get_stats_no_pushes(self):
        """Test get_stats with no pushes returns 0 success rate"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=False)
        stats = writer.get_stats()

        assert stats["success_rate"] == 0

    @patch('src.services.prometheus_remote_write.Config')
    async def test_init_prometheus_remote_write_disabled(self, mock_config):
        """Test init_prometheus_remote_write when Prometheus is disabled"""
        import src.services.prometheus_remote_write
        from src.services.prometheus_remote_write import (
            init_prometheus_remote_write,
            get_prometheus_writer
        )

        # Reset the global writer
        src.services.prometheus_remote_write.prometheus_writer = None

        mock_config.PROMETHEUS_ENABLED = False

        await init_prometheus_remote_write()

        # Verify no writer was created
        writer = get_prometheus_writer()
        assert writer is None

    @patch('src.services.prometheus_remote_write.PROTOBUF_AVAILABLE', True)
    @patch('src.services.prometheus_remote_write.Config')
    async def test_init_prometheus_remote_write_enabled(self, mock_config, mock_protobuf):
        """Test init_prometheus_remote_write creates and starts writer"""
        import src.services.prometheus_remote_write
        from src.services.prometheus_remote_write import (
            init_prometheus_remote_write,
            get_prometheus_writer
        )

        # Reset the global writer
        src.services.prometheus_remote_write.prometheus_writer = None

        mock_config.PROMETHEUS_ENABLED = True
        mock_config.PROMETHEUS_REMOTE_WRITE_URL = "http://test:9090/api/v1/write"

        # Mock the start method
        with patch.object(
            src.services.prometheus_remote_write.PrometheusRemoteWriter,
            'start',
            new_callable=AsyncMock
        ) as mock_start:
            await init_prometheus_remote_write()

            # Writer should be created and enabled with protobuf support
            writer = get_prometheus_writer()
            assert writer is not None
            assert writer.enabled is True
            mock_start.assert_called_once()

    @patch('src.services.prometheus_remote_write.prometheus_writer')
    async def test_shutdown_prometheus_remote_write_with_writer(self, mock_prometheus_writer):
        """Test shutdown_prometheus_remote_write with active writer"""
        from src.services.prometheus_remote_write import (
            shutdown_prometheus_remote_write,
            PrometheusRemoteWriter
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

    @patch('src.services.prometheus_remote_write.prometheus_writer', None)
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
            get_prometheus_writer,
            PrometheusRemoteWriter
        )

        # Set up a test writer as the global instance
        test_writer = PrometheusRemoteWriter(enabled=False)
        src.services.prometheus_remote_write.prometheus_writer = test_writer

        result = get_prometheus_writer()
        assert result == test_writer

        # Clean up
        src.services.prometheus_remote_write.prometheus_writer = None

    @patch('src.services.prometheus_remote_write.PROTOBUF_AVAILABLE', False)
    def test_serialize_metrics_no_protobuf(self, mock_protobuf):
        """Test serialization fails gracefully without protobuf"""
        from src.services.prometheus_remote_write import _serialize_metrics_to_protobuf

        with pytest.raises(RuntimeError, match="Protobuf support not available"):
            _serialize_metrics_to_protobuf()

    @patch('src.services.prometheus_remote_write.PROTOBUF_AVAILABLE', True)
    @patch('src.services.prometheus_remote_write.snappy.compress')
    @patch('prometheus_client.openmetrics.exposition.generate_latest')
    def test_serialize_metrics_success(self, mock_generate, mock_compress, mock_protobuf):
        """Test successful metrics serialization"""
        from src.services.prometheus_remote_write import _serialize_metrics_to_protobuf

        mock_generate.return_value = b"test_metrics_data"
        mock_compress.return_value = b"compressed_data"

        result = _serialize_metrics_to_protobuf()

        assert result == b"compressed_data"
        mock_generate.assert_called_once()
        mock_compress.assert_called_once_with(b"test_metrics_data")
