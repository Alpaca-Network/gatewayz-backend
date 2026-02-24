"""
Comprehensive tests for the server-side tools system.

Tests cover:
- Tool base classes and types
- Tool registry and discovery
- Text-to-Speech tool
- Chatterbox TTS client
- API routes
- SSRF protection
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.chatterbox_tts_client import (
    CHATTERBOX_MODELS,
    LANGUAGE_NAMES,
    _is_safe_url,
    get_chatterbox_models,
    validate_chatterbox_model,
    validate_language,
)
from src.services.tools import (
    AVAILABLE_TOOLS,
    BaseTool,
    ToolDefinition,
    ToolResult,
    execute_tool,
    get_tool_by_name,
    get_tool_definitions,
)
from src.services.tools.text_to_speech import TextToSpeechTool

# =============================================================================
# BASE TOOL TESTS
# =============================================================================


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result(self):
        """Test creating a successful result."""
        result = ToolResult(success=True, result={"value": 42}, metadata={"execution_time": 0.1})
        assert result.success is True
        assert result.result == {"value": 42}
        assert result.error is None
        assert result.metadata == {"execution_time": 0.1}

    def test_error_result(self):
        """Test creating an error result."""
        result = ToolResult(success=False, error="Something went wrong")
        assert result.success is False
        assert result.result is None
        assert result.error == "Something went wrong"

    def test_to_dict(self):
        """Test converting result to dictionary."""
        result = ToolResult(success=True, result={"key": "value"}, metadata={"info": "data"})
        result_dict = result.to_dict()
        assert result_dict == {
            "success": True,
            "result": {"key": "value"},
            "error": None,
            "metadata": {"info": "data"},
        }


class TestBaseTool:
    """Tests for BaseTool abstract class."""

    def test_cannot_instantiate_directly(self):
        """Test that BaseTool cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseTool()

    def test_concrete_implementation(self):
        """Test implementing a concrete tool."""

        class TestTool(BaseTool):
            @classmethod
            def get_definition(cls) -> ToolDefinition:
                return {
                    "type": "function",
                    "function": {
                        "name": "test_tool",
                        "description": "A test tool",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }

            async def execute(self, **kwargs) -> ToolResult:
                return self._success(result={"status": "ok"})

        tool = TestTool()
        definition = tool.get_definition()
        assert definition["function"]["name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_success_helper(self):
        """Test the _success helper method."""

        class TestTool(BaseTool):
            @classmethod
            def get_definition(cls) -> ToolDefinition:
                return {
                    "type": "function",
                    "function": {"name": "test", "description": "", "parameters": {}},
                }

            async def execute(self, **kwargs) -> ToolResult:
                return self._success(result={"data": "test"}, extra_meta="value")

        tool = TestTool()
        result = await tool.execute()
        assert result.success is True
        assert result.result == {"data": "test"}
        assert result.metadata.get("extra_meta") == "value"

    @pytest.mark.asyncio
    async def test_error_helper(self):
        """Test the _error helper method."""

        class TestTool(BaseTool):
            @classmethod
            def get_definition(cls) -> ToolDefinition:
                return {
                    "type": "function",
                    "function": {"name": "test", "description": "", "parameters": {}},
                }

            async def execute(self, **kwargs) -> ToolResult:
                return self._error("Something failed", error_type="test_error")

        tool = TestTool()
        result = await tool.execute()
        assert result.success is False
        assert result.error == "Something failed"
        assert result.metadata.get("error_type") == "test_error"


# =============================================================================
# TOOL REGISTRY TESTS
# =============================================================================


class TestToolRegistry:
    """Tests for tool registry functions."""

    def test_available_tools_not_empty(self):
        """Test that AVAILABLE_TOOLS is not empty."""
        assert len(AVAILABLE_TOOLS) > 0

    def test_text_to_speech_registered(self):
        """Test that text_to_speech tool is registered."""
        assert "text_to_speech" in AVAILABLE_TOOLS
        assert AVAILABLE_TOOLS["text_to_speech"] == TextToSpeechTool

    def test_get_tool_definitions(self):
        """Test getting all tool definitions."""
        definitions = get_tool_definitions()
        assert isinstance(definitions, list)
        assert len(definitions) == len(AVAILABLE_TOOLS)

        # Check each definition has required fields
        for definition in definitions:
            assert definition["type"] == "function"
            assert "function" in definition
            assert "name" in definition["function"]
            assert "description" in definition["function"]
            assert "parameters" in definition["function"]

    def test_get_tool_by_name_exists(self):
        """Test getting an existing tool by name."""
        tool_class = get_tool_by_name("text_to_speech")
        assert tool_class is not None
        assert tool_class == TextToSpeechTool

    def test_get_tool_by_name_not_exists(self):
        """Test getting a non-existent tool."""
        tool_class = get_tool_by_name("nonexistent_tool")
        assert tool_class is None

    @pytest.mark.asyncio
    async def test_execute_tool_success(self):
        """Test executing a tool by name with parameter validation."""
        with patch.object(TextToSpeechTool, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True, result={"audio_base64": "test_audio"}
            )

            result = await execute_tool("text_to_speech", {"text": "Hello world"})

            assert result.success is True
            assert result.result == {"audio_base64": "test_audio"}
            mock_execute.assert_called_once_with(text="Hello world")

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        """Test executing a non-existent tool raises ValueError."""
        with pytest.raises(ValueError, match="Tool 'nonexistent' not found"):
            await execute_tool("nonexistent")


# =============================================================================
# CHATTERBOX TTS CLIENT TESTS
# =============================================================================


class TestChatterboxModels:
    """Tests for Chatterbox model configuration."""

    def test_models_defined(self):
        """Test that Chatterbox models are defined."""
        assert len(CHATTERBOX_MODELS) == 3
        assert "chatterbox-turbo" in CHATTERBOX_MODELS
        assert "chatterbox-multilingual" in CHATTERBOX_MODELS
        assert "chatterbox" in CHATTERBOX_MODELS

    def test_model_has_required_fields(self):
        """Test that each model has required fields."""
        for model_id, info in CHATTERBOX_MODELS.items():
            assert "name" in info
            assert "description" in info
            assert "parameters" in info
            assert "languages" in info
            assert "features" in info

    def test_turbo_model_english_only(self):
        """Test that turbo model supports only English."""
        assert CHATTERBOX_MODELS["chatterbox-turbo"]["languages"] == ["en"]

    def test_multilingual_supports_many_languages(self):
        """Test that multilingual model supports 22+ languages."""
        languages = CHATTERBOX_MODELS["chatterbox-multilingual"]["languages"]
        assert len(languages) >= 22
        assert "en" in languages
        assert "fr" in languages
        assert "es" in languages
        assert "ja" in languages
        assert "zh" in languages

    def test_get_chatterbox_models(self):
        """Test getting formatted model list."""
        models = get_chatterbox_models()
        assert len(models) == 3

        for model in models:
            assert "id" in model
            assert "name" in model
            assert "description" in model
            assert "parameters" in model
            assert "languages" in model
            assert "features" in model

    def test_validate_chatterbox_model_valid(self):
        """Test validating valid model IDs."""
        assert validate_chatterbox_model("chatterbox-turbo") is True
        assert validate_chatterbox_model("chatterbox-multilingual") is True
        assert validate_chatterbox_model("chatterbox") is True

    def test_validate_chatterbox_model_invalid(self):
        """Test validating invalid model IDs."""
        assert validate_chatterbox_model("invalid-model") is False
        assert validate_chatterbox_model("") is False
        assert validate_chatterbox_model("gpt-4") is False

    def test_validate_language_valid(self):
        """Test validating valid language codes."""
        assert validate_language("chatterbox-multilingual", "en") is True
        assert validate_language("chatterbox-multilingual", "fr") is True
        assert validate_language("chatterbox-multilingual", "ja") is True

    def test_validate_language_invalid_language(self):
        """Test validating invalid language codes."""
        assert validate_language("chatterbox-multilingual", "xx") is False
        assert validate_language("chatterbox-multilingual", "invalid") is False

    def test_validate_language_wrong_model(self):
        """Test validating language for wrong model."""
        # Turbo only supports English
        assert validate_language("chatterbox-turbo", "en") is True
        assert validate_language("chatterbox-turbo", "fr") is False

    def test_validate_language_invalid_model(self):
        """Test validating language for non-existent model."""
        assert validate_language("invalid-model", "en") is False


class TestLanguageNames:
    """Tests for language name mapping."""

    def test_language_names_defined(self):
        """Test that language names are defined."""
        assert len(LANGUAGE_NAMES) >= 22

    def test_common_languages_present(self):
        """Test that common languages are present."""
        assert LANGUAGE_NAMES["en"] == "English"
        assert LANGUAGE_NAMES["es"] == "Spanish"
        assert LANGUAGE_NAMES["fr"] == "French"
        assert LANGUAGE_NAMES["de"] == "German"
        assert LANGUAGE_NAMES["ja"] == "Japanese"
        assert LANGUAGE_NAMES["zh"] == "Chinese"


# =============================================================================
# SSRF PROTECTION TESTS
# =============================================================================


class TestSSRFProtection:
    """Tests for SSRF protection in URL validation."""

    def test_safe_public_urls(self):
        """Test that public URLs are allowed.

        We mock DNS resolution since test environments may not have network access.
        """
        # Mock DNS to return a safe public IP (8.8.8.8 - Google DNS)
        with patch("socket.gethostbyname", return_value="8.8.8.8"):
            assert _is_safe_url("https://example.com/audio.wav") is True
            assert _is_safe_url("https://storage.googleapis.com/bucket/file.wav") is True
            assert _is_safe_url("http://cdn.example.org/voice.mp3") is True

    def test_blocks_localhost(self):
        """Test that localhost URLs are blocked."""
        assert _is_safe_url("http://localhost/file") is False
        assert _is_safe_url("http://localhost:8080/file") is False
        assert _is_safe_url("http://127.0.0.1/file") is False
        assert _is_safe_url("http://127.0.0.1:3000/file") is False

    def test_blocks_private_ips(self):
        """Test that private IP ranges are blocked."""
        # 10.x.x.x
        assert _is_safe_url("http://10.0.0.1/file") is False
        assert _is_safe_url("http://10.255.255.255/file") is False
        # 172.16.x.x - 172.31.x.x
        assert _is_safe_url("http://172.16.0.1/file") is False
        assert _is_safe_url("http://172.31.255.255/file") is False
        # 192.168.x.x
        assert _is_safe_url("http://192.168.1.1/file") is False
        assert _is_safe_url("http://192.168.0.100/file") is False

    def test_blocks_cloud_metadata(self):
        """Test that cloud metadata URLs are blocked."""
        # AWS metadata
        assert _is_safe_url("http://169.254.169.254/latest/meta-data/") is False
        # Link-local
        assert _is_safe_url("http://169.254.1.1/") is False

    def test_blocks_non_http_schemes(self):
        """Test that non-HTTP schemes are blocked."""
        assert _is_safe_url("file:///etc/passwd") is False
        assert _is_safe_url("ftp://example.com/file") is False
        assert _is_safe_url("gopher://example.com/") is False

    def test_blocks_empty_or_invalid(self):
        """Test that empty or invalid URLs are blocked."""
        assert _is_safe_url("") is False
        assert _is_safe_url("not-a-url") is False
        assert _is_safe_url("://missing-scheme") is False


# =============================================================================
# TEXT-TO-SPEECH TOOL TESTS
# =============================================================================


class TestTextToSpeechTool:
    """Tests for TextToSpeechTool."""

    def test_get_definition(self):
        """Test tool definition."""
        definition = TextToSpeechTool.get_definition()

        assert definition["type"] == "function"
        assert definition["function"]["name"] == "text_to_speech"
        assert "description" in definition["function"]

        # Check parameters
        params = definition["function"]["parameters"]
        assert params["type"] == "object"
        assert "text" in params["properties"]
        assert "model" in params["properties"]
        assert "language" in params["properties"]
        assert "voice_reference_url" in params["properties"]
        assert "exaggeration" in params["properties"]
        assert "cfg_weight" in params["properties"]
        assert "text" in params["required"]

    def test_model_enum_in_definition(self):
        """Test that model parameter has correct enum values."""
        definition = TextToSpeechTool.get_definition()
        model_param = definition["function"]["parameters"]["properties"]["model"]

        assert "enum" in model_param
        assert "chatterbox-turbo" in model_param["enum"]
        assert "chatterbox-multilingual" in model_param["enum"]
        assert "chatterbox" in model_param["enum"]

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """Test successful TTS execution."""
        with patch(
            "src.services.tools.text_to_speech.generate_speech", new_callable=AsyncMock
        ) as mock_generate:
            mock_generate.return_value = {
                "audio_url": None,
                "audio_base64": "data:audio/wav;base64,SGVsbG8=",
                "duration": 1.5,
                "format": "wav",
            }

            tool = TextToSpeechTool()
            result = await tool.execute(text="Hello world")

            assert result.success is True
            assert result.result["audio_base64"] == "data:audio/wav;base64,SGVsbG8="
            assert result.result["duration"] == 1.5
            assert result.result["format"] == "wav"
            mock_generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_validation_error(self):
        """Test TTS execution with validation error.

        When text is empty, the tool catches it early with a validation error
        before calling generate_speech.
        """
        tool = TextToSpeechTool()
        result = await tool.execute(text="")

        assert result.success is False
        assert "required" in result.error.lower() or "empty" in result.error.lower()
        assert result.metadata.get("error_type") == "validation"

    @pytest.mark.asyncio
    async def test_execute_runtime_error(self):
        """Test TTS execution with runtime error."""
        with patch(
            "src.services.tools.text_to_speech.generate_speech", new_callable=AsyncMock
        ) as mock_generate:
            mock_generate.side_effect = RuntimeError("TTS generation failed")

            tool = TextToSpeechTool()
            result = await tool.execute(text="Hello world")

            assert result.success is False
            assert "failed" in result.error.lower()
            assert result.metadata.get("error_type") == "generation"

    @pytest.mark.asyncio
    async def test_execute_with_all_options(self):
        """Test TTS execution with all options and parameter validation."""
        with patch(
            "src.services.tools.text_to_speech.generate_speech", new_callable=AsyncMock
        ) as mock_generate:
            mock_generate.return_value = {
                "audio_url": None,
                "audio_base64": "data:audio/wav;base64,SGVsbG8=",
                "duration": 2.0,
                "format": "wav",
            }

            tool = TextToSpeechTool()
            result = await tool.execute(
                text="Bonjour le monde",
                model="chatterbox-multilingual",
                language="fr",
                voice_reference_url="https://example.com/voice.wav",
                exaggeration=1.5,
                cfg_weight=0.7,
            )

            assert result.success is True
            # Verify all parameters were passed correctly
            mock_generate.assert_called_once()
            call_kwargs = mock_generate.call_args.kwargs
            assert call_kwargs["text"] == "Bonjour le monde"
            assert call_kwargs["model"] == "chatterbox-multilingual"
            assert call_kwargs["language"] == "fr"
            assert call_kwargs["voice_reference_url"] == "https://example.com/voice.wav"
            assert call_kwargs["exaggeration"] == 1.5
            assert call_kwargs["cfg_weight"] == 0.7


# =============================================================================
# TTS CLIENT TESTS
# =============================================================================


class TestGenerateSpeech:
    """Tests for generate_speech function."""

    @pytest.mark.asyncio
    async def test_empty_text_raises_error(self):
        """Test that empty text raises ValueError."""
        from src.services.chatterbox_tts_client import generate_speech

        with pytest.raises(ValueError, match="Text cannot be empty"):
            await generate_speech("")

    @pytest.mark.asyncio
    async def test_whitespace_text_raises_error(self):
        """Test that whitespace-only text raises ValueError."""
        from src.services.chatterbox_tts_client import generate_speech

        with pytest.raises(ValueError, match="Text cannot be empty"):
            await generate_speech("   ")

    @pytest.mark.asyncio
    async def test_text_too_long_raises_error(self):
        """Test that text over 5000 chars raises ValueError."""
        from src.services.chatterbox_tts_client import generate_speech

        long_text = "a" * 5001
        with pytest.raises(ValueError, match="Text too long"):
            await generate_speech(long_text)

    @pytest.mark.asyncio
    async def test_invalid_model_raises_error(self):
        """Test that invalid model raises ValueError."""
        from src.services.chatterbox_tts_client import generate_speech

        with pytest.raises(ValueError, match="Invalid model"):
            await generate_speech("Hello", model="invalid-model")

    @pytest.mark.asyncio
    async def test_invalid_language_for_model_raises_error(self):
        """Test that invalid language for model raises ValueError."""
        from src.services.chatterbox_tts_client import generate_speech

        with pytest.raises(ValueError, match="Language.*not supported"):
            await generate_speech("Hello", model="chatterbox-multilingual", language="invalid_lang")

    @pytest.mark.asyncio
    async def test_ssrf_url_raises_error(self):
        """Test that SSRF URLs are rejected."""
        from src.services.chatterbox_tts_client import generate_speech

        with pytest.raises(ValueError, match="Invalid voice reference URL"):
            await generate_speech(
                "Hello", voice_reference_url="http://169.254.169.254/latest/meta-data/"
            )

    @pytest.mark.asyncio
    async def test_localhost_url_raises_error(self):
        """Test that localhost URLs are rejected."""
        from src.services.chatterbox_tts_client import generate_speech

        with pytest.raises(ValueError, match="Invalid voice reference URL"):
            await generate_speech("Hello", voice_reference_url="http://localhost:8080/audio.wav")


class TestVoiceReferenceFileSize:
    """Tests for voice reference file size limits."""

    def test_max_file_size_constant_defined(self):
        """Test that max file size constant is defined."""
        from src.services.chatterbox_tts_client import CHATTERBOX_MAX_VOICE_REF_SIZE

        # Should be 10 MB
        assert CHATTERBOX_MAX_VOICE_REF_SIZE == 10 * 1024 * 1024


# =============================================================================
# API ROUTE TESTS
# =============================================================================


class TestToolsRoute:
    """Tests for tools API routes."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked auth."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.routes.tools import router

        app = FastAPI()
        app.include_router(router)

        # Override auth dependency for testing
        from src.security.deps import get_api_key

        app.dependency_overrides[get_api_key] = lambda: "test-api-key"

        return TestClient(app)

    def test_list_tools(self, client):
        """Test listing all tools."""
        response = client.get("/tools")
        assert response.status_code == 200

        data = response.json()
        assert "tools" in data
        assert "count" in data
        assert data["count"] == len(AVAILABLE_TOOLS)

    def test_get_definitions(self, client):
        """Test getting tool definitions."""
        response = client.get("/tools/definitions")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == len(AVAILABLE_TOOLS)

    def test_get_tool_info_exists(self, client):
        """Test getting info for existing tool."""
        response = client.get("/tools/text_to_speech")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "text_to_speech"
        assert data["available"] is True
        assert "definition" in data

    def test_get_tool_info_not_exists(self, client):
        """Test getting info for non-existent tool."""
        response = client.get("/tools/nonexistent")
        assert response.status_code == 404

    def test_execute_tool_success(self, client):
        """Test executing a tool successfully with auth."""
        with patch.object(TextToSpeechTool, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True, result={"audio_base64": "test_audio"}, metadata={}
            )

            response = client.post(
                "/tools/execute",
                json={"name": "text_to_speech", "parameters": {"text": "Hello world"}},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["result"]["audio_base64"] == "test_audio"

    def test_execute_tool_not_found(self, client):
        """Test executing a non-existent tool."""
        response = client.post("/tools/execute", json={"name": "nonexistent", "parameters": {}})

        assert response.status_code == 404

    def test_execute_tool_validation_error(self, client):
        """Test executing a tool with validation error."""
        with patch.object(TextToSpeechTool, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = ValueError("Invalid parameter")

            response = client.post(
                "/tools/execute", json={"name": "text_to_speech", "parameters": {"text": ""}}
            )

            assert response.status_code == 400


# =============================================================================
# INTEGRATION TESTS
# =============================================================================


class TestToolIntegration:
    """Integration tests for the tools system."""

    def test_all_tools_have_valid_definitions(self):
        """Test that all registered tools have valid definitions."""
        for name, tool_class in AVAILABLE_TOOLS.items():
            definition = tool_class.get_definition()

            # Check structure
            assert definition["type"] == "function"
            assert "function" in definition

            func = definition["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

            # Name should match registration key
            # (allowing for slight variations like underscores)
            assert name.replace("-", "_") == func["name"].replace("-", "_")

    def test_all_tools_are_instantiable(self):
        """Test that all registered tools can be instantiated."""
        for name, tool_class in AVAILABLE_TOOLS.items():
            tool = tool_class()
            assert isinstance(tool, BaseTool)

    @pytest.mark.asyncio
    async def test_all_tools_have_execute_method(self):
        """Test that all tools have an async execute method."""
        import inspect

        for name, tool_class in AVAILABLE_TOOLS.items():
            tool = tool_class()
            assert hasattr(tool, "execute")
            assert inspect.iscoroutinefunction(tool.execute)


# =============================================================================
# SEARCH AUGMENTATION TESTS
# =============================================================================


class TestSearchAugmentRoute:
    """Tests for the search augmentation endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client with mocked auth."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.routes.tools import router

        app = FastAPI()
        app.include_router(router)

        # Override auth dependency for testing
        from src.security.deps import get_optional_api_key

        app.dependency_overrides[get_optional_api_key] = lambda: None

        return TestClient(app)

    def test_search_augment_success(self, client):
        """Test successful search augmentation."""
        with patch("src.routes.tools.execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True,
                result={
                    "results": [
                        {
                            "title": "Test Title",
                            "content": "Test content about the query",
                            "url": "https://example.com/test",
                        }
                    ],
                    "answer": "This is a summary of the search results",
                },
                metadata={},
            )

            response = client.post(
                "/tools/search/augment",
                json={"query": "test query", "max_results": 5, "include_answer": True},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["results_count"] == 1
            assert "[Web Search Results]" in data["context"]
            assert "Summary:" in data["context"]
            assert "Test Title" in data["context"]
            assert "[End of Search Results]" in data["context"]

    def test_search_augment_with_parameters(self, client):
        """Test search augmentation passes correct parameters."""
        with patch("src.routes.tools.execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True, result={"results": [], "answer": None}, metadata={}
            )

            response = client.post(
                "/tools/search/augment",
                json={"query": "specific query", "max_results": 3, "include_answer": False},
            )

            mock_execute.assert_called_once_with(
                "web_search",
                {
                    "query": "specific query",
                    "max_results": 3,
                    "include_answer": False,
                    "search_depth": "basic",
                },
            )

    def test_search_augment_no_results(self, client):
        """Test search augmentation with no results."""
        with patch("src.routes.tools.execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True, result={"results": [], "answer": None}, metadata={}
            )

            response = client.post(
                "/tools/search/augment", json={"query": "obscure query with no results"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["context"] is None
            assert data["results_count"] == 0
            assert data["error"] == "No results found"

    def test_search_augment_tool_failure(self, client):
        """Test search augmentation when tool execution fails."""
        with patch("src.routes.tools.execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=False, error="Search service unavailable", metadata={}
            )

            response = client.post("/tools/search/augment", json={"query": "test query"})

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert data["error"] == "Search service unavailable"
            assert data["results_count"] == 0

    def test_search_augment_validation_empty_query(self, client):
        """Test search augmentation with empty query."""
        response = client.post("/tools/search/augment", json={"query": ""})

        assert response.status_code == 422  # Validation error

    def test_search_augment_validation_invalid_max_results(self, client):
        """Test search augmentation with invalid max_results."""
        response = client.post(
            "/tools/search/augment",
            json={"query": "test", "max_results": 100},  # Too high, max is 10
        )

        assert response.status_code == 422  # Validation error

    def test_search_augment_truncates_long_content(self, client):
        """Test that search augmentation truncates long content."""
        long_content = "x" * 500  # Longer than 300 char limit

        with patch("src.routes.tools.execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True,
                result={
                    "results": [
                        {
                            "title": "Test Title",
                            "content": long_content,
                            "url": "https://example.com/test",
                        }
                    ],
                    "answer": None,
                },
                metadata={},
            )

            response = client.post("/tools/search/augment", json={"query": "test query"})

            assert response.status_code == 200
            data = response.json()
            # Content should be truncated with "..."
            assert "..." in data["context"]
            # The full long content should not be there
            assert long_content not in data["context"]

    def test_search_augment_multiple_results(self, client):
        """Test search augmentation formats multiple results correctly."""
        with patch("src.routes.tools.execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True,
                result={
                    "results": [
                        {
                            "title": "First Result",
                            "content": "First content",
                            "url": "https://first.com",
                        },
                        {
                            "title": "Second Result",
                            "content": "Second content",
                            "url": "https://second.com",
                        },
                        {
                            "title": "Third Result",
                            "content": "Third content",
                            "url": "https://third.com",
                        },
                    ],
                    "answer": "Combined answer",
                },
                metadata={},
            )

            response = client.post("/tools/search/augment", json={"query": "test query"})

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["results_count"] == 3
            assert "1. First Result" in data["context"]
            assert "2. Second Result" in data["context"]
            assert "3. Third Result" in data["context"]

    def test_search_augment_exception_handling(self, client):
        """Test search augmentation handles exceptions gracefully."""
        with patch("src.routes.tools.execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = RuntimeError("Unexpected error")

            response = client.post("/tools/search/augment", json={"query": "test query"})

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert data["error"] == "An unexpected error occurred during search"
            assert data["results_count"] == 0
