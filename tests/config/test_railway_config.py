"""
Tests for Railway deployment configuration consistency.

This module validates that railway.toml and railway.json configurations
are properly aligned, particularly for healthcheck settings which are
critical for deployment success.
"""

import json
from pathlib import Path

import pytest
import toml


class TestRailwayConfiguration:
    """Test suite for Railway deployment configuration files."""

    @pytest.fixture
    def project_root(self):
        """Get the project root directory."""
        return Path(__file__).parent.parent.parent

    @pytest.fixture
    def railway_toml(self, project_root):
        """Load railway.toml configuration."""
        railway_toml_path = project_root / "railway.toml"
        if not railway_toml_path.exists():
            pytest.skip("railway.toml not found")
        return toml.load(railway_toml_path)

    @pytest.fixture
    def railway_json(self, project_root):
        """Load railway.json configuration."""
        railway_json_path = project_root / "railway.json"
        if not railway_json_path.exists():
            pytest.skip("railway.json not found")
        with open(railway_json_path) as f:
            return json.load(f)

    def test_railway_toml_exists(self, project_root):
        """Verify railway.toml exists in project root."""
        railway_toml_path = project_root / "railway.toml"
        assert railway_toml_path.exists(), "railway.toml must exist in project root"

    def test_railway_json_exists(self, project_root):
        """Verify railway.json exists in project root."""
        railway_json_path = project_root / "railway.json"
        assert railway_json_path.exists(), "railway.json must exist in project root"

    def test_healthcheck_timeout_sufficient(self, railway_toml):
        """
        Verify healthcheck timeout is sufficient for app startup.

        The application typically takes 10-15 seconds to fully start.
        The healthcheck timeout must be at least 30 seconds to allow
        for initialization, especially in cold starts.
        """
        deploy_config = railway_toml.get("deploy", {})
        timeout = deploy_config.get("healthcheckTimeout")

        assert timeout is not None, "healthcheckTimeout must be configured in railway.toml"
        assert timeout >= 30, (
            f"healthcheckTimeout must be at least 30 seconds for reliable deployments. "
            f"Current value: {timeout}s. App startup typically takes 10-15 seconds."
        )

    def test_healthcheck_initial_delay_sufficient(self, railway_toml):
        """
        Verify initial delay is sufficient for first healthcheck.

        The initial delay should be at least 45-60 seconds to allow
        for container initialization, dependency installation, and
        application startup before the first healthcheck attempt.
        """
        deploy_config = railway_toml.get("deploy", {})
        initial_delay = deploy_config.get("initialDelaySeconds")

        assert initial_delay is not None, "initialDelaySeconds must be configured in railway.toml"
        assert initial_delay >= 45, (
            f"initialDelaySeconds must be at least 45 seconds. "
            f"Current value: {initial_delay}s. Container startup and app initialization "
            f"typically takes 40-50 seconds."
        )

    def test_healthcheck_path_configured(self, railway_toml):
        """Verify healthcheck path is properly configured."""
        deploy_config = railway_toml.get("deploy", {})
        path = deploy_config.get("healthcheckPath")

        assert path is not None, "healthcheckPath must be configured in railway.toml"
        assert path == "/health", f"healthcheckPath should be /health, got: {path}"

    def test_healthcheck_interval_configured(self, railway_toml):
        """Verify healthcheck interval is properly configured."""
        deploy_config = railway_toml.get("deploy", {})
        interval = deploy_config.get("healthcheckInterval")

        assert interval is not None, "healthcheckInterval must be configured in railway.toml"
        assert (
            10 <= interval <= 60
        ), f"healthcheckInterval should be between 10-60 seconds, got: {interval}s"

    def test_railway_configs_consistency(self, railway_toml, railway_json):
        """
        Verify railway.toml and railway.json have consistent healthcheck settings.

        Both files should specify similar healthcheck timeouts and delays to ensure
        consistent behavior across different Railway deployment methods.
        """
        toml_deploy = railway_toml.get("deploy", {})
        toml_timeout = toml_deploy.get("healthcheckTimeout")
        toml_initial_delay = toml_deploy.get("initialDelaySeconds")

        # Get gateway-api service config from railway.json
        services = railway_json.get("services", [])
        gateway_service = next((s for s in services if s.get("name") == "gateway-api"), None)

        if gateway_service:
            json_healthchecks = gateway_service.get("deploy", {}).get("healthchecks", {})
            json_liveness = json_healthchecks.get("liveness", {})

            json_timeout = json_liveness.get("timeout")
            json_initial_delay = json_liveness.get("initialDelay")

            # Verify timeouts are consistent (allow 10s tolerance)
            if json_timeout and toml_timeout:
                assert abs(toml_timeout - json_timeout) <= 10, (
                    f"Healthcheck timeouts should be consistent. "
                    f"railway.toml: {toml_timeout}s, railway.json: {json_timeout}s"
                )

            # Verify initial delays are consistent (allow 15s tolerance)
            if json_initial_delay and toml_initial_delay:
                assert abs(toml_initial_delay - json_initial_delay) <= 15, (
                    f"Initial delays should be consistent. "
                    f"railway.toml: {toml_initial_delay}s, railway.json: {json_initial_delay}s"
                )

    def test_start_command_configured(self, railway_toml):
        """Verify start command is properly configured."""
        deploy_config = railway_toml.get("deploy", {})
        start_command = deploy_config.get("startCommand")

        assert start_command is not None, "startCommand must be configured in railway.toml"
        assert len(start_command) > 0, "startCommand must not be empty"

    def test_restart_policy_configured(self, railway_toml):
        """Verify restart policy is properly configured."""
        deploy_config = railway_toml.get("deploy", {})

        restart_type = deploy_config.get("restartPolicyType")
        assert restart_type is not None, "restartPolicyType must be configured"
        assert restart_type in [
            "ON_FAILURE",
            "ALWAYS",
            "NEVER",
        ], f"restartPolicyType must be one of: ON_FAILURE, ALWAYS, NEVER. Got: {restart_type}"

        max_retries = deploy_config.get("restartPolicyMaxRetries")
        if restart_type == "ON_FAILURE":
            assert (
                max_retries is not None
            ), "restartPolicyMaxRetries must be set when using ON_FAILURE"
            assert max_retries > 0, "restartPolicyMaxRetries must be greater than 0"


