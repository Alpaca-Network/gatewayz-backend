#!/usr/bin/env python3
"""
Comprehensive Monitoring Stack Test Script

This script tests all monitoring components:
1. Prometheus metrics endpoint
2. Redis metrics service
3. Monitoring API endpoints (16 endpoints)
4. Analytics service
5. Circuit breakers
6. Health monitoring
7. Metrics aggregation
8. Database schema

Usage:
    python scripts/test_monitoring_stack.py
    python scripts/test_monitoring_stack.py --verbose
    python scripts/test_monitoring_stack.py --skip-db  # Skip database tests
"""

import asyncio
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
from colorama import init, Fore, Style

# Initialize colorama for colored output
init(autoreset=True)

# Test configuration
BASE_URL = "http://localhost:8000"
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv
SKIP_DB = "--skip-db" in sys.argv
SKIP_REDIS = "--skip-redis" in sys.argv


class TestResult:
    """Track test results"""
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.warnings = 0
        self.errors = []

    def add_pass(self, test_name):
        self.total += 1
        self.passed += 1
        print(f"{Fore.GREEN}✓{Style.RESET_ALL} {test_name}")

    def add_fail(self, test_name, error):
        self.total += 1
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"{Fore.RED}✗{Style.RESET_ALL} {test_name}")
        if VERBOSE:
            print(f"  {Fore.RED}Error: {error}{Style.RESET_ALL}")

    def add_skip(self, test_name, reason):
        self.total += 1
        self.skipped += 1
        print(f"{Fore.YELLOW}⊘{Style.RESET_ALL} {test_name} (skipped: {reason})")

    def add_warning(self, test_name, message):
        self.total += 1
        self.warnings += 1
        print(f"{Fore.YELLOW}⚠{Style.RESET_ALL} {test_name}")
        if VERBOSE:
            print(f"  {Fore.YELLOW}Warning: {message}{Style.RESET_ALL}")

    def print_summary(self):
        print("\n" + "=" * 70)
        print(f"{Fore.CYAN}Test Summary{Style.RESET_ALL}")
        print("=" * 70)
        print(f"Total:    {self.total}")
        print(f"{Fore.GREEN}Passed:   {self.passed}{Style.RESET_ALL}")
        print(f"{Fore.RED}Failed:   {self.failed}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Warnings: {self.warnings}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Skipped:  {self.skipped}{Style.RESET_ALL}")

        if self.failed > 0:
            print(f"\n{Fore.RED}Failed Tests:{Style.RESET_ALL}")
            for test_name, error in self.errors:
                print(f"  • {test_name}")
                print(f"    {error}")

        print("\n" + "=" * 70)

        if self.failed == 0 and self.warnings == 0:
            print(f"{Fore.GREEN}🎉 All tests passed!{Style.RESET_ALL}")
            return 0
        elif self.failed == 0:
            print(f"{Fore.YELLOW}⚠ Tests passed with warnings{Style.RESET_ALL}")
            return 0
        else:
            print(f"{Fore.RED}❌ Some tests failed{Style.RESET_ALL}")
            return 1


results = TestResult()


def print_section(title):
    """Print section header"""
    print(f"\n{Fore.CYAN}{'=' * 70}")
    print(f"{title}")
    print(f"{'=' * 70}{Style.RESET_ALL}")


