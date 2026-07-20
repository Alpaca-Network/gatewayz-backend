"""
Comprehensive tests for src/config/config.py
"""

import os

import pytest


class TestConfigEnvironmentDetection:
    """Test environment detection logic"""

    def test_is_production_detection(self, monkeypatch):
        """Test production environment detection"""
        from src.config import config

        monkeypatch.setenv("APP_ENV", "production")
        # Reload module to pick up new env var
        import importlib

        importlib.reload(config)

        assert config.Config.APP_ENV == "production"
        assert config.Config.IS_PRODUCTION is True
        assert config.Config.IS_STAGING is False
        assert config.Config.IS_DEVELOPMENT is False

    def test_is_staging_detection(self, monkeypatch):
        """Test staging environment detection"""
        from src.config import config

        monkeypatch.setenv("APP_ENV", "staging")
        import importlib

        importlib.reload(config)

        assert config.Config.APP_ENV == "staging"
        assert config.Config.IS_PRODUCTION is False
        assert config.Config.IS_STAGING is True
        assert config.Config.IS_DEVELOPMENT is False

    def test_is_development_detection(self, monkeypatch):
        """Test development environment detection (default)"""
        from src.config import config

        monkeypatch.setenv("APP_ENV", "development")
        import importlib

        importlib.reload(config)

        assert config.Config.APP_ENV == "development"
        assert config.Config.IS_PRODUCTION is False
        assert config.Config.IS_STAGING is False
        assert config.Config.IS_DEVELOPMENT is True

    def test_is_testing_detection_with_testing_env(self, monkeypatch):
        """Test testing environment detection with APP_ENV=testing"""
        from src.config import config

        monkeypatch.setenv("APP_ENV", "testing")
        import importlib

        importlib.reload(config)

        assert config.Config.IS_TESTING is True

    def test_is_testing_detection_with_test_env(self, monkeypatch):
        """Test testing environment detection with APP_ENV=test"""
        from src.config import config

        monkeypatch.setenv("APP_ENV", "test")
        import importlib

        importlib.reload(config)

        assert config.Config.IS_TESTING is True

    def test_is_testing_detection_with_testing_flag_true(self, monkeypatch):
        """Test testing environment detection with TESTING=true"""
        from src.config import config

        monkeypatch.setenv("TESTING", "true")
        import importlib

        importlib.reload(config)

        assert config.Config.IS_TESTING is True

    def test_is_testing_detection_with_testing_flag_1(self, monkeypatch):
        """Test testing environment detection with TESTING=1"""
        from src.config import config

        monkeypatch.setenv("TESTING", "1")
        import importlib

        importlib.reload(config)

        assert config.Config.IS_TESTING is True

    def test_is_testing_detection_with_testing_flag_yes(self, monkeypatch):
        """Test testing environment detection with TESTING=yes"""
        from src.config import config

        monkeypatch.setenv("TESTING", "yes")
        import importlib

        importlib.reload(config)

        assert config.Config.IS_TESTING is True


