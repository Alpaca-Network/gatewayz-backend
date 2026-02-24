"""Tests for Cloudflare Workers AI client"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.services.cloudflare_workers_ai_client import (
    DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS,
    fetch_models_from_cloudflare_api,
    fetch_models_from_cloudflare_workers_ai,
    get_cloudflare_workers_ai_client,
    make_cloudflare_workers_ai_request_openai,
    make_cloudflare_workers_ai_request_openai_stream,
    process_cloudflare_workers_ai_response,
)


class TestCloudflareWorkersAIClient:
    """Test Cloudflare Workers AI client functionality"""

    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    @patch("src.services.cloudflare_workers_ai_client.get_cloudflare_workers_ai_pooled_client")
    def test_get_cloudflare_workers_ai_client(self, mock_pooled_client):
        """Test getting Cloudflare Workers AI client"""
        mock_client = Mock()
        mock_pooled_client.return_value = mock_client

        client = get_cloudflare_workers_ai_client()

        assert client is not None
        mock_pooled_client.assert_called_once()

    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", None)
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    def test_get_cloudflare_workers_ai_client_no_token(self):
        """Test getting Cloudflare Workers AI client without API token"""
        with pytest.raises(ValueError, match="Cloudflare API token not configured"):
            get_cloudflare_workers_ai_client()

    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", None)
    def test_get_cloudflare_workers_ai_client_no_account_id(self):
        """Test getting Cloudflare Workers AI client without Account ID"""
        with pytest.raises(ValueError, match="Cloudflare Account ID not configured"):
            get_cloudflare_workers_ai_client()

    @patch("src.services.cloudflare_workers_ai_client.get_cloudflare_workers_ai_client")
    def test_make_cloudflare_workers_ai_request_openai(self, mock_get_client):
        """Test making request to Cloudflare Workers AI"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "@cf/meta/llama-3.1-8b-instruct"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_cloudflare_workers_ai_request_openai(
            messages, "@cf/meta/llama-3.1-8b-instruct"
        )

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.cloudflare_workers_ai_client.get_cloudflare_workers_ai_client")
    def test_make_cloudflare_workers_ai_request_openai_with_kwargs(self, mock_get_client):
        """Test making request to Cloudflare Workers AI with additional parameters"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_cloudflare_workers_ai_request_openai(
            messages, "@cf/meta/llama-3.1-8b-instruct", max_tokens=100, temperature=0.7
        )

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="@cf/meta/llama-3.1-8b-instruct",
            messages=messages,
            max_tokens=100,
            temperature=0.7,
        )

    @patch("src.services.cloudflare_workers_ai_client.get_cloudflare_workers_ai_client")
    def test_make_cloudflare_workers_ai_request_openai_error(self, mock_get_client):
        """Test handling errors from Cloudflare Workers AI"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="API Error"):
            make_cloudflare_workers_ai_request_openai(messages, "@cf/meta/llama-3.1-8b-instruct")

    @patch("src.services.cloudflare_workers_ai_client.get_cloudflare_workers_ai_client")
    def test_make_cloudflare_workers_ai_request_openai_stream(self, mock_get_client):
        """Test making streaming request to Cloudflare Workers AI"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_cloudflare_workers_ai_request_openai_stream(messages, "@cf/qwen/qwq-32b")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="@cf/qwen/qwq-32b", messages=messages, stream=True
        )

    @patch("src.services.cloudflare_workers_ai_client.get_cloudflare_workers_ai_client")
    def test_make_cloudflare_workers_ai_request_openai_stream_with_kwargs(self, mock_get_client):
        """Test making streaming request to Cloudflare Workers AI with additional parameters"""
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_cloudflare_workers_ai_request_openai_stream(
            messages, "@cf/openai/gpt-oss-120b", max_tokens=500
        )

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="@cf/openai/gpt-oss-120b",
            messages=messages,
            stream=True,
            max_tokens=500,
        )

    @patch("src.services.cloudflare_workers_ai_client.get_cloudflare_workers_ai_client")
    def test_make_cloudflare_workers_ai_request_openai_stream_error(self, mock_get_client):
        """Test handling streaming errors from Cloudflare Workers AI"""
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("Stream Error")
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        with pytest.raises(Exception, match="Stream Error"):
            make_cloudflare_workers_ai_request_openai_stream(
                messages, "@cf/meta/llama-3.1-8b-instruct"
            )

    def test_process_cloudflare_workers_ai_response(self):
        """Test processing Cloudflare Workers AI response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "chatcmpl-test123"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "@cf/meta/llama-3.1-8b-instruct"

        # Mock choice
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Hello! How can I help you today?"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        # Mock usage
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 15
        mock_response.usage.completion_tokens = 25
        mock_response.usage.total_tokens = 40

        processed = process_cloudflare_workers_ai_response(mock_response)

        assert processed["id"] == "chatcmpl-test123"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "@cf/meta/llama-3.1-8b-instruct"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["index"] == 0
        assert processed["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
        assert processed["choices"][0]["finish_reason"] == "stop"
        assert processed["usage"]["prompt_tokens"] == 15
        assert processed["usage"]["completion_tokens"] == 25
        assert processed["usage"]["total_tokens"] == 40

    def test_process_cloudflare_workers_ai_response_no_usage(self):
        """Test processing Cloudflare Workers AI response without usage data"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "@cf/meta/llama-3.1-8b-instruct"

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        processed = process_cloudflare_workers_ai_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["usage"] == {}

    def test_process_cloudflare_workers_ai_response_multiple_choices(self):
        """Test processing Cloudflare Workers AI response with multiple choices"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "@cf/openai/gpt-oss-120b"

        # Create multiple choices
        choices = []
        for i in range(3):
            mock_choice = Mock()
            mock_choice.index = i
            mock_choice.message = Mock()
            mock_choice.message.role = "assistant"
            mock_choice.message.content = f"Response {i}"
            mock_choice.message.tool_calls = None
            mock_choice.finish_reason = "stop"
            choices.append(mock_choice)

        mock_response.choices = choices
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 30
        mock_response.usage.total_tokens = 40

        processed = process_cloudflare_workers_ai_response(mock_response)

        assert len(processed["choices"]) == 3
        for i, choice in enumerate(processed["choices"]):
            assert choice["index"] == i
            assert choice["message"]["content"] == f"Response {i}"

    def test_fetch_models_from_cloudflare_workers_ai(self):
        """Test fetching models from Cloudflare Workers AI"""
        models = fetch_models_from_cloudflare_workers_ai()

        assert models is not None
        assert len(models) > 0
        assert models == DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS

    def test_default_cloudflare_workers_ai_models(self):
        """Test the default Cloudflare Workers AI models catalog"""
        models = DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS

        # Verify structure of model entries
        for model in models:
            assert "id" in model
            assert "name" in model
            assert "description" in model
            assert "provider" in model
            assert model["provider"] == "cloudflare-workers-ai"
            # All model IDs should start with @cf/
            assert model["id"].startswith("@cf/"), f"Model {model['id']} should start with @cf/"

        # Verify some key models are present
        model_ids = [m["id"] for m in models]
        assert "@cf/openai/gpt-oss-120b" in model_ids
        assert "@cf/meta/llama-3.1-8b-instruct" in model_ids
        assert "@cf/qwen/qwq-32b" in model_ids

    def test_default_cloudflare_workers_ai_models_have_source_gateway(self):
        """Test that all default Cloudflare Workers AI models have source_gateway field"""
        models = DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS

        for model in models:
            assert "source_gateway" in model, f"Model {model['id']} missing source_gateway field"
            assert (
                model["source_gateway"] == "cloudflare-workers-ai"
            ), f"Model {model['id']} has incorrect source_gateway: {model.get('source_gateway')}"


class TestFetchModelsFromCloudflareAPI:
    """Test fetching models from Cloudflare API with various response formats"""

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_properties_as_list(self):
        """Test parsing models when properties is a list of property objects"""
        # This is the format that Cloudflare API actually returns
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": [
                        {"property_id": "max_total_tokens", "value": "16384"},
                        {"property_id": "some_other_property", "value": "test"},
                    ],
                    "task": {"name": "Text Generation"},
                },
                {
                    "name": "@cf/openai/gpt-oss-120b",
                    "description": "GPT-OSS 120B",
                    "properties": [
                        {"property_id": "max_total_tokens", "value": "8192"},
                    ],
                    "task": {"name": "Text Generation"},
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 2
            assert models[0]["id"] == "@cf/meta/llama-3.1-8b-instruct"
            assert models[0]["context_length"] == 16384
            assert models[0]["provider"] == "cloudflare-workers-ai"
            assert models[0]["source_gateway"] == "cloudflare-workers-ai"
            assert models[1]["id"] == "@cf/openai/gpt-oss-120b"
            assert models[1]["context_length"] == 8192
            assert models[1]["source_gateway"] == "cloudflare-workers-ai"

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_properties_as_dict(self):
        """Test parsing models when properties is a dict (legacy format)"""
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": {"max_total_tokens": 32768},
                    "task": {"name": "Text Generation"},
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 1
            assert models[0]["context_length"] == 32768

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_missing_properties(self):
        """Test parsing models when properties field is missing"""
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "task": {"name": "Text Generation"},
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 1
            # Should use default context length
            assert models[0]["context_length"] == 8192

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_task_as_string(self):
        """Test parsing models when task is a string instead of a dict"""
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": [],
                    "task": "Text Generation",
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 1
            assert models[0]["task"] == "Text Generation"

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_invalid_context_length_value(self):
        """Test parsing models when max_total_tokens has an invalid value"""
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": [
                        {"property_id": "max_total_tokens", "value": "invalid"},
                    ],
                    "task": {"name": "Text Generation"},
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 1
            # Should use default context length when value is invalid
            assert models[0]["context_length"] == 8192

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", None)
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_without_api_token(self):
        """Test that API returns empty list when token is missing"""
        models = await fetch_models_from_cloudflare_api()
        assert models == []

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", None)
    async def test_fetch_models_without_account_id(self):
        """Test that API returns empty list when account ID is missing"""
        models = await fetch_models_from_cloudflare_api()
        assert models == []

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_null_task(self):
        """Test parsing models when task is null"""
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": [],
                    "task": None,
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 1
            # Should use default task name when task is null
            assert models[0]["task"] == "Text Generation"

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_dict_properties_string_value(self):
        """Test parsing models when properties dict has string value for max_total_tokens"""
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": {"max_total_tokens": "16384"},
                    "task": {"name": "Text Generation"},
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 1
            # Should correctly parse string value to int
            assert models[0]["context_length"] == 16384

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_dict_properties_invalid_value(self):
        """Test parsing models when properties dict has invalid value for max_total_tokens"""
        mock_response_data = {
            "success": True,
            "result": [
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": {"max_total_tokens": "not_a_number"},
                    "task": {"name": "Text Generation"},
                },
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            assert len(models) == 1
            # Should use default context length when value is invalid
            assert models[0]["context_length"] == 8192

    @pytest.mark.asyncio
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_API_TOKEN", "test_token")
    @patch("src.services.cloudflare_workers_ai_client.Config.CLOUDFLARE_ACCOUNT_ID", "test_account")
    async def test_fetch_models_with_non_dict_items_in_result(self):
        """Test parsing models when result array contains non-dict items (lists, strings, etc.)"""
        mock_response_data = {
            "success": True,
            "result": [
                # Valid dict entry
                {
                    "name": "@cf/meta/llama-3.1-8b-instruct",
                    "description": "Meta Llama 3.1 8B Instruct",
                    "properties": [],
                    "task": {"name": "Text Generation"},
                },
                # Invalid: list entry (should be skipped)
                ["unexpected", "list", "data"],
                # Invalid: string entry (should be skipped)
                "unexpected string data",
                # Another valid dict entry
                {
                    "name": "@cf/openai/gpt-oss-120b",
                    "description": "GPT-OSS 120B",
                    "properties": [],
                    "task": {"name": "Text Generation"},
                },
                # Invalid: None (should be skipped)
                None,
                # Invalid: integer (should be skipped)
                42,
            ],
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            models = await fetch_models_from_cloudflare_api()

            # Should only include the 2 valid dict entries
            assert len(models) == 2
            assert models[0]["id"] == "@cf/meta/llama-3.1-8b-instruct"
            assert models[1]["id"] == "@cf/openai/gpt-oss-120b"
