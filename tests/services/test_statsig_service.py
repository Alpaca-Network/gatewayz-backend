"""
Comprehensive tests for Statsig Service service
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestStatsigService:
    """Test Statsig Service service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.statsig_service

        assert src.services.statsig_service is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import statsig_service

        assert hasattr(statsig_service, "__name__")

    def test_statsig_service_class_exists(self):
        """Test StatsigService class is importable"""
        from src.services.statsig_service import StatsigService

        assert StatsigService is not None

    def test_statsig_service_singleton_exists(self):
        """Test statsig_service singleton is created"""
        from src.services.statsig_service import statsig_service

        assert statsig_service is not None

    def test_statsig_service_has_required_methods(self):
        """Test StatsigService has all required methods"""
        from src.services.statsig_service import StatsigService

        service = StatsigService()
        assert hasattr(service, "initialize")
        assert hasattr(service, "log_event")
        assert hasattr(service, "log_session_start")
        assert hasattr(service, "log_session_end")
        assert hasattr(service, "get_feature_flag")
        assert hasattr(service, "flush")
        assert hasattr(service, "shutdown")

    def test_log_event_returns_true_in_fallback_mode(self):
        """Test log_event returns True in fallback mode (no server key)"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            # Service is not enabled but should still return True for fallback logging
            result = service.log_event(
                user_id="test_user",
                event_name="test_event",
                value="test_value",
                metadata={"key": "value"},
            )
            assert result is True

    def test_flush_returns_true_when_not_enabled(self):
        """Test flush returns True when service is not enabled"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            result = service.flush()
            assert result is True

    def test_get_feature_flag_returns_default_when_not_enabled(self):
        """Test get_feature_flag returns default value when not enabled"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            result = service.get_feature_flag(
                flag_name="test_flag", user_id="test_user", default_value=True
            )
            assert result is True

            result = service.get_feature_flag(
                flag_name="test_flag", user_id="test_user", default_value=False
            )
            assert result is False


class TestStatsigServiceInitialization:
    """Test Statsig Service initialization behavior"""

    @pytest.mark.asyncio
    async def test_initialize_without_server_key(self):
        """Test initialization gracefully handles missing server key"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            await service.initialize()

            assert service._initialized is True
            assert service.enabled is False
            assert service.statsig is None

    @pytest.mark.asyncio
    async def test_initialize_with_missing_sdk(self):
        """Test initialization handles missing statsig_python_core package"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {"STATSIG_SERVER_SECRET_KEY": "test_key"}):
            with patch.dict("sys.modules", {"statsig_python_core": None}):
                service = StatsigService()
                # The import will fail and it should fall back gracefully
                await service.initialize()
                # After import error, service should be initialized but not enabled
                assert service._initialized is True


class TestStatsigServiceShutdown:
    """Test Statsig Service shutdown behavior"""

    @pytest.mark.asyncio
    async def test_shutdown_when_not_enabled(self):
        """Test shutdown works when service is not enabled"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            await service.initialize()
            await service.shutdown()

            assert service._initialized is False
            assert service.enabled is False

    @pytest.mark.asyncio
    async def test_shutdown_calls_wait(self):
        """Test shutdown calls .wait() on the statsig SDK"""
        from src.services.statsig_service import StatsigService

        service = StatsigService()
        service._initialized = True
        service.enabled = True

        # Mock the statsig SDK
        mock_shutdown_result = MagicMock()
        mock_statsig = MagicMock()
        mock_statsig.shutdown.return_value = mock_shutdown_result
        service.statsig = mock_statsig

        await service.shutdown()

        # Verify shutdown was called and .wait() was called on the result
        mock_statsig.shutdown.assert_called_once()
        mock_shutdown_result.wait.assert_called_once_with(timeout=10)