class TestConfigProviderKeys:
    """Test provider API key configuration"""

    def test_openrouter_keys(self, monkeypatch):
        """Test OpenRouter configuration"""
        from src.config import config

        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter_key")
        monkeypatch.setenv("OPENROUTER_SITE_URL", "https://test-site.com")
        monkeypatch.setenv("OPENROUTER_SITE_NAME", "Test Site")
        import importlib

        importlib.reload(config)

        assert config.Config.OPENROUTER_API_KEY == "test_openrouter_key"
        assert config.Config.OPENROUTER_SITE_URL == "https://test-site.com"
        assert config.Config.OPENROUTER_SITE_NAME == "Test Site"

    def test_openrouter_defaults(self, monkeypatch):
        """Test OpenRouter default values"""
        from src.config import config

        monkeypatch.delenv("OPENROUTER_SITE_URL", raising=False)
        monkeypatch.delenv("OPENROUTER_SITE_NAME", raising=False)
        import importlib

        importlib.reload(config)

        assert config.Config.OPENROUTER_SITE_URL == "https://your-site.com"
        assert config.Config.OPENROUTER_SITE_NAME == "Openrouter AI Gateway"

    def test_openrouter_key_strips_whitespace(self, monkeypatch):
        """Ensure OpenRouter API key trimming removes accidental whitespace"""
        from src.config import config

        monkeypatch.setenv("OPENROUTER_API_KEY", "  sk-or-abc123  \n")
        import importlib

        importlib.reload(config)

        assert config.Config.OPENROUTER_API_KEY == "sk-or-abc123"

    def test_all_provider_keys(self, monkeypatch):
        """Test all provider API keys are loaded"""
        from src.config import config

        providers = {
            "DEEPINFRA_API_KEY": "deepinfra_key",
            "XAI_API_KEY": "xai_key",
            "NOVITA_API_KEY": "novita_key",
            "CEREBRAS_API_KEY": "cerebras_key",
            "FEATHERLESS_API_KEY": "featherless_key",
            "FIREWORKS_API_KEY": "fireworks_key",
            "TOGETHER_API_KEY": "together_key",
            "GROQ_API_KEY": "groq_key",
            "VERCEL_AI_GATEWAY_API_KEY": "vercel_key",
            "HELICONE_API_KEY": "helicone_key",
            "AIHUBMIX_API_KEY": "aihubmix_key",
            "ANANNAS_API_KEY": "anannas_key",
            "ALIBABA_CLOUD_API_KEY": "alibaba_key",
            "ALIBABA_CLOUD_API_KEY_INTERNATIONAL": "intl_key",
            "ALIBABA_CLOUD_API_KEY_CHINA": "china_key",
        }

        for key, value in providers.items():
            monkeypatch.setenv(key, value)

        import importlib

        importlib.reload(config)

        assert config.Config.DEEPINFRA_API_KEY == "deepinfra_key"
        assert config.Config.XAI_API_KEY == "xai_key"
        assert config.Config.CEREBRAS_API_KEY == "cerebras_key"
        assert config.Config.FEATHERLESS_API_KEY == "featherless_key"
        assert config.Config.ALIBABA_CLOUD_API_KEY_INTERNATIONAL == "intl_key"
        assert config.Config.ALIBABA_CLOUD_API_KEY_CHINA == "china_key"


class TestConfigGoogleVertex:
    """Test Google Vertex AI configuration"""

    def test_google_vertex_defaults(self, monkeypatch):
        """Test Google Vertex AI default configuration"""
        from src.config import config

        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_VERTEX_LOCATION", raising=False)
        monkeypatch.delenv("GOOGLE_VERTEX_ENDPOINT_ID", raising=False)
        import importlib

        importlib.reload(config)

        assert config.Config.GOOGLE_PROJECT_ID == "gatewayz-468519"
        assert config.Config.GOOGLE_VERTEX_LOCATION == "us-central1"
        assert config.Config.GOOGLE_VERTEX_ENDPOINT_ID == "6072619212881264640"

    def test_google_vertex_custom_values(self, monkeypatch):
        """Test Google Vertex AI custom configuration"""
        from src.config import config

        monkeypatch.setenv("GOOGLE_PROJECT_ID", "my-project")
        monkeypatch.setenv("GOOGLE_VERTEX_LOCATION", "us-west1")
        monkeypatch.setenv("GOOGLE_VERTEX_ENDPOINT_ID", "123456")
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/creds.json")
        import importlib

        importlib.reload(config)

        assert config.Config.GOOGLE_PROJECT_ID == "my-project"
        assert config.Config.GOOGLE_VERTEX_LOCATION == "us-west1"
        assert config.Config.GOOGLE_VERTEX_ENDPOINT_ID == "123456"
        assert config.Config.GOOGLE_APPLICATION_CREDENTIALS == "/path/to/creds.json"


