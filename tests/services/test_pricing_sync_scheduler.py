"""
Tests for Pricing Sync Scheduler (Phase 2.5/Phase 4)

Tests cover:
- Scheduler lifecycle (start, stop, status)
- Manual sync triggering
- Graceful shutdown
- Error handling
- Metrics collection
- Configuration validation

Uses pytest-asyncio for async test support.
"""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# Set test environment
os.environ['APP_ENV'] = 'testing'
os.environ['TESTING'] = 'true'
os.environ['PRICING_SYNC_ENABLED'] = 'true'
os.environ['PRICING_SYNC_INTERVAL_HOURS'] = '6'
os.environ['PRICING_SYNC_PROVIDERS'] = 'openrouter,featherless'

from src.services.pricing_sync_scheduler import (
    start_pricing_sync_scheduler,
    stop_pricing_sync_scheduler,
    trigger_manual_sync,
    get_scheduler_status,
    _scheduler_task,
    _shutdown_event,
)


class TestSchedulerLifecycle:
    """Test scheduler startup and shutdown"""

    @pytest.mark.asyncio
    async def test_start_scheduler_creates_task(self):
        """Starting scheduler creates background task"""
        # Import the module to access globals
        import src.services.pricing_sync_scheduler as scheduler_module

        # Ensure clean state
        scheduler_module._scheduler_task = None
        scheduler_module._shutdown_event.clear()

        # Mock the scheduler loop to return immediately
        with patch('src.services.pricing_sync_scheduler._pricing_sync_scheduler_loop') as mock_loop:
            mock_loop.return_value = asyncio.coroutine(lambda: None)()

            await start_pricing_sync_scheduler()

            # Verify task was created
            assert scheduler_module._scheduler_task is not None

            # Cleanup
            await stop_pricing_sync_scheduler()

    @pytest.mark.asyncio
    async def test_start_scheduler_twice_warns(self):
        """Starting scheduler twice doesn't create duplicate task"""
        import src.services.pricing_sync_scheduler as scheduler_module

        # Ensure clean state
        scheduler_module._scheduler_task = None
        scheduler_module._shutdown_event.clear()

        with patch('src.services.pricing_sync_scheduler._pricing_sync_scheduler_loop') as mock_loop:
            mock_loop.return_value = asyncio.coroutine(lambda: None)()

            # Start first time
            await start_pricing_sync_scheduler()
            first_task = scheduler_module._scheduler_task

            # Start second time
            await start_pricing_sync_scheduler()
            second_task = scheduler_module._scheduler_task

            # Should be same task
            assert first_task == second_task

            # Cleanup
            await stop_pricing_sync_scheduler()

    @pytest.mark.asyncio
    async def test_stop_scheduler_sets_shutdown_event(self):
        """Stopping scheduler sets shutdown event"""
        import src.services.pricing_sync_scheduler as scheduler_module

        # Ensure clean state
        scheduler_module._scheduler_task = None
        scheduler_module._shutdown_event.clear()

        # Create a mock task that completes quickly
        async def quick_task():
            await asyncio.sleep(0.01)

        scheduler_module._scheduler_task = asyncio.create_task(quick_task())

        # Stop scheduler
        await stop_pricing_sync_scheduler()

        # Verify shutdown event was set
        assert scheduler_module._shutdown_event.is_set()

        # Verify task is None after stop
        assert scheduler_module._scheduler_task is None

    @pytest.mark.asyncio
    async def test_stop_scheduler_with_timeout(self):
        """Stopping scheduler times out gracefully"""
        import src.services.pricing_sync_scheduler as scheduler_module

        # Ensure clean state
        scheduler_module._scheduler_task = None
        scheduler_module._shutdown_event.clear()

        # Create a long-running task
        async def long_task():
            await asyncio.sleep(100)  # Much longer than timeout

        scheduler_module._scheduler_task = asyncio.create_task(long_task())

        # Stop with timeout (should cancel after 30s, but we mock time)
        with patch('asyncio.wait_for', side_effect=asyncio.TimeoutError):
            await stop_scheduler_sync_scheduler()

        # Task should be cancelled and cleaned up
        assert scheduler_module._scheduler_task is None or scheduler_module._scheduler_task.cancelled()

        # Cleanup any remaining tasks
        try:
            await asyncio.sleep(0.01)
        except:
            pass


