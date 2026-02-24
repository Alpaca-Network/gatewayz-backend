"""
Comprehensive tests for Xai Client service
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestXaiClient:
    """Test Xai Client service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.xai_client

        assert src.services.xai_client is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import xai_client

        assert hasattr(xai_client, "__name__")


class TestXaiReasoningDetection:
    """Test xAI reasoning model detection"""

    def test_is_reasoning_model_grok_4(self):
        """Test that Grok 4 models are detected as reasoning models"""
        from src.services.xai_client import is_xai_reasoning_model

        assert is_xai_reasoning_model("grok-4") is True
        assert is_xai_reasoning_model("grok-4-fast") is True
        assert is_xai_reasoning_model("grok-4.1-fast") is True
        assert is_xai_reasoning_model("grok-4-1-fast-reasoning") is True

    def test_is_reasoning_model_grok_3_mini(self):
        """Test that Grok 3 mini models are detected as reasoning models"""
        from src.services.xai_client import is_xai_reasoning_model

        assert is_xai_reasoning_model("grok-3-mini") is True
        assert is_xai_reasoning_model("grok-3-mini-beta") is True

    def test_non_reasoning_models(self):
        """Test that non-reasoning models are correctly identified"""
        from src.services.xai_client import is_xai_reasoning_model

        assert is_xai_reasoning_model("grok-4-1-fast-non-reasoning") is False
        assert is_xai_reasoning_model("grok-4.1-fast-non-reasoning") is False
        assert is_xai_reasoning_model("grok-2") is False
        assert is_xai_reasoning_model("grok-2-1212") is False
        assert is_xai_reasoning_model("grok-beta") is False

    def test_case_insensitivity(self):
        """Test that model detection is case-insensitive"""
        from src.services.xai_client import is_xai_reasoning_model

        assert is_xai_reasoning_model("GROK-4") is True
        assert is_xai_reasoning_model("Grok-4-Fast") is True
        assert is_xai_reasoning_model("GROK-2") is False


class TestXaiReasoningParams:
    """Test xAI reasoning parameter generation"""

    def test_reasoning_params_for_reasoning_model(self):
        """Test reasoning params are generated for reasoning models"""
        from src.services.xai_client import get_xai_reasoning_params

        params = get_xai_reasoning_params("grok-4.1-fast")
        assert params == {"reasoning": {"enabled": True}}

    def test_reasoning_params_for_non_reasoning_model(self):
        """Test no reasoning params for non-reasoning models"""
        from src.services.xai_client import get_xai_reasoning_params

        params = get_xai_reasoning_params("grok-2")
        assert params == {}

    def test_explicit_enable_reasoning(self):
        """Test explicitly enabling reasoning"""
        from src.services.xai_client import get_xai_reasoning_params

        params = get_xai_reasoning_params("grok-4", enable_reasoning=True)
        assert params == {"reasoning": {"enabled": True}}

    def test_explicit_disable_reasoning(self):
        """Test explicitly disabling reasoning"""
        from src.services.xai_client import get_xai_reasoning_params

        params = get_xai_reasoning_params("grok-4", enable_reasoning=False)
        assert params == {"reasoning": {"enabled": False}}

    def test_explicit_enable_on_non_reasoning_model(self):
        """Test that explicit enable on non-reasoning model returns empty"""
        from src.services.xai_client import get_xai_reasoning_params

        # Non-reasoning models don't support the reasoning parameter
        params = get_xai_reasoning_params("grok-2", enable_reasoning=True)
        assert params == {}


class TestXaiRequestWithReasoning:
    """Test xAI request functions with reasoning parameters"""

    @patch("src.services.xai_client.get_xai_client")
    def test_make_request_adds_reasoning_params(self, mock_get_client):
        """Test that reasoning params are added to requests for reasoning models"""
        from src.services.xai_client import make_xai_request_openai

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        make_xai_request_openai(
            messages=[{"role": "user", "content": "Hello"}], model="grok-4.1-fast"
        )

        # Verify reasoning param was passed
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "reasoning" in call_kwargs
        assert call_kwargs["reasoning"] == {"enabled": True}

    @patch("src.services.xai_client.get_xai_client")
    def test_make_request_no_reasoning_for_old_models(self, mock_get_client):
        """Test that reasoning params are NOT added for non-reasoning models"""
        from src.services.xai_client import make_xai_request_openai

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        make_xai_request_openai(messages=[{"role": "user", "content": "Hello"}], model="grok-2")

        # Verify reasoning param was NOT passed
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "reasoning" not in call_kwargs

    @patch("src.services.xai_client.get_xai_client")
    def test_make_stream_request_adds_reasoning_params(self, mock_get_client):
        """Test that streaming requests also get reasoning params"""
        from src.services.xai_client import make_xai_request_openai_stream

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        make_xai_request_openai_stream(
            messages=[{"role": "user", "content": "Hello"}], model="grok-4.1-fast"
        )

        # Verify reasoning param was passed
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "reasoning" in call_kwargs
        assert call_kwargs["reasoning"] == {"enabled": True}
        assert call_kwargs["stream"] is True

    @patch("src.services.xai_client.get_xai_client")
    def test_explicit_reasoning_override(self, mock_get_client):
        """Test that explicit enable_reasoning parameter works"""
        from src.services.xai_client import make_xai_request_openai

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        make_xai_request_openai(
            messages=[{"role": "user", "content": "Hello"}],
            model="grok-4.1-fast",
            enable_reasoning=False,
        )

        # Verify reasoning was disabled
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["reasoning"] == {"enabled": False}
        # enable_reasoning should be popped from kwargs
        assert "enable_reasoning" not in call_kwargs