class TestRailwayHealthcheckRegression:
    """Regression tests for known healthcheck issues."""

    @pytest.fixture
    def project_root(self):
        """Get the project root directory."""
        return Path(__file__).parent.parent.parent

    @pytest.fixture
    def railway_toml(self, project_root):
        """Load railway.toml configuration."""
        railway_toml_path = project_root / "railway.toml"
        if not railway_toml_path.exists():
            pytest.skip("railway.toml not found")
        return toml.load(railway_toml_path)

    def test_healthcheck_timeout_not_10_seconds(self, railway_toml):
        """
        Regression test: Ensure healthcheck timeout is not 10 seconds.

        Issue: PR #575 updated railway.json but missed railway.toml, causing
        deployments to fail with 10-second timeouts while app takes 12+ seconds
        to start.

        Related commits: 2918b7d9, 27f161e6
        """
        deploy_config = railway_toml.get("deploy", {})
        timeout = deploy_config.get("healthcheckTimeout")

        assert timeout != 10, (
            "REGRESSION: healthcheckTimeout is set to 10 seconds, which is too short. "
            "This was the root cause of deployment failures in PR #575. "
            "The application takes 12+ seconds to start. Timeout must be at least 30 seconds."
        )

    def test_healthcheck_initial_delay_not_30_seconds(self, railway_toml):
        """
        Regression test: Ensure initial delay is not 30 seconds.

        Issue: Initial delay of 30 seconds was insufficient for cold starts,
        causing premature healthcheck failures.

        Related commits: 27f161e6
        """
        deploy_config = railway_toml.get("deploy", {})
        initial_delay = deploy_config.get("initialDelaySeconds")

        assert initial_delay != 30, (
            "REGRESSION: initialDelaySeconds is set to 30 seconds, which may be too short "
            "for cold starts. This was identified in PR #575 analysis. "
            "Initial delay should be at least 45-60 seconds to account for container "
            "initialization and app startup."
        )