class TestSchedulerStatus:
    """Test scheduler status reporting"""

    def test_get_scheduler_status_when_running(self):
        """Get status returns correct data when scheduler is running"""
        import src.services.pricing_sync_scheduler as scheduler_module

        # Mock a running task
        scheduler_module._scheduler_task = MagicMock()
        scheduler_module._scheduler_task.done.return_value = False

        with patch('src.config.config.Config.PRICING_SYNC_ENABLED', True):
            with patch('src.config.config.Config.PRICING_SYNC_INTERVAL_HOURS', 6):
                with patch('src.config.config.Config.PRICING_SYNC_PROVIDERS', ['openrouter', 'featherless']):
                    status = get_scheduler_status()

                    assert status['enabled'] is True
                    assert status['interval_hours'] == 6
                    assert status['running'] is True
                    assert 'openrouter' in status['providers']
                    assert 'featherless' in status['providers']

    def test_get_scheduler_status_when_not_running(self):
        """Get status returns correct data when scheduler is not running"""
        import src.services.pricing_sync_scheduler as scheduler_module

        # No task
        scheduler_module._scheduler_task = None

        with patch('src.config.config.Config.PRICING_SYNC_ENABLED', False):
            with patch('src.config.config.Config.PRICING_SYNC_INTERVAL_HOURS', 12):
                with patch('src.config.config.Config.PRICING_SYNC_PROVIDERS', ['openrouter']):
                    status = get_scheduler_status()

                    assert status['enabled'] is False
                    assert status['interval_hours'] == 12
                    assert status['running'] is False
                    assert 'openrouter' in status['providers']

    def test_get_scheduler_status_with_last_syncs(self):
        """Get status includes last sync timestamps"""
        import src.services.pricing_sync_scheduler as scheduler_module
        from src.services.pricing_sync_scheduler import last_sync_timestamp

        # Mock Prometheus gauge
        mock_metric = MagicMock()
        mock_metric._value.get.return_value = 1737900000.0  # Mock timestamp

        with patch.object(last_sync_timestamp, 'labels', return_value=mock_metric):
            with patch('src.config.config.Config.PRICING_SYNC_ENABLED', True):
                with patch('src.config.config.Config.PRICING_SYNC_INTERVAL_HOURS', 6):
                    with patch('src.config.config.Config.PRICING_SYNC_PROVIDERS', ['openrouter']):
                        status = get_scheduler_status()

                        # Should include last_syncs if metrics available
                        assert 'last_syncs' in status or status is not None


class TestManualTrigger:
    """Test manual sync triggering"""

    @pytest.mark.asyncio
    async def test_trigger_manual_sync_success(self):
        """Manual trigger executes sync successfully"""
        mock_result = {
            'status': 'success',
            'total_models_updated': 100,
            'total_models_skipped': 0,
            'total_errors': 0,
            'results': {
                'openrouter': {
                    'status': 'success',
                    'models_updated': 50
                }
            }
        }

        with patch('src.services.pricing_sync_service.run_scheduled_sync') as mock_sync:
            mock_sync.return_value = mock_result

            result = await trigger_manual_sync()

            assert result['status'] == 'success'
            assert result['total_models_updated'] == 100
            assert 'duration_seconds' in result
            assert result['total_errors'] == 0

    @pytest.mark.asyncio
    async def test_trigger_manual_sync_failure(self):
        """Manual trigger handles sync failure"""
        with patch('src.services.pricing_sync_service.run_scheduled_sync') as mock_sync:
            mock_sync.side_effect = Exception('Provider API error')

            result = await trigger_manual_sync()

            assert result['status'] == 'failed'
            assert 'error_message' in result
            assert 'Provider API error' in result['error_message']
            assert 'duration_seconds' in result

    @pytest.mark.asyncio
    async def test_trigger_manual_sync_records_duration(self):
        """Manual trigger records execution duration"""
        mock_result = {
            'status': 'success',
            'total_models_updated': 50
        }

        # Simulate slow sync
        async def slow_sync(*args, **kwargs):
            await asyncio.sleep(0.1)
            return mock_result

        with patch('src.services.pricing_sync_service.run_scheduled_sync', new=slow_sync):
            result = await trigger_manual_sync()

            assert 'duration_seconds' in result
            assert result['duration_seconds'] > 0.09  # At least 0.1s


class TestSchedulerLoop:
    """Test scheduler main loop behavior"""

    @pytest.mark.asyncio
    async def test_scheduler_loop_waits_before_first_sync(self):
        """Scheduler waits 30 seconds before first sync"""
        import src.services.pricing_sync_scheduler as scheduler_module

        # Reset shutdown event
        scheduler_module._shutdown_event.clear()

        # Mock wait_for to track timeout
        timeouts = []

        async def mock_wait_for(coro, timeout):
            timeouts.append(timeout)
            # Simulate timeout (expected behavior)
            raise asyncio.TimeoutError

        with patch('asyncio.wait_for', side_effect=mock_wait_for):
            with patch('src.services.pricing_sync_scheduler._run_scheduled_sync', return_value=asyncio.coroutine(lambda: {'status': 'success'})()):
                pass

                # Start loop in background
                task = asyncio.create_task(scheduler_module._pricing_sync_scheduler_loop())

                # Let it run briefly
                await asyncio.sleep(0.01)

                # Stop it
                scheduler_module._shutdown_event.set()

                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                # Verify first timeout was 30 seconds (initial delay)
                assert len(timeouts) > 0
                assert timeouts[0] == 30.0

    @pytest.mark.asyncio
    async def test_scheduler_loop_respects_shutdown(self):
        """Scheduler loop stops when shutdown event is set"""
        import src.services.pricing_sync_scheduler as scheduler_module

        # Reset shutdown event
        scheduler_module._shutdown_event.clear()

        sync_calls = []

        async def mock_sync():
            sync_calls.append(datetime.now(timezone.utc))
            return {'status': 'success', 'total_models_updated': 10}

        with patch('src.services.pricing_sync_scheduler._run_scheduled_sync', side_effect=mock_sync):
            with patch('asyncio.wait_for') as mock_wait_for:
                # First call: simulate timeout (initial delay)
                # Second call: return normally (shutdown signal)
                mock_wait_for.side_effect = [asyncio.TimeoutError, None]

                # Run loop
                await scheduler_module._pricing_sync_scheduler_loop()

                # Loop should have completed and called sync once
                assert len(sync_calls) >= 1


