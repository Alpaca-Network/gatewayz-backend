"""
Comprehensive tests for Startup service
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock


class TestStartup:
    """Test Startup service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.startup
        assert src.services.startup is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import startup
        assert hasattr(startup, '__name__')

    @pytest.mark.asyncio
    async def test_lifespan_startup_success(self):
        """Test successful lifespan startup"""
        from src.services.startup import lifespan

        mock_app = MagicMock()

        with patch('src.services.startup.Config') as mock_config, \
             patch('src.services.startup.get_supabase_client') as mock_get_supabase, \
             patch('src.services.startup.initialize_fal_cache_from_catalog') as mock_fal_cache, \
             patch('src.services.startup.init_tempo_otlp') as mock_tempo, \
             patch('src.services.startup.init_tempo_otlp_fastapi') as mock_tempo_fastapi, \
             patch('src.services.startup.init_prometheus_remote_write') as mock_prometheus_init, \
             patch('src.services.startup.health_monitor') as mock_health_monitor, \
             patch('src.services.startup.availability_service') as mock_availability, \
             patch('src.services.startup.get_pool_stats') as mock_pool_stats, \
             patch('src.services.startup.get_cache') as mock_cache, \
             patch('src.services.startup.initialize_autonomous_monitor') as mock_auto_monitor, \
             patch('src.services.startup.get_autonomous_monitor') as mock_get_auto_monitor, \
             patch('src.services.startup.shutdown_prometheus_remote_write') as mock_prometheus_shutdown, \
             patch('src.services.startup.clear_connection_pools') as mock_clear_pools, \
             patch('src.services.startup.os.environ.get') as mock_env_get:

            # Setup mocks
            mock_config.validate_critical_env_vars.return_value = (True, [])
            mock_health_monitor.start_monitoring = AsyncMock()
            mock_health_monitor.stop_monitoring = AsyncMock()
            mock_availability.start_monitoring = AsyncMock()
            mock_availability.stop_monitoring = AsyncMock()
            mock_prometheus_init.return_value = AsyncMock()
            mock_prometheus_shutdown.return_value = AsyncMock()
            mock_auto_monitor.return_value = AsyncMock()
            mock_pool_stats.return_value = {"total": 0, "active": 0}
            mock_env_get.side_effect = lambda key, default=None: {
                "ERROR_MONITORING_ENABLED": "true",
                "AUTO_FIX_ENABLED": "true",
                "ERROR_MONITOR_INTERVAL": "300",
            }.get(key, default)

            autonomous_monitor_mock = MagicMock()
            autonomous_monitor_mock.stop = AsyncMock()
            mock_get_auto_monitor.return_value = autonomous_monitor_mock

            # Run lifespan
            async with lifespan(mock_app):
                # Verify startup calls
                mock_config.validate_critical_env_vars.assert_called_once()
                mock_get_supabase.assert_called_once()
                mock_health_monitor.start_monitoring.assert_called_once()
                mock_availability.start_monitoring.assert_called_once()

            # Verify shutdown calls
            mock_availability.stop_monitoring.assert_called_once()
            mock_health_monitor.stop_monitoring.assert_called_once()
            autonomous_monitor_mock.stop.assert_called_once()
            mock_clear_pools.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_startup_critical_env_vars_missing(self):
        """Test lifespan startup fails with missing critical env vars"""
        from src.services.startup import lifespan

        mock_app = MagicMock()

        with patch('src.services.startup.Config') as mock_config:
            mock_config.validate_critical_env_vars.return_value = (False, ["SUPABASE_URL", "SUPABASE_KEY"])

            # Should raise RuntimeError
            with pytest.raises(RuntimeError, match="Missing required environment variables"):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio
    async def test_lifespan_startup_supabase_init_fails(self):
        """Test lifespan startup fails when Supabase init fails"""
        from src.services.startup import lifespan

        mock_app = MagicMock()

        with patch('src.services.startup.Config') as mock_config, \
             patch('src.services.startup.get_supabase_client') as mock_get_supabase, \
             patch('src.services.startup.sentry_sdk'):

            mock_config.validate_critical_env_vars.return_value = (True, [])
            mock_get_supabase.side_effect = Exception("Connection refused")

            # Should raise RuntimeError
            with pytest.raises(RuntimeError, match="Cannot start application: Database initialization failed"):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio
    async def test_lifespan_startup_continues_if_monitoring_fails(self):
        """Test lifespan startup continues even if optional monitoring fails"""
        from src.services.startup import lifespan

        mock_app = MagicMock()

        with patch('src.services.startup.Config') as mock_config, \
             patch('src.services.startup.get_supabase_client') as mock_get_supabase, \
             patch('src.services.startup.initialize_fal_cache_from_catalog') as mock_fal_cache, \
             patch('src.services.startup.health_monitor') as mock_health_monitor, \
             patch('src.services.startup.availability_service') as mock_availability, \
             patch('src.services.startup.get_pool_stats') as mock_pool_stats, \
             patch('src.services.startup.get_cache') as mock_cache, \
             patch('src.services.startup.clear_connection_pools') as mock_clear_pools:

            mock_config.validate_critical_env_vars.return_value = (True, [])
            mock_health_monitor.start_monitoring = AsyncMock(side_effect=Exception("Health monitor failed"))
            mock_health_monitor.stop_monitoring = AsyncMock()
            mock_availability.start_monitoring = AsyncMock()
            mock_availability.stop_monitoring = AsyncMock()
            mock_pool_stats.return_value = {"total": 0, "active": 0}

            # Should not raise - monitoring failures are non-fatal
            async with lifespan(mock_app):
                mock_get_supabase.assert_called_once()
                # Health monitor failed but startup continued
                mock_availability.start_monitoring.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_services(self):
        """Test initialize_services function"""
        from src.services.startup import initialize_services

        with patch('src.services.startup.health_monitor') as mock_health_monitor, \
             patch('src.services.startup.availability_service') as mock_availability:

            mock_health_monitor.start_monitoring = AsyncMock()
            mock_availability.start_monitoring = AsyncMock()

            await initialize_services()

            mock_health_monitor.start_monitoring.assert_called_once()
            mock_availability.start_monitoring.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_services_handles_failure(self):
        """Test initialize_services handles failures"""
        from src.services.startup import initialize_services

        with patch('src.services.startup.health_monitor') as mock_health_monitor:
            mock_health_monitor.start_monitoring = AsyncMock(side_effect=Exception("Init failed"))

            with pytest.raises(Exception, match="Init failed"):
                await initialize_services()

    @pytest.mark.asyncio
    async def test_shutdown_services(self):
        """Test shutdown_services function"""
        from src.services.startup import shutdown_services

        with patch('src.services.startup.health_monitor') as mock_health_monitor, \
             patch('src.services.startup.availability_service') as mock_availability:

            mock_health_monitor.stop_monitoring = AsyncMock()
            mock_availability.stop_monitoring = AsyncMock()

            await shutdown_services()

            mock_availability.stop_monitoring.assert_called_once()
            mock_health_monitor.stop_monitoring.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_services_handles_failure(self):
        """Test shutdown_services handles failures gracefully"""
        from src.services.startup import shutdown_services

        with patch('src.services.startup.health_monitor') as mock_health_monitor, \
             patch('src.services.startup.availability_service') as mock_availability:

            mock_health_monitor.stop_monitoring = AsyncMock(side_effect=Exception("Shutdown failed"))
            mock_availability.stop_monitoring = AsyncMock()

            # Should not raise - errors are logged
            await shutdown_services()