class TestStatsigServiceFlush:
    """Test Statsig Service flush behavior"""

    def test_flush_when_not_enabled(self):
        """Test flush returns True when service is not enabled"""
        from src.services.statsig_service import StatsigService

        service = StatsigService()
        service.enabled = False

        result = service.flush()
        assert result is True

    def test_flush_calls_wait_when_flush_exists(self):
        """Test flush calls .wait() on the SDK flush method"""
        from src.services.statsig_service import StatsigService

        service = StatsigService()
        service._initialized = True
        service.enabled = True

        # Mock the statsig SDK with flush method
        mock_flush_result = MagicMock()
        mock_statsig = MagicMock()
        mock_statsig.flush.return_value = mock_flush_result
        service.statsig = mock_statsig

        result = service.flush()

        # Verify flush was called and .wait() was called on the result
        mock_statsig.flush.assert_called_once()
        mock_flush_result.wait.assert_called_once_with(timeout=5)
        assert result is True

    def test_flush_handles_exception_gracefully(self):
        """Test flush returns False when exception occurs"""
        from src.services.statsig_service import StatsigService

        service = StatsigService()
        service._initialized = True
        service.enabled = True

        # Mock the statsig SDK to raise an exception
        mock_statsig = MagicMock()
        mock_statsig.flush.side_effect = Exception("Network error")
        service.statsig = mock_statsig

        result = service.flush()
        assert result is False


class TestStatsigServiceBatchingConfig:
    """Test Statsig Service batching configuration"""

    @pytest.mark.asyncio
    async def test_initialization_sets_batching_options(self):
        """Test that initialization configures event batching options"""
        from src.services.statsig_service import StatsigService

        # Mock the statsig_python_core module
        mock_options = MagicMock()
        mock_statsig_class = MagicMock()
        mock_statsig_instance = MagicMock()
        mock_statsig_instance.initialize.return_value = MagicMock(wait=MagicMock())
        mock_statsig_class.return_value = mock_statsig_instance

        mock_module = MagicMock()
        mock_module.Statsig = mock_statsig_class
        mock_module.StatsigUser = MagicMock()
        mock_module.StatsigOptions = MagicMock(return_value=mock_options)

        with patch.dict(
            "os.environ", {"STATSIG_SERVER_SECRET_KEY": "test_key", "APP_ENV": "production"}
        ):
            with patch.dict("sys.modules", {"statsig_python_core": mock_module}):
                service = StatsigService()
                await service.initialize()

                # Verify batching options were set
                assert mock_options.event_logging_flush_interval_ms == 10000
                assert mock_options.event_logging_max_queue_size == 50
                assert mock_options.environment == "production"


class TestStatsigServiceSessionTracking:
    """Test Statsig Service session tracking for DAU/WAU/MAU"""

    def test_log_session_start_in_fallback_mode(self):
        """Test log_session_start returns True in fallback mode"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            result = service.log_session_start(
                user_id="test_user", platform="web", metadata={"version": "1.0.0"}
            )
            assert result is True

    def test_log_session_start_includes_platform(self):
        """Test log_session_start includes platform in metadata"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            # Patch log_event to capture the call
            with patch.object(service, "log_event", return_value=True) as mock_log:
                service.log_session_start(
                    user_id="test_user", platform="ios", metadata={"version": "2.0.0"}
                )
                mock_log.assert_called_once_with(
                    user_id="test_user",
                    event_name="session_start",
                    metadata={"platform": "ios", "version": "2.0.0"},
                )

    def test_log_session_end_in_fallback_mode(self):
        """Test log_session_end returns True in fallback mode"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            result = service.log_session_end(user_id="test_user", session_duration_seconds=300)
            assert result is True

    def test_log_session_end_includes_duration(self):
        """Test log_session_end includes duration in metadata"""
        from src.services.statsig_service import StatsigService

        with patch.dict("os.environ", {}, clear=True):
            service = StatsigService()
            with patch.object(service, "log_event", return_value=True) as mock_log:
                service.log_session_end(user_id="test_user", session_duration_seconds=600)
                mock_log.assert_called_once_with(
                    user_id="test_user",
                    event_name="session_end",
                    metadata={"duration_seconds": "600"},
                )