class TestConfigMonitoring:
    """Test monitoring and observability configuration"""

    def test_prometheus_enabled_by_default(self, monkeypatch):
        """Test Prometheus is enabled by default"""
        from src.config import config

        monkeypatch.delenv("PROMETHEUS_ENABLED", raising=False)
        import importlib

        importlib.reload(config)

        assert config.Config.PROMETHEUS_ENABLED is True

    def test_prometheus_enabled_explicit_true(self, monkeypatch):
        """Test Prometheus enabled with explicit true values"""
        from src.config import config

        for value in ["true", "1", "yes", "True", "YES"]:
            monkeypatch.setenv("PROMETHEUS_ENABLED", value)
            import importlib

            importlib.reload(config)
            assert config.Config.PROMETHEUS_ENABLED is True

    def test_prometheus_disabled(self, monkeypatch):
        """Test Prometheus can be disabled"""
        from src.config import config

        monkeypatch.setenv("PROMETHEUS_ENABLED", "false")
        import importlib

        importlib.reload(config)

        assert config.Config.PROMETHEUS_ENABLED is False

    def test_prometheus_scrape_enabled_by_default(self, monkeypatch):
        """Test Prometheus scrape is enabled by default"""
        from src.config import config

        monkeypatch.delenv("PROMETHEUS_SCRAPE_ENABLED", raising=False)
        import importlib

        importlib.reload(config)

        assert config.Config.PROMETHEUS_SCRAPE_ENABLED is True

    def test_tempo_disabled_by_default(self, monkeypatch):
        """Test Tempo is disabled by default (cost reduction)."""
        from src.config import config

        monkeypatch.delenv("TEMPO_ENABLED", raising=False)
        import importlib

        importlib.reload(config)

        assert config.Config.TEMPO_ENABLED is False

    def test_tempo_enabled(self, monkeypatch):
        """Test Tempo can be enabled"""
        from src.config import config

        for value in ["true", "1", "yes"]:
            monkeypatch.setenv("TEMPO_ENABLED", value)
            import importlib

            importlib.reload(config)
            assert config.Config.TEMPO_ENABLED is True

    def test_loki_disabled_by_default(self, monkeypatch):
        """Test Loki is disabled by default"""
        from src.config import config

        monkeypatch.delenv("LOKI_ENABLED", raising=False)
        import importlib

        importlib.reload(config)

        assert config.Config.LOKI_ENABLED is False

    def test_loki_enabled(self, monkeypatch):
        """Test Loki can be enabled"""
        from src.config import config

        monkeypatch.setenv("LOKI_ENABLED", "true")
        import importlib

        importlib.reload(config)

        assert config.Config.LOKI_ENABLED is True

    def test_otel_service_name_default(self, monkeypatch):
        """Test OTEL service name default"""
        from src.config import config

        monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
        import importlib

        importlib.reload(config)

        assert config.Config.OTEL_SERVICE_NAME == "gatewayz-api"