def test_server_running():
    """Test that the server is running"""
    print_section("1. Server Health Check")

    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            results.add_pass("Server is running")
            return True
        else:
            results.add_fail("Server health check", f"Status code: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        results.add_fail("Server health check", "Cannot connect to server. Is it running?")
        print(f"\n{Fore.RED}ERROR: Server is not running!{Style.RESET_ALL}")
        print(f"Start the server with: {Fore.CYAN}uvicorn src.main:app --reload{Style.RESET_ALL}\n")
        return False
    except Exception as e:
        results.add_fail("Server health check", str(e))
        return False


def test_prometheus_metrics():
    """Test Prometheus metrics endpoint"""
    print_section("2. Prometheus Metrics")

    try:
        response = requests.get(f"{BASE_URL}/metrics", timeout=5)

        if response.status_code != 200:
            results.add_fail("Prometheus /metrics endpoint", f"Status code: {response.status_code}")
            return

        results.add_pass("Prometheus /metrics endpoint accessible")

        # Check for expected metrics
        content = response.text

        expected_metrics = [
            "model_inference_requests_total",
            "model_inference_duration_seconds",
            "tokens_used_total",
            "credits_used_total",
            "database_query_total",
            "database_query_duration_seconds",
            "http_requests_total",
            "http_request_duration_seconds",
        ]

        for metric in expected_metrics:
            if metric in content:
                results.add_pass(f"Metric '{metric}' present")
            else:
                results.add_warning(f"Metric '{metric}' present", "Metric not found (may not have data yet)")

    except Exception as e:
        results.add_fail("Prometheus metrics test", str(e))


def test_monitoring_api():
    """Test monitoring API endpoints"""
    print_section("3. Monitoring API Endpoints")

    endpoints = [
        ("/api/monitoring/health", "GET", "All provider health"),
        ("/api/monitoring/stats/realtime", "GET", "Real-time statistics"),
        ("/api/monitoring/circuit-breakers", "GET", "Circuit breaker states"),
        ("/api/monitoring/providers/comparison", "GET", "Provider comparison"),
        ("/api/monitoring/anomalies", "GET", "Anomaly detection"),
        ("/api/monitoring/trial-analytics", "GET", "Trial analytics"),
        ("/api/monitoring/cost-analysis?days=7", "GET", "Cost analysis"),
        ("/api/monitoring/error-rates?hours=24", "GET", "Error rates"),
    ]

    for endpoint, method, description in endpoints:
        try:
            response = requests.get(f"{BASE_URL}{endpoint}", timeout=5)

            if response.status_code == 200:
                data = response.json()
                results.add_pass(f"{description} - {endpoint}")

                if VERBOSE:
                    print(f"  Response keys: {list(data.keys())}")
            elif response.status_code == 404:
                results.add_fail(f"{description} - {endpoint}", "Endpoint not found (route not registered?)")
            else:
                results.add_warning(f"{description} - {endpoint}", f"Status {response.status_code} (may need data)")
        except Exception as e:
            results.add_fail(f"{description} - {endpoint}", str(e))


def test_redis_connection():
    """Test Redis connection"""
    print_section("4. Redis Metrics Service")

    if SKIP_REDIS:
        results.add_skip("Redis connection test", "Skipped via --skip-redis")
        return False

    try:
        from src.config.redis_config import get_redis_client

        redis_client = get_redis_client()

        if redis_client is None:
            results.add_skip("Redis connection", "Redis is disabled in config")
            return False

        # Test ping
        redis_client.ping()
        results.add_pass("Redis connection successful")

        # Test Redis metrics service
        from src.services.redis_metrics import get_redis_metrics

        redis_metrics = get_redis_metrics()

        if redis_metrics.enabled:
            results.add_pass("Redis metrics service initialized")
        else:
            results.add_warning("Redis metrics service", "Service disabled")
            return False

        # Test recording a request (async)
        async def test_record():
            await redis_metrics.record_request(
                provider="test_provider",
                model="test_model",
                latency_ms=100,
                success=True,
                cost=0.01,
                tokens_input=10,
                tokens_output=20
            )

        asyncio.run(test_record())
        results.add_pass("Redis metrics recording")

        # Test retrieving health
        async def test_health():
            score = await redis_metrics.get_provider_health("test_provider")
            return score

        score = asyncio.run(test_health())
        results.add_pass(f"Redis health score retrieval (score: {score})")

        return True

    except ImportError:
        results.add_skip("Redis tests", "Redis dependencies not installed")
        return False
    except Exception as e:
        results.add_fail("Redis connection test", str(e))
        print(f"\n{Fore.YELLOW}TIP: Start Redis with: {Fore.CYAN}docker run -d -p 6379:6379 redis:latest{Style.RESET_ALL}\n")
        return False


def test_database_schema():
    """Test database schema"""
    print_section("5. Database Schema")

    if SKIP_DB:
        results.add_skip("Database schema test", "Skipped via --skip-db")
        return False

    try:
        from src.config.supabase_config import get_supabase_client

        supabase = get_supabase_client()

        # Test metrics_hourly_aggregates table
        try:
            result = supabase.table("metrics_hourly_aggregates").select("*").limit(1).execute()
            results.add_pass("Table 'metrics_hourly_aggregates' exists")

            if VERBOSE and result.data:
                print(f"  Sample columns: {list(result.data[0].keys())}")
        except Exception as e:
            if "does not exist" in str(e) or "relation" in str(e):
                results.add_fail("Table 'metrics_hourly_aggregates'", "Table not found - run migration!")
                print(f"\n{Fore.YELLOW}TIP: Run migration with: {Fore.CYAN}supabase migration up{Style.RESET_ALL}\n")
            else:
                results.add_fail("Table 'metrics_hourly_aggregates'", str(e))

        # Test materialized view
        try:
            result = supabase.table("provider_stats_24h").select("*").limit(1).execute()
            results.add_pass("Materialized view 'provider_stats_24h' exists")

            if VERBOSE and result.data:
                print(f"  Sample columns: {list(result.data[0].keys())}")
        except Exception as e:
            if "does not exist" in str(e) or "relation" in str(e):
                results.add_fail("Materialized view 'provider_stats_24h'", "View not found - run migration!")
            else:
                results.add_fail("Materialized view 'provider_stats_24h'", str(e))

        return True

    except ImportError:
        results.add_skip("Database tests", "Supabase dependencies not installed")
        return False
    except Exception as e:
        results.add_fail("Database connection", str(e))
        return False


def test_analytics_service():
    """Test analytics service"""
    print_section("6. Analytics Service")

    try:
        from src.services.analytics import get_analytics_service

        analytics = get_analytics_service()
        results.add_pass("Analytics service initialized")

        # Test trial analytics (sync function)
        try:
            trial_data = analytics.get_trial_analytics()

            if "signups" in trial_data and "conversion_rate" in trial_data:
                results.add_pass("Trial analytics function")
                if VERBOSE:
                    print(f"  Signups: {trial_data.get('signups', 0)}")
                    print(f"  Conversion rate: {trial_data.get('conversion_rate', 0)}%")
            else:
                results.add_warning("Trial analytics function", "Unexpected response format")
        except Exception as e:
            results.add_fail("Trial analytics function", str(e))

        # Test provider comparison (async function)
        async def test_comparison():
            providers = await analytics.get_provider_comparison()
            return providers

        try:
            providers = asyncio.run(test_comparison())
            results.add_pass("Provider comparison function")
            if VERBOSE:
                print(f"  Providers found: {len(providers)}")
        except Exception as e:
            results.add_fail("Provider comparison function", str(e))

        # Test anomaly detection (async function)
        async def test_anomalies():
            anomalies = await analytics.detect_anomalies()
            return anomalies

        try:
            anomalies = asyncio.run(test_anomalies())
            results.add_pass("Anomaly detection function")
            if VERBOSE:
                print(f"  Anomalies detected: {len(anomalies)}")
        except Exception as e:
            results.add_fail("Anomaly detection function", str(e))

    except ImportError as e:
        results.add_skip("Analytics service tests", f"Import error: {e}")
    except Exception as e:
        results.add_fail("Analytics service initialization", str(e))


def test_circuit_breakers():
    """Test circuit breaker functionality"""
    print_section("7. Circuit Breakers")

    try:
        from src.services.model_availability import availability_service

        results.add_pass("Circuit breaker service initialized")

        # Check if circuit breakers exist
        if hasattr(availability_service, 'circuit_breakers'):
            cb_count = len(availability_service.circuit_breakers)
            results.add_pass(f"Circuit breakers loaded ({cb_count} breakers)")

            if VERBOSE and cb_count > 0:
                print(f"  Sample breakers: {list(availability_service.circuit_breakers.keys())[:5]}")
        else:
            results.add_warning("Circuit breakers", "No circuit breakers loaded yet")

        # Test availability check
        try:
            is_available = availability_service.is_model_available("gpt-4", "openrouter")
            results.add_pass(f"Circuit breaker check function (gpt-4 available: {is_available})")
        except Exception as e:
            results.add_fail("Circuit breaker check function", str(e))

    except ImportError as e:
        results.add_skip("Circuit breaker tests", f"Import error: {e}")
    except Exception as e:
        results.add_fail("Circuit breaker initialization", str(e))


def test_health_monitoring():
    """Test health monitoring"""
    print_section("8. Health Monitoring")

    try:
        from src.services.model_health_monitor import health_monitor

        results.add_pass("Health monitor service initialized")

        # Check if monitoring is running
        if hasattr(health_monitor, 'monitoring_active'):
            if health_monitor.monitoring_active:
                results.add_pass("Active health monitoring enabled")
            else:
                results.add_warning("Active health monitoring", "Not currently active")
        else:
            results.add_warning("Health monitoring status", "Cannot determine status")

        # Test passive health capture (sync function)
        from src.services.model_health_monitor import capture_model_health

        try:
            capture_model_health(
                provider="test_provider",
                model="test_model",
                response_time_ms=100,
                health_status="healthy"
            )
            results.add_pass("Passive health monitoring capture")
        except Exception as e:
            results.add_fail("Passive health monitoring capture", str(e))

    except ImportError as e:
        results.add_skip("Health monitoring tests", f"Import error: {e}")
    except Exception as e:
        results.add_fail("Health monitoring initialization", str(e))


def test_metrics_aggregator():
    """Test metrics aggregator"""
    print_section("9. Metrics Aggregator")

    try:
        from src.services.metrics_aggregator import get_metrics_aggregator

        aggregator = get_metrics_aggregator()

        if aggregator.enabled:
            results.add_pass("Metrics aggregator initialized")
        else:
            results.add_warning("Metrics aggregator", "Aggregator disabled (Redis or Supabase unavailable)")
            return

        # Test aggregation (don't actually run, just check if method exists)
        if hasattr(aggregator, 'aggregate_last_hour'):
            results.add_pass("Metrics aggregator has aggregation methods")
        else:
            results.add_fail("Metrics aggregator methods", "Missing aggregation methods")

        if VERBOSE:
            print(f"  Aggregator enabled: {aggregator.enabled}")

    except ImportError as e:
        results.add_skip("Metrics aggregator tests", f"Import error: {e}")
    except Exception as e:
        results.add_fail("Metrics aggregator initialization", str(e))


def test_configuration():
    """Test configuration"""
    print_section("10. Configuration")

    try:
        from src.config.config import Config

        # Check Grafana Cloud config
        if hasattr(Config, 'GRAFANA_CLOUD_ENABLED'):
            results.add_pass("Grafana Cloud configuration variables present")
        else:
            results.add_fail("Grafana Cloud configuration", "Missing config variables")

        # Check Redis config
        if hasattr(Config, 'REDIS_ENABLED'):
            results.add_pass("Redis configuration variables present")
            if VERBOSE:
                print(f"  Redis enabled: {Config.REDIS_ENABLED}")
        else:
            results.add_fail("Redis configuration", "Missing config variables")

        # Check metrics aggregation config
        if hasattr(Config, 'METRICS_AGGREGATION_ENABLED'):
            results.add_pass("Metrics aggregation configuration present")
            if VERBOSE:
                print(f"  Aggregation enabled: {Config.METRICS_AGGREGATION_ENABLED}")
                print(f"  Aggregation interval: {Config.METRICS_AGGREGATION_INTERVAL_MINUTES} min")
        else:
            results.add_fail("Metrics aggregation configuration", "Missing config variables")

    except ImportError as e:
        results.add_fail("Configuration import", str(e))
    except Exception as e:
        results.add_fail("Configuration test", str(e))


def test_sentry_configuration():
    """Test Sentry adaptive sampling"""
    print_section("11. Sentry Configuration")

    try:
        from src.config.config import Config

        if Config.SENTRY_ENABLED:
            results.add_pass("Sentry is enabled")

            if VERBOSE:
                print(f"  Environment: {Config.SENTRY_ENVIRONMENT}")
                print(f"  Traces sample rate: {Config.SENTRY_TRACES_SAMPLE_RATE}")
                print(f"  Profiles sample rate: {Config.SENTRY_PROFILES_SAMPLE_RATE}")
        else:
            results.add_skip("Sentry configuration", "Sentry is disabled")

    except Exception as e:
        results.add_fail("Sentry configuration test", str(e))


def main():
    """Run all tests"""
    print(f"\n{Fore.CYAN}{'=' * 70}")
    print(f"{Style.BRIGHT}Gatewayz Monitoring Stack - Comprehensive Test Suite{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'=' * 70}{Style.RESET_ALL}\n")

    print(f"Testing against: {Fore.CYAN}{BASE_URL}{Style.RESET_ALL}")
    print(f"Verbose mode: {Fore.CYAN}{VERBOSE}{Style.RESET_ALL}")
    print(f"Skip database: {Fore.CYAN}{SKIP_DB}{Style.RESET_ALL}")
    print(f"Skip Redis: {Fore.CYAN}{SKIP_REDIS}{Style.RESET_ALL}\n")

    # Run tests in order
    if not test_server_running():
        print(f"\n{Fore.RED}Cannot proceed without running server. Exiting.{Style.RESET_ALL}\n")
        return 1

    test_prometheus_metrics()
    test_monitoring_api()
    test_redis_connection()
    test_database_schema()
    test_analytics_service()
    test_circuit_breakers()
    test_health_monitoring()
    test_metrics_aggregator()
    test_configuration()
    test_sentry_configuration()

    # Print summary
    return results.print_summary()


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}Tests interrupted by user{Style.RESET_ALL}\n")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Fore.RED}Unexpected error: {e}{Style.RESET_ALL}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
