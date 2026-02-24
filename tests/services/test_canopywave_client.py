"""
Comprehensive tests for Canopy Wave Client service
"""

from unittest.mock import MagicMock

import httpx
import pytest


@pytest.mark.unit
class TestCanopywaveClient:
    """Test Canopy Wave Client service functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.services.canopywave_client

        assert src.services.canopywave_client is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.services import canopywave_client

        assert hasattr(canopywave_client, "__name__")
        assert hasattr(canopywave_client, "fetch_models_from_canopywave")
        assert hasattr(canopywave_client, "get_canopywave_client")
        assert hasattr(canopywave_client, "make_canopywave_request_openai")
        assert hasattr(canopywave_client, "make_canopywave_request_openai_stream")

    def test_get_canopywave_client_raises_without_api_key(self, monkeypatch):
        """Test that get_canopywave_client raises ValueError when API key is not configured"""
        from src.services import canopywave_client

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", None)

        with pytest.raises(ValueError, match="Canopy Wave API key not configured"):
            canopywave_client.get_canopywave_client()

    def test_get_canopywave_client_returns_pooled_client(self, monkeypatch):
        """Test that get_canopywave_client returns a pooled client when API key is configured"""
        from src.services import canopywave_client

        fake_client = MagicMock()
        fake_pooled_client_fn = MagicMock(return_value=fake_client)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr(
            "src.services.canopywave_client.get_canopywave_pooled_client", fake_pooled_client_fn
        )

        client = canopywave_client.get_canopywave_client()

        assert client == fake_client
        fake_pooled_client_fn.assert_called_once()

    def test_fetch_models_from_canopywave_success(self, monkeypatch):
        """Test successful model fetching from Canopy Wave API"""
        from src.services import canopywave_client

        mock_response = {
            "data": [
                {
                    "id": "zai/glm-4.6",
                    "name": "GLM-4.6",
                    "description": "Zhipu AI's flagship open-source reasoning model",
                    "type": "chat",
                    "pricing": {"prompt": 0.00000045, "completion": 0.0000015},
                    "context_length": 200000,
                    "capabilities": {
                        "tools": True,
                        "json_mode": True,
                        "structured_outputs": True,
                        "reasoning": True,
                    },
                },
                {
                    "id": "deepseek-ai/DeepSeek-V3.2",
                    "name": "DeepSeek V3.2",
                    "description": "DeepSeek V3.2 model",
                    "type": "chat",
                    "pricing": {"prompt": 0.0000009, "completion": 0.000002},
                    "context_length": 131072,
                    "capabilities": {"tools": True},
                },
            ]
        }

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.json.return_value = mock_response
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert len(models) == 2
        assert models[0]["id"] == "zai/glm-4.6"
        assert models[0]["name"] == "GLM-4.6"
        assert models[0]["provider"] == "canopywave"
        assert models[0]["provider_slug"] == "canopywave"
        assert models[0]["source_gateway"] == "canopywave"
        assert models[0]["type"] == "chat"
        assert models[0]["context_length"] == 200000
        assert models[0]["pricing"]["prompt"] == "4.5e-07"
        assert models[0]["pricing"]["completion"] == "1.5e-06"
        assert "tools" in models[0]["features"]
        assert "json" in models[0]["features"]
        assert "structured_outputs" in models[0]["features"]
        assert "reasoning" in models[0]["features"]

    def test_fetch_models_from_canopywave_handles_embedding_models(self, monkeypatch):
        """Test that embedding models are properly handled"""
        from src.services import canopywave_client

        mock_response = {
            "data": [
                {
                    "id": "bge/bge-large-en-v1.5",
                    "name": "BGE Large English",
                    "description": "BGE embedding model",
                    "type": "embedding",
                    "pricing": {"prompt": 0.0000001, "completion": 0},
                    "context_length": 512,
                    "capabilities": {
                        "embedding": {"dimensions": 1024},
                    },
                }
            ]
        }

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.json.return_value = mock_response
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert len(models) == 1
        assert models[0]["type"] == "embedding"
        assert models[0]["embedding_dimensions"] == 1024

    def test_fetch_models_from_canopywave_http_error(self, monkeypatch):
        """Test that HTTP errors are handled gracefully"""
        from src.services import canopywave_client

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.status_code = 500
        fake_response.text = "Internal Server Error"
        fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 error", request=MagicMock(), response=fake_response
        )
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert models == []

    def test_fetch_models_from_canopywave_network_error(self, monkeypatch):
        """Test that network errors are handled gracefully"""
        from src.services import canopywave_client

        fake_http_client = MagicMock()
        fake_http_client.get.side_effect = Exception("Network error")
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert models == []

    def test_fetch_models_from_canopywave_skips_models_without_id(self, monkeypatch):
        """Test that models without an ID are skipped"""
        from src.services import canopywave_client

        mock_response = {
            "data": [
                {
                    "name": "Invalid Model",
                    "description": "Model without ID",
                    "type": "chat",
                },
                {
                    "id": "valid-model",
                    "name": "Valid Model",
                    "type": "chat",
                    "pricing": {"prompt": 0.000001, "completion": 0.000002},
                    "context_length": 8192,
                },
            ]
        }

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.json.return_value = mock_response
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert len(models) == 1
        assert models[0]["id"] == "valid-model"

    def test_fetch_models_from_canopywave_handles_missing_pricing(self, monkeypatch):
        """Test that missing pricing information is handled with defaults"""
        from src.services import canopywave_client

        mock_response = {
            "data": [
                {
                    "id": "test-model",
                    "name": "Test Model",
                    "type": "chat",
                    "context_length": 8192,
                }
            ]
        }

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.json.return_value = mock_response
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert len(models) == 1
        assert models[0]["pricing"]["prompt"] == "0.0"
        assert models[0]["pricing"]["completion"] == "0.0"

    def test_fetch_models_from_canopywave_handles_context_in_capabilities(self, monkeypatch):
        """Test that context_length in capabilities is properly extracted"""
        from src.services import canopywave_client

        mock_response = {
            "data": [
                {
                    "id": "test-model",
                    "name": "Test Model",
                    "type": "chat",
                    "capabilities": {"context_length": 32768},
                    "pricing": {"prompt": 0.000001, "completion": 0.000002},
                }
            ]
        }

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.json.return_value = mock_response
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert len(models) == 1
        assert models[0]["context_length"] == 32768

    def test_make_canopywave_request_openai(self, monkeypatch):
        """Test making a request to Canopy Wave using OpenAI client"""
        from src.services import canopywave_client

        fake_client = MagicMock()
        fake_response = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr(
            "src.services.canopywave_client.get_canopywave_client", lambda: fake_client
        )

        messages = [{"role": "user", "content": "Hello"}]
        response = canopywave_client.make_canopywave_request_openai(messages, "test-model")

        assert response == fake_response
        fake_client.chat.completions.create.assert_called_once_with(
            model="test-model", messages=messages
        )

    def test_make_canopywave_request_openai_stream(self, monkeypatch):
        """Test making a streaming request to Canopy Wave"""
        from src.services import canopywave_client

        fake_client = MagicMock()
        fake_stream = MagicMock()
        fake_client.chat.completions.create.return_value = fake_stream

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr(
            "src.services.canopywave_client.get_canopywave_client", lambda: fake_client
        )

        messages = [{"role": "user", "content": "Hello"}]
        stream = canopywave_client.make_canopywave_request_openai_stream(messages, "test-model")

        assert stream == fake_stream
        fake_client.chat.completions.create.assert_called_once_with(
            model="test-model", messages=messages, stream=True
        )

    def test_process_canopywave_response(self, monkeypatch):
        """Test processing a Canopy Wave response"""
        from src.services import canopywave_client

        fake_message = MagicMock()
        fake_message.content = "Hello, world!"
        fake_message.role = "assistant"

        fake_choice = MagicMock()
        fake_choice.index = 0
        fake_choice.message = fake_message
        fake_choice.finish_reason = "stop"

        fake_usage = MagicMock()
        fake_usage.prompt_tokens = 10
        fake_usage.completion_tokens = 5
        fake_usage.total_tokens = 15

        fake_response = MagicMock()
        fake_response.id = "chatcmpl-123"
        fake_response.object = "chat.completion"
        fake_response.created = 1234567890
        fake_response.model = "test-model"
        fake_response.choices = [fake_choice]
        fake_response.usage = fake_usage

        monkeypatch.setattr(
            "src.services.canopywave_client.extract_message_with_tools",
            lambda msg: {"role": msg.role, "content": msg.content},
        )

        result = canopywave_client.process_canopywave_response(fake_response)

        assert result["id"] == "chatcmpl-123"
        assert result["object"] == "chat.completion"
        assert result["created"] == 1234567890
        assert result["model"] == "test-model"
        assert len(result["choices"]) == 1
        assert result["choices"][0]["index"] == 0
        assert result["choices"][0]["message"]["content"] == "Hello, world!"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5
        assert result["usage"]["total_tokens"] == 15

    def test_is_canopywave_model_returns_true_for_valid_model(self, monkeypatch):
        """Test that is_canopywave_model returns True for a valid model"""
        from src.services import canopywave_client

        monkeypatch.setattr(
            "src.services.canopywave_client.fetch_models_from_canopywave",
            lambda: [{"id": "test-model"}, {"id": "another-model"}],
        )

        result = canopywave_client.is_canopywave_model("test-model")

        assert result is True

    def test_is_canopywave_model_returns_false_for_invalid_model(self, monkeypatch):
        """Test that is_canopywave_model returns False for an invalid model"""
        from src.services import canopywave_client

        monkeypatch.setattr(
            "src.services.canopywave_client.fetch_models_from_canopywave",
            lambda: [{"id": "test-model"}, {"id": "another-model"}],
        )

        result = canopywave_client.is_canopywave_model("nonexistent-model")

        assert result is False

    def test_is_canopywave_model_handles_errors(self, monkeypatch):
        """Test that is_canopywave_model handles errors gracefully"""
        from src.services import canopywave_client

        monkeypatch.setattr(
            "src.services.canopywave_client.fetch_models_from_canopywave",
            MagicMock(side_effect=Exception("API error")),
        )

        result = canopywave_client.is_canopywave_model("test-model")

        assert result is False

    def test_fetch_models_handles_function_calling_capability(self, monkeypatch):
        """Test that function_calling capability is mapped to tools feature"""
        from src.services import canopywave_client

        mock_response = {
            "data": [
                {
                    "id": "test-model",
                    "name": "Test Model",
                    "type": "chat",
                    "context_length": 8192,
                    "pricing": {"prompt": 0.000001, "completion": 0.000002},
                    "capabilities": {
                        "function_calling": True,
                    },
                }
            ]
        }

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.json.return_value = mock_response
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert len(models) == 1
        assert "tools" in models[0]["features"]

    def test_fetch_models_handles_web_search_capability(self, monkeypatch):
        """Test that web_search capability is properly extracted"""
        from src.services import canopywave_client

        mock_response = {
            "data": [
                {
                    "id": "test-model",
                    "name": "Test Model",
                    "type": "chat",
                    "context_length": 8192,
                    "pricing": {"prompt": 0.000001, "completion": 0.000002},
                    "capabilities": {
                        "web_search": True,
                    },
                }
            ]
        }

        fake_http_client = MagicMock()
        fake_response = MagicMock()
        fake_response.json.return_value = mock_response
        fake_http_client.get.return_value = fake_response
        fake_http_client.__enter__ = MagicMock(return_value=fake_http_client)
        fake_http_client.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr("src.config.Config.CANOPYWAVE_API_KEY", "test-api-key")
        monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_client))

        models = canopywave_client.fetch_models_from_canopywave()

        assert len(models) == 1
        assert "web_search" in models[0]["features"]
