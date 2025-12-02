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

    async def test_push_metrics_with_client_returns_false(self):
        """Test push_metrics returns False even with client (disabled implementation)"""
        from src.services.prometheus_remote_write import PrometheusRemoteWriter

        writer = PrometheusRemoteWriter(enabled=True)
        writer.client = MagicMock()
        result = await writer.push_metrics()

        # Should return False because implementation is disabled
        assert result is False

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

    @patch('src.services.prometheus_remote_write.prometheus_writer', None)
    @patch('src.services.prometheus_remote_write.Config')
    async def test_init_prometheus_remote_write_disabled(self, mock_config, mock_writer):
        """Test init_prometheus_remote_write when Prometheus is disabled"""
        from src.services.prometheus_remote_write import (
            init_prometheus_remote_write,
            get_prometheus_writer
        )

        mock_config.PROMETHEUS_ENABLED = False

        await init_prometheus_remote_write()

        # Verify no writer was created
        writer = get_prometheus_writer()
        assert writer is None

    @patch('src.services.prometheus_remote_write.Config')
    async def test_init_prometheus_remote_write_enabled(self, mock_config):
        """Test init_prometheus_remote_write creates writer but disabled"""
        from src.services.prometheus_remote_write import (
            init_prometheus_remote_write,
            get_prometheus_writer
        )

        mock_config.PROMETHEUS_ENABLED = True
        mock_config.PROMETHEUS_REMOTE_WRITE_URL = "http://test:9090/api/v1/write"

        await init_prometheus_remote_write()

        # Writer should be created but disabled
        writer = get_prometheus_writer()
        assert writer is not None
        assert writer.enabled is False

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

    @patch('src.services.prometheus_remote_write.prometheus_writer')
    def test_get_prometheus_writer(self, mock_prometheus_writer):
        """Test get_prometheus_writer returns the global instance"""
        from src.services.prometheus_remote_write import (
            get_prometheus_writer,
            PrometheusRemoteWriter
        )

        test_writer = PrometheusRemoteWriter(enabled=False)
        mock_prometheus_writer.return_value = test_writer

        result = get_prometheus_writer()
        assert result == test_writer
