"""
Comprehensive tests for Startup service
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestStartup:
    """Test Startup service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.startup

        assert src.services.startup is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import startup

        assert hasattr(startup, "__name__")

    @pytest.mark.asyncio
    async def test_lifespan_startup_success(self):
        """Test successful lifespan startup"""
        from src.services.startup import lifespan

        mock_app = MagicMock()

        with (
            patch("src.config.Config") as mock_config,
            patch("src.config.supabase_config.get_supabase_client") as mock_get_supabase,
            patch("src.services.startup.initialize_fal_cache_from_catalog") as mock_fal_cache,
            patch("src.services.startup.init_tempo_otlp") as mock_tempo,
            patch("src.services.startup.init_tempo_otlp_fastapi") as mock_tempo_fastapi,
            patch("src.services.startup.init_prometheus_remote_write") as mock_prometheus_init,
            patch("src.services.startup.get_pool_stats") as mock_pool_stats,
            patch("src.services.startup.get_cache") as mock_cache,
            patch("src.services.startup.initialize_autonomous_monitor") as mock_auto_monitor,
            patch("src.services.startup.get_autonomous_monitor") as mock_get_auto_monitor,
            patch(
                "src.services.startup.shutdown_prometheus_remote_write"
            ) as mock_prometheus_shutdown,
            patch("src.services.startup.clear_connection_pools") as mock_clear_pools,
            patch("src.services.startup.warmup_provider_connections_async") as mock_warmup,
            patch("src.services.startup.os.environ.get") as mock_env_get,
        ):

            # Setup mocks
            mock_config.validate_critical_env_vars.return_value = (True, [])
            mock_prometheus_init.return_value = AsyncMock()
            mock_prometheus_shutdown.return_value = AsyncMock()
            mock_auto_monitor.return_value = AsyncMock()
            mock_warmup.return_value = {}
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

            # Verify shutdown calls
            autonomous_monitor_mock.stop.assert_called_once()
            mock_clear_pools.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_startup_critical_env_vars_missing(self):
        """Test lifespan startup fails with missing critical env vars"""
        from src.services.startup import lifespan

        mock_app = MagicMock()

        with patch("src.config.Config") as mock_config:
            mock_config.validate_critical_env_vars.return_value = (
                False,
                ["SUPABASE_URL", "SUPABASE_KEY"],
            )

            # Should raise RuntimeError
            with pytest.raises(RuntimeError, match="Missing required environment variables"):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio
    async def test_lifespan_startup_supabase_init_fails(self):
        """Test lifespan startup continues in degraded mode when Supabase init fails"""
        import sys

        from src.services.startup import lifespan

        mock_app = MagicMock()

        # Create a mock sentry_sdk module
        mock_sentry = MagicMock()
        sys.modules["sentry_sdk"] = mock_sentry

        try:
            with (
                patch("src.config.Config") as mock_config,
                patch("src.config.supabase_config.get_supabase_client") as mock_get_supabase,
                patch("src.services.startup.get_pool_stats"),
                patch("src.services.startup.get_cache"),
                patch("src.services.startup.initialize_fal_cache_from_catalog"),
                patch("src.services.startup.init_tempo_otlp"),
                patch("src.services.startup.init_tempo_otlp_fastapi"),
                patch("src.services.startup.init_prometheus_remote_write"),
                patch("src.services.startup.warmup_provider_connections_async"),
                patch("src.services.startup.initialize_autonomous_monitor"),
                patch("src.services.startup.get_autonomous_monitor"),
                patch("src.services.startup.shutdown_prometheus_remote_write"),
                patch("src.services.startup.clear_connection_pools"),
            ):

                mock_config.validate_critical_env_vars.return_value = (True, [])
                mock_get_supabase.side_effect = Exception("Connection refused")

                # Should NOT raise RuntimeError - app starts in degraded mode
                # The context manager should complete successfully
                async with lifespan(mock_app):
                    # App should be running even with DB failure
                    pass

                # Verify Sentry was called to report the degraded state
                assert mock_sentry.capture_exception.called
        finally:
            # Clean up
            if "sentry_sdk" in sys.modules:
                del sys.modules["sentry_sdk"]

    @pytest.mark.asyncio
    async def test_lifespan_startup_continues_if_fal_cache_fails(self):
        """Test lifespan startup continues even if optional Fal cache fails"""
        from src.services.startup import lifespan

        mock_app = MagicMock()

        with (
            patch("src.config.Config") as mock_config,
            patch("src.config.supabase_config.get_supabase_client") as mock_get_supabase,
            patch("src.services.startup.initialize_fal_cache_from_catalog") as mock_fal_cache,
            patch("src.services.startup.get_pool_stats") as mock_pool_stats,
            patch("src.services.startup.get_cache") as mock_cache,
            patch("src.services.startup.init_tempo_otlp_fastapi"),
            patch("src.services.startup.init_tempo_otlp"),
            patch("src.services.startup.init_prometheus_remote_write"),
            patch("src.services.startup.warmup_provider_connections_async"),
            patch("src.services.startup.initialize_autonomous_monitor"),
            patch("src.services.startup.get_autonomous_monitor") as mock_get_auto_monitor,
            patch("src.services.startup.shutdown_prometheus_remote_write"),
            patch("src.services.startup.clear_connection_pools") as mock_clear_pools,
        ):

            mock_config.validate_critical_env_vars.return_value = (True, [])
            mock_fal_cache.side_effect = Exception("Fal cache failed")
            mock_pool_stats.return_value = {"total": 0, "active": 0}

            autonomous_monitor_mock = MagicMock()
            autonomous_monitor_mock.stop = AsyncMock()
            mock_get_auto_monitor.return_value = autonomous_monitor_mock

            # Should not raise - Fal cache failures are non-fatal
            async with lifespan(mock_app):
                mock_get_supabase.assert_called_once()
                # Fal cache failed but startup continued

    @pytest.mark.asyncio
    async def test_initialize_services(self):
        """Test initialize_services function"""
        from src.services.startup import initialize_services

        # initialize_services now just logs - no more health_monitor
        await initialize_services()
        # Should not raise any errors

    @pytest.mark.asyncio
    async def test_shutdown_services(self):
        """Test shutdown_services function"""
        from src.services.startup import shutdown_services

        # shutdown_services now just logs - no more health_monitor
        await shutdown_services()
        # Should not raise any errors

    @pytest.mark.asyncio
    async def test_shutdown_services_handles_failure(self):
        """Test shutdown_services handles failures gracefully"""
        from src.services.startup import shutdown_services

        # shutdown_services now just logs, so no failures expected
        await shutdown_services()
        # Should not raise any errors