class TestConfigValidation:
    """Test validate and validate_critical_env_vars methods"""

    def test_validate_success_with_all_vars(self, monkeypatch):
        """Test validate succeeds with all required variables"""
        from src.config.config import Config

        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")
        monkeypatch.delenv("VERCEL", raising=False)

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        result = config_mod.Config.validate()
        assert result is True

    def test_validate_skips_in_vercel_environment(self, monkeypatch):
        """Test validate skips validation in Vercel environment"""
        from src.config.config import Config

        monkeypatch.setenv("VERCEL", "1")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        result = config_mod.Config.validate()
        assert result is True

    def test_validate_raises_on_missing_supabase_url(self, monkeypatch):
        """Test validate raises error on missing SUPABASE_URL"""
        from src.config.config import Config

        monkeypatch.delenv("VERCEL", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        with pytest.raises(RuntimeError, match="Missing required environment variables"):
            config_mod.Config.validate()

    def test_validate_raises_on_missing_multiple_vars(self, monkeypatch):
        """Test validate raises error listing all missing variables"""
        from src.config.config import Config

        monkeypatch.delenv("VERCEL", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        with pytest.raises(RuntimeError) as exc_info:
            config_mod.Config.validate()

        error_message = str(exc_info.value)
        assert "SUPABASE_URL" in error_message
        assert "SUPABASE_KEY" in error_message
        assert "OPENROUTER_API_KEY" in error_message

    def test_validate_critical_env_vars_success(self, monkeypatch):
        """Test validate_critical_env_vars with all variables present"""
        from src.config.config import Config

        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")
        monkeypatch.delenv("VERCEL", raising=False)

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        is_valid, missing = config_mod.Config.validate_critical_env_vars()
        assert is_valid is True
        assert missing == []

    def test_validate_critical_env_vars_missing_vars(self, monkeypatch):
        """Test validate_critical_env_vars with missing variables"""
        from src.config.config import Config

        monkeypatch.delenv("VERCEL", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        is_valid, missing = config_mod.Config.validate_critical_env_vars()
        assert is_valid is False
        assert "SUPABASE_URL" in missing
        assert len(missing) == 1

    def test_validate_critical_env_vars_skips_in_vercel(self, monkeypatch):
        """Test validate_critical_env_vars skips in Vercel environment"""
        from src.config.config import Config

        monkeypatch.setenv("VERCEL", "1")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        is_valid, missing = config_mod.Config.validate_critical_env_vars()
        assert is_valid is True
        assert missing == []

    def test_validate_raises_on_supabase_url_missing_protocol(self, monkeypatch):
        """Test validate raises error when SUPABASE_URL lacks http:// or https:// protocol"""
        monkeypatch.delenv("VERCEL", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "test.supabase.co")  # Missing protocol
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        with pytest.raises(RuntimeError) as exc_info:
            config_mod.Config.validate()

        error_message = str(exc_info.value)
        assert "SUPABASE_URL must start with 'http://' or 'https://'" in error_message

    def test_validate_accepts_http_protocol(self, monkeypatch):
        """Test validate accepts SUPABASE_URL with http:// protocol"""
        monkeypatch.delenv("VERCEL", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        result = config_mod.Config.validate()
        assert result is True

    def test_validate_accepts_https_protocol(self, monkeypatch):
        """Test validate accepts SUPABASE_URL with https:// protocol"""
        monkeypatch.delenv("VERCEL", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        result = config_mod.Config.validate()
        assert result is True

    def test_validate_critical_env_vars_detects_missing_protocol(self, monkeypatch):
        """Test validate_critical_env_vars detects SUPABASE_URL without protocol"""
        monkeypatch.delenv("VERCEL", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "test.supabase.co")  # Missing protocol
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        is_valid, issues = config_mod.Config.validate_critical_env_vars()
        assert is_valid is False
        assert any("SUPABASE_URL" in issue and "protocol" in issue for issue in issues)

    def test_validate_raises_in_vercel_when_url_missing_protocol(self, monkeypatch):
        """Test validate raises error in Vercel env when SUPABASE_URL lacks protocol"""
        monkeypatch.setenv("VERCEL", "1")
        monkeypatch.setenv("SUPABASE_URL", "test.supabase.co")  # Missing protocol
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        with pytest.raises(RuntimeError) as exc_info:
            config_mod.Config.validate()

        error_message = str(exc_info.value)
        assert "SUPABASE_URL must start with 'http://' or 'https://'" in error_message

    def test_validate_critical_env_vars_detects_missing_protocol_in_vercel(self, monkeypatch):
        """Test validate_critical_env_vars detects SUPABASE_URL without protocol in Vercel"""
        monkeypatch.setenv("VERCEL", "1")
        monkeypatch.setenv("SUPABASE_URL", "test.supabase.co")  # Missing protocol
        monkeypatch.setenv("SUPABASE_KEY", "test_key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        is_valid, issues = config_mod.Config.validate_critical_env_vars()
        assert is_valid is False
        assert any("SUPABASE_URL" in issue and "protocol" in issue for issue in issues)

    def test_validate_skips_presence_check_in_vercel(self, monkeypatch):
        """Test validate skips checking presence of keys in Vercel env (but validates URL format)"""
        monkeypatch.setenv("VERCEL", "1")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")  # Valid protocol
        monkeypatch.delenv(
            "SUPABASE_KEY", raising=False
        )  # Missing key - should be skipped in Vercel
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)  # Missing key

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        result = config_mod.Config.validate()
        assert result is True  # Should pass in Vercel despite missing keys


class TestConfigGetSupabaseConfig:
    """Test get_supabase_config method"""

    def test_get_supabase_config_returns_tuple(self, monkeypatch):
        """Test get_supabase_config returns URL and key as tuple"""
        from src.config.config import Config

        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test_key_123")

        import importlib

        import src.config.config as config_mod

        importlib.reload(config_mod)

        url, key = config_mod.Config.get_supabase_config()
        assert url == "https://test.supabase.co"
        assert key == "test_key_123"


class TestConfigAdminAndAnalytics:
    """Test admin and analytics configuration"""

    def test_admin_email_configuration(self, monkeypatch):
        """Test admin email configuration"""
        from src.config import config

        monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")

        import importlib

        importlib.reload(config)

        assert config.Config.ADMIN_EMAIL == "admin@example.com"

    def test_openrouter_cookie_configuration(self, monkeypatch):
        """Test OpenRouter cookie configuration"""
        from src.config import config

        monkeypatch.setenv("OPENROUTER_COOKIE", "test_cookie_value")

        import importlib

        importlib.reload(config)

        assert config.Config.OPENROUTER_COOKIE == "test_cookie_value"


class TestCostRoutingDefaults:
    """Cost-first routing is on by default so the markup+provider spread is captured."""

    def test_smart_router_enabled_by_default(self, monkeypatch):
        import importlib

        from src.config import config

        monkeypatch.delenv("SMART_ROUTER_ENABLED", raising=False)
        importlib.reload(config)
        assert config.Config.SMART_ROUTER_ENABLED is True

    def test_default_policy_is_cost(self, monkeypatch):
        import importlib

        from src.config import config

        monkeypatch.delenv("SMART_ROUTER_POLICY", raising=False)
        importlib.reload(config)
        assert config.Config.SMART_ROUTER_POLICY == "cost"

    def test_kill_switch_restores_legacy(self, monkeypatch):
        import importlib

        from src.config import config

        monkeypatch.setenv("SMART_ROUTER_ENABLED", "false")
        importlib.reload(config)
        assert config.Config.SMART_ROUTER_ENABLED is False
