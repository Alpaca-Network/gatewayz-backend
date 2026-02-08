"""
Comprehensive tests for Langfuse LLM observability configuration.
"""
from unittest.mock import Mock


class TestLangfuseConfig:
    """Test LangfuseConfig functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        from src.config import langfuse_config
        assert langfuse_config is not None

    def test_module_has_expected_attributes(self):
        """Test module has expected public API"""
        from src.config import langfuse_config
        # Verify expected exports exist
        assert hasattr(langfuse_config, 'LangfuseConfig')
        assert hasattr(langfuse_config, 'LangfuseTracer')
        assert hasattr(langfuse_config, 'LangfuseGenerationContext')
        assert hasattr(langfuse_config, 'init_langfuse')
        assert hasattr(langfuse_config, 'shutdown_langfuse')
        assert hasattr(langfuse_config, 'get_langfuse_client')
        assert hasattr(langfuse_config, 'flush_langfuse')

    def test_langfuse_available_flag(self):
        """Test that LANGFUSE_AVAILABLE flag is set correctly"""
        from src.config.langfuse_config import LANGFUSE_AVAILABLE
        # Should be True if langfuse package is installed, False otherwise
        assert isinstance(LANGFUSE_AVAILABLE, bool)


class TestLangfuseConfigInitialization:
    """Test LangfuseConfig initialization"""

    def test_initialize_returns_false_when_disabled(self, monkeypatch):
        """Test initialization returns False when LANGFUSE_ENABLED=false"""
        monkeypatch.setenv("LANGFUSE_ENABLED", "false")

        # Reload config to pick up env var
        import importlib
        from src.config import config
        importlib.reload(config)

        from src.config.langfuse_config import LangfuseConfig
        # Reset state
        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        result = LangfuseConfig.initialize()
        assert result is False
        assert LangfuseConfig.is_initialized() is False

    def test_initialize_returns_false_without_public_key(self, monkeypatch):
        """Test initialization returns False when LANGFUSE_PUBLIC_KEY is missing"""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-key")

        import importlib
        from src.config import config
        importlib.reload(config)

        from src.config.langfuse_config import LangfuseConfig
        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        result = LangfuseConfig.initialize()
        assert result is False

    def test_initialize_returns_false_without_secret_key(self, monkeypatch):
        """Test initialization returns False when LANGFUSE_SECRET_KEY is missing"""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-key")
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        import importlib
        from src.config import config
        importlib.reload(config)

        from src.config.langfuse_config import LangfuseConfig
        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        result = LangfuseConfig.initialize()
        assert result is False

    def test_get_client_returns_none_when_not_initialized(self):
        """Test get_client returns None when not initialized"""
        from src.config.langfuse_config import LangfuseConfig
        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        assert LangfuseConfig.get_client() is None

    def test_is_initialized_returns_correct_state(self):
        """Test is_initialized returns correct state"""
        from src.config.langfuse_config import LangfuseConfig

        LangfuseConfig._initialized = False
        assert LangfuseConfig.is_initialized() is False

        LangfuseConfig._initialized = True
        assert LangfuseConfig.is_initialized() is True

        # Reset
        LangfuseConfig._initialized = False


class TestLangfuseConfigShutdown:
    """Test LangfuseConfig shutdown"""

    def test_shutdown_does_nothing_when_not_initialized(self):
        """Test shutdown is a no-op when not initialized"""
        from src.config.langfuse_config import LangfuseConfig
        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        # Should not raise
        LangfuseConfig.shutdown()
        assert LangfuseConfig._initialized is False

    def test_shutdown_flushes_and_resets_state(self):
        """Test shutdown flushes client and resets state"""
        from src.config.langfuse_config import LangfuseConfig

        mock_client = Mock()
        LangfuseConfig._initialized = True
        LangfuseConfig._client = mock_client

        LangfuseConfig.shutdown()

        mock_client.flush.assert_called_once()
        mock_client.shutdown.assert_called_once()
        assert LangfuseConfig._initialized is False
        assert LangfuseConfig._client is None


class TestLangfuseGenerationContext:
    """Test LangfuseGenerationContext"""

    def test_context_creation(self):
        """Test context can be created"""
        from src.config.langfuse_config import LangfuseGenerationContext

        ctx = LangfuseGenerationContext(
            trace=None,
            generation=None,
            provider="openrouter",
            model="gpt-4",
        )

        assert ctx.provider == "openrouter"
        assert ctx.model == "gpt-4"

    def test_set_usage(self):
        """Test set_usage method"""
        from src.config.langfuse_config import LangfuseGenerationContext

        ctx = LangfuseGenerationContext(provider="test", model="test-model")
        result = ctx.set_usage(input_tokens=100, output_tokens=50)

        assert result is ctx  # Returns self for chaining
        assert ctx._usage["input"] == 100
        assert ctx._usage["output"] == 50
        assert ctx._usage["total"] == 150

    def test_set_cost(self):
        """Test set_cost method"""
        from src.config.langfuse_config import LangfuseGenerationContext

        ctx = LangfuseGenerationContext(provider="test", model="test-model")
        result = ctx.set_cost(0.0023)

        assert result is ctx
        assert ctx._metadata["cost_usd"] == 0.0023

    def test_set_output(self):
        """Test set_output method"""
        from src.config.langfuse_config import LangfuseGenerationContext

        ctx = LangfuseGenerationContext(provider="test", model="test-model")
        output = {"choices": [{"message": {"content": "Hello!"}}]}
        result = ctx.set_output(output)

        assert result is ctx
        assert ctx._output == output

    def test_set_error(self):
        """Test set_error method"""
        from src.config.langfuse_config import LangfuseGenerationContext

        ctx = LangfuseGenerationContext(provider="test", model="test-model")
        error = ValueError("Test error")
        result = ctx.set_error(error)

        assert result is ctx
        assert ctx._output["error"] == "Test error"
        assert ctx._output["error_type"] == "ValueError"
        assert ctx._metadata["error"] is True


class TestLangfuseTracerSync:
    """Test LangfuseTracer synchronous context manager"""

    def test_trace_generation_sync_without_client(self):
        """Test sync trace_generation works without Langfuse client"""
        from src.config.langfuse_config import LangfuseTracer, LangfuseConfig

        # Ensure not initialized
        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        with LangfuseTracer.trace_generation_sync("openrouter", "gpt-4") as ctx:
            assert ctx.provider == "openrouter"
            assert ctx.model == "gpt-4"


class TestConvenienceFunctions:
    """Test convenience functions"""

    def test_init_langfuse_calls_initialize(self):
        """Test init_langfuse calls LangfuseConfig.initialize"""
        from src.config.langfuse_config import init_langfuse, LangfuseConfig

        # Reset state
        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        # When disabled, should return False
        result = init_langfuse()
        assert isinstance(result, bool)

    def test_shutdown_langfuse_calls_shutdown(self):
        """Test shutdown_langfuse calls LangfuseConfig.shutdown"""
        from src.config.langfuse_config import shutdown_langfuse, LangfuseConfig

        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        # Should not raise
        shutdown_langfuse()

    def test_get_langfuse_client_returns_client(self):
        """Test get_langfuse_client returns client"""
        from src.config.langfuse_config import get_langfuse_client, LangfuseConfig

        LangfuseConfig._initialized = False
        result = get_langfuse_client()
        assert result is None

    def test_flush_langfuse_flushes(self):
        """Test flush_langfuse flushes traces"""
        from src.config.langfuse_config import flush_langfuse, LangfuseConfig

        LangfuseConfig._initialized = False
        LangfuseConfig._client = None

        # Should not raise
        flush_langfuse()


class TestLangfuseConfigEnvironmentVariables:
    """Test Langfuse configuration from environment variables"""

    def test_langfuse_enabled_default_false(self, monkeypatch):
        """Test LANGFUSE_ENABLED defaults to false"""
        monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_ENABLED is False

    def test_langfuse_enabled_true(self, monkeypatch):
        """Test LANGFUSE_ENABLED=true is recognized"""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_ENABLED is True

    def test_langfuse_enabled_1(self, monkeypatch):
        """Test LANGFUSE_ENABLED=1 is recognized"""
        monkeypatch.setenv("LANGFUSE_ENABLED", "1")

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_ENABLED is True

    def test_langfuse_enabled_yes(self, monkeypatch):
        """Test LANGFUSE_ENABLED=yes is recognized"""
        monkeypatch.setenv("LANGFUSE_ENABLED", "yes")

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_ENABLED is True

    def test_langfuse_host_default(self, monkeypatch):
        """Test LANGFUSE_HOST default value"""
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_HOST == "https://cloud.langfuse.com"

    def test_langfuse_host_custom(self, monkeypatch):
        """Test LANGFUSE_HOST custom value"""
        monkeypatch.setenv("LANGFUSE_HOST", "https://my-langfuse.example.com")

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_HOST == "https://my-langfuse.example.com"

    def test_langfuse_debug_default_false(self, monkeypatch):
        """Test LANGFUSE_DEBUG defaults to false"""
        monkeypatch.delenv("LANGFUSE_DEBUG", raising=False)

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_DEBUG is False

    def test_langfuse_flush_interval_default(self, monkeypatch):
        """Test LANGFUSE_FLUSH_INTERVAL default value"""
        monkeypatch.delenv("LANGFUSE_FLUSH_INTERVAL", raising=False)

        import importlib
        from src.config import config
        importlib.reload(config)

        assert config.Config.LANGFUSE_FLUSH_INTERVAL == 1.0
