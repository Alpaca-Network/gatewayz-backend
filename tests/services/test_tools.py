"""
Comprehensive tests for the server-side tools system.

Tests cover:
- Tool base classes and types
- Tool registry and discovery
- Text-to-Speech tool
- Chatterbox TTS client
- API routes
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.tools import (
    AVAILABLE_TOOLS,
    BaseTool,
    ToolDefinition,
    ToolResult,
    execute_tool,
    get_tool_by_name,
    get_tool_definitions,
)
from src.services.tools.base import ToolFunction, ToolFunctionParameters
from src.services.tools.text_to_speech import TextToSpeechTool
from src.services.chatterbox_tts_client import (
    CHATTERBOX_MODELS,
    LANGUAGE_NAMES,
    get_chatterbox_models,
    validate_chatterbox_model,
    validate_language,
)


# =============================================================================
# BASE TOOL TESTS
# =============================================================================


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result(self):
        """Test creating a successful result."""
        result = ToolResult(
            success=True,
            result={"value": 42},
            metadata={"execution_time": 0.1}
        )
        assert result.success is True
        assert result.result == {"value": 42}
        assert result.error is None
        assert result.metadata == {"execution_time": 0.1}

    def test_error_result(self):
        """Test creating an error result."""
        result = ToolResult(
            success=False,
            error="Something went wrong"
        )
        assert result.success is False
        assert result.result is None
        assert result.error == "Something went wrong"

    def test_to_dict(self):
        """Test converting result to dictionary."""
        result = ToolResult(
            success=True,
            result={"key": "value"},
            metadata={"info": "data"}
        )
        result_dict = result.to_dict()
        assert result_dict == {
            "success": True,
            "result": {"key": "value"},
            "error": None,
            "metadata": {"info": "data"}
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
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
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
                return {"type": "function", "function": {"name": "test", "description": "", "parameters": {}}}

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
                return {"type": "function", "function": {"name": "test", "description": "", "parameters": {}}}

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
        """Test executing a tool by name."""
        with patch.object(TextToSpeechTool, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True,
                result={"audio_base64": "test_audio"}
            )

            result = await execute_tool("text_to_speech", text="Hello world")

            assert result.success is True
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
        with patch("src.services.tools.text_to_speech.generate_speech", new_callable=AsyncMock) as mock_generate:
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
        """Test TTS execution with validation error."""
        with patch("src.services.tools.text_to_speech.generate_speech", new_callable=AsyncMock) as mock_generate:
            mock_generate.side_effect = ValueError("Text cannot be empty")

            tool = TextToSpeechTool()
            result = await tool.execute(text="")

            assert result.success is False
            assert "empty" in result.error.lower()
            assert result.metadata.get("error_type") == "validation"

    @pytest.mark.asyncio
    async def test_execute_runtime_error(self):
        """Test TTS execution with runtime error."""
        with patch("src.services.tools.text_to_speech.generate_speech", new_callable=AsyncMock) as mock_generate:
            mock_generate.side_effect = RuntimeError("TTS generation failed")

            tool = TextToSpeechTool()
            result = await tool.execute(text="Hello world")

            assert result.success is False
            assert "failed" in result.error.lower()
            assert result.metadata.get("error_type") == "generation"

    @pytest.mark.asyncio
    async def test_execute_with_all_options(self):
        """Test TTS execution with all options."""
        with patch("src.services.tools.text_to_speech.generate_speech", new_callable=AsyncMock) as mock_generate:
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
            mock_generate.assert_called_once_with(
                text="Bonjour le monde",
                model="chatterbox-multilingual",
                voice_reference_url="https://example.com/voice.wav",
                language="fr",
                exaggeration=1.5,
                cfg_weight=0.7,
            )


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
            await generate_speech(
                "Hello",
                model="chatterbox-multilingual",
                language="invalid_lang"
            )


# =============================================================================
# API ROUTE TESTS
# =============================================================================


class TestToolsRoute:
    """Tests for tools API routes."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from fastapi.testclient import TestClient
        from src.routes.tools import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
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
        """Test executing a tool successfully."""
        with patch.object(TextToSpeechTool, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = ToolResult(
                success=True,
                result={"audio_base64": "test_audio"},
                metadata={}
            )

            response = client.post("/tools/execute", json={
                "name": "text_to_speech",
                "parameters": {"text": "Hello world"}
            })

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["result"]["audio_base64"] == "test_audio"

    def test_execute_tool_not_found(self, client):
        """Test executing a non-existent tool."""
        response = client.post("/tools/execute", json={
            "name": "nonexistent",
            "parameters": {}
        })

        assert response.status_code == 404

    def test_execute_tool_validation_error(self, client):
        """Test executing a tool with validation error."""
        with patch.object(TextToSpeechTool, "execute", new_callable=AsyncMock) as mock_execute:
            mock_execute.side_effect = ValueError("Invalid parameter")

            response = client.post("/tools/execute", json={
                "name": "text_to_speech",
                "parameters": {"text": ""}
            })

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