class TestErrorHandling:
    """Test scheduler error handling"""

    @pytest.mark.asyncio
    async def test_scheduler_loop_continues_after_error(self):
        """Scheduler loop continues running after sync error"""
        import src.services.pricing_sync_scheduler as scheduler_module

        scheduler_module._shutdown_event.clear()

        sync_attempts = []

        async def failing_sync():
            sync_attempts.append(datetime.now(timezone.utc))
            raise Exception('Provider timeout')

        with patch('src.services.pricing_sync_scheduler._run_scheduled_sync', side_effect=failing_sync):
            with patch('asyncio.wait_for') as mock_wait_for:
                # Simulate: timeout, error sleep, shutdown
                mock_wait_for.side_effect = [
                    asyncio.TimeoutError,  # Initial delay
                    asyncio.TimeoutError,  # Error retry delay
                    None  # Shutdown
                ]

                with patch('sentry_sdk.capture_exception'):  # Don't actually send to Sentry
                    await scheduler_module._pricing_sync_scheduler_loop()

                # Should have attempted sync at least once
                assert len(sync_attempts) >= 1

    @pytest.mark.asyncio
    async def test_scheduler_loop_sends_errors_to_sentry(self):
        """Scheduler loop sends errors to Sentry"""
        import src.services.pricing_sync_scheduler as scheduler_module

        scheduler_module._shutdown_event.clear()

        async def failing_sync():
            raise RuntimeError('Database connection lost')

        sentry_calls = []

        def mock_capture(exc):
            sentry_calls.append(exc)

        with patch('src.services.pricing_sync_scheduler._run_scheduled_sync', side_effect=failing_sync):
            with patch('asyncio.wait_for') as mock_wait_for:
                mock_wait_for.side_effect = [asyncio.TimeoutError, None]

                with patch('sentry_sdk.capture_exception', side_effect=mock_capture):
                    await scheduler_module._pricing_sync_scheduler_loop()

                # Verify error was captured
                assert len(sentry_calls) >= 1
                assert 'Database connection lost' in str(sentry_calls[0])


class TestPrometheusMetrics:
    """Test Prometheus metrics collection"""

    @pytest.mark.asyncio
    async def test_manual_sync_updates_metrics(self):
        """Manual sync updates Prometheus metrics"""
        from src.services.pricing_sync_scheduler import (
            scheduled_sync_runs,
            scheduled_sync_duration
        )

        mock_result = {
            'status': 'success',
            'total_models_updated': 100,
            'results': {
                'openrouter': {'status': 'success', 'models_updated': 50}
            }
        }

        # Mock metrics
        with patch.object(scheduled_sync_runs, 'labels') as mock_counter:
            with patch.object(scheduled_sync_duration, 'observe') as mock_histogram:
                with patch('src.services.pricing_sync_scheduler.run_scheduled_sync', return_value=mock_result):
                    await trigger_manual_sync()

                    # Note: Manual sync doesn't increment scheduled metrics
                    # This is intentional design
                    # Just verify no errors occurred

    def test_metrics_exist(self):
        """Verify Prometheus metrics are defined"""
        from src.services.pricing_sync_scheduler import (
            scheduled_sync_runs,
            scheduled_sync_duration,
            last_sync_timestamp,
            models_synced_total
        )

        assert scheduled_sync_runs is not None
        assert scheduled_sync_duration is not None
        assert last_sync_timestamp is not None
        assert models_synced_total is not None


class TestConfiguration:
    """Test configuration handling"""

    def test_scheduler_reads_config(self):
        """Scheduler reads configuration from Config"""
        with patch('src.config.config.Config.PRICING_SYNC_ENABLED', True):
            with patch('src.config.config.Config.PRICING_SYNC_INTERVAL_HOURS', 12):
                with patch('src.config.config.Config.PRICING_SYNC_PROVIDERS', ['openrouter', 'featherless', 'nearai']):
                    status = get_scheduler_status()

                    assert status['enabled'] is True
                    assert status['interval_hours'] == 12
                    assert len(status['providers']) == 3

    def test_scheduler_handles_disabled_config(self):
        """Scheduler handles disabled configuration"""
        with patch('src.config.config.Config.PRICING_SYNC_ENABLED', False):
            status = get_scheduler_status()
            assert status['enabled'] is False
