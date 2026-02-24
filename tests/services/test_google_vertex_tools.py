"""Tests for Google Vertex client tools extraction and translation"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock vertexai modules before importing our module (needed for lazy imports)
sys.modules["vertexai"] = MagicMock()
sys.modules["vertexai.generative_models"] = MagicMock()
sys.modules["google.protobuf"] = MagicMock()
sys.modules["google.protobuf.json_format"] = MagicMock()

from src.config import Config
from src.services.google_vertex_client import (
    _translate_openai_tools_to_vertex,
    _translate_tool_choice_to_vertex,
    make_google_vertex_request_openai,
)


@pytest.fixture(autouse=True)
def force_sdk_transport(monkeypatch):
    """Ensure tests exercise the SDK code path."""
    monkeypatch.setattr(Config, "GOOGLE_VERTEX_TRANSPORT", "sdk")
    yield


class TestOpenAIToVertexToolsTranslation:
    """Test OpenAI tools format to Vertex AI functionDeclarations translation"""

    def test_translate_single_tool(self):
        """Test translating a single OpenAI tool to Vertex format"""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather in a given location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {
                                "type": "string",
                                "description": "The city and state, e.g. San Francisco, CA",
                            },
                            "unit": {
                                "type": "string",
                                "enum": ["celsius", "fahrenheit"],
                            },
                        },
                        "required": ["location"],
                    },
                },
            }
        ]

        result = _translate_openai_tools_to_vertex(openai_tools)

        assert len(result) == 1
        assert "functionDeclarations" in result[0]
        declarations = result[0]["functionDeclarations"]
        assert len(declarations) == 1
        assert declarations[0]["name"] == "get_weather"
        assert declarations[0]["description"] == "Get the current weather in a given location"
        assert declarations[0]["parameters"]["type"] == "object"
        assert "location" in declarations[0]["parameters"]["properties"]
        assert declarations[0]["parameters"]["required"] == ["location"]

    def test_translate_multiple_tools(self):
        """Test translating multiple OpenAI tools to Vertex format"""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the web",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
        ]

        result = _translate_openai_tools_to_vertex(openai_tools)

        assert len(result) == 1
        declarations = result[0]["functionDeclarations"]
        assert len(declarations) == 2
        assert declarations[0]["name"] == "get_weather"
        assert declarations[1]["name"] == "search_web"

    def test_translate_tool_without_description(self):
        """Test translating a tool without description"""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "simple_function",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        result = _translate_openai_tools_to_vertex(openai_tools)

        declarations = result[0]["functionDeclarations"]
        assert declarations[0]["name"] == "simple_function"
        assert "description" not in declarations[0]

    def test_translate_tool_without_parameters(self):
        """Test translating a tool without parameters"""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "no_params_function",
                    "description": "A function with no parameters",
                },
            }
        ]

        result = _translate_openai_tools_to_vertex(openai_tools)

        declarations = result[0]["functionDeclarations"]
        assert declarations[0]["name"] == "no_params_function"
        assert "parameters" not in declarations[0]

    def test_translate_empty_tools_list(self):
        """Test translating an empty tools list"""
        result = _translate_openai_tools_to_vertex([])
        assert result == []

    def test_translate_none_tools(self):
        """Test translating None tools"""
        result = _translate_openai_tools_to_vertex(None)
        assert result == []

    def test_skip_non_function_tools(self):
        """Test that non-function tools are skipped with warning"""
        openai_tools = [
            {
                "type": "code_interpreter",  # Not a function type
                "function": {"name": "should_skip"},
            },
            {
                "type": "function",
                "function": {
                    "name": "valid_function",
                    "description": "Valid",
                },
            },
        ]

        result = _translate_openai_tools_to_vertex(openai_tools)

        declarations = result[0]["functionDeclarations"]
        assert len(declarations) == 1
        assert declarations[0]["name"] == "valid_function"

    def test_skip_tool_without_name(self):
        """Test that tools without name are skipped"""
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "description": "No name function",
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "valid_function",
                },
            },
        ]

        result = _translate_openai_tools_to_vertex(openai_tools)

        declarations = result[0]["functionDeclarations"]
        assert len(declarations) == 1
        assert declarations[0]["name"] == "valid_function"

    def test_skip_tool_without_function_definition(self):
        """Test that tools without function definition are skipped"""
        openai_tools = [
            {"type": "function"},  # Missing function key
            {
                "type": "function",
                "function": {"name": "valid_function"},
            },
        ]

        result = _translate_openai_tools_to_vertex(openai_tools)

        declarations = result[0]["functionDeclarations"]
        assert len(declarations) == 1
        assert declarations[0]["name"] == "valid_function"


class TestToolChoiceTranslation:
    """Test OpenAI tool_choice to Vertex AI toolConfig translation"""

    def test_translate_tool_choice_none(self):
        """Test translating tool_choice='none'"""
        result = _translate_tool_choice_to_vertex("none")

        assert result == {"functionCallingConfig": {"mode": "NONE"}}

    def test_translate_tool_choice_auto(self):
        """Test translating tool_choice='auto'"""
        result = _translate_tool_choice_to_vertex("auto")

        assert result == {"functionCallingConfig": {"mode": "AUTO"}}

    def test_translate_tool_choice_required(self):
        """Test translating tool_choice='required'"""
        result = _translate_tool_choice_to_vertex("required")

        assert result == {"functionCallingConfig": {"mode": "ANY"}}

    def test_translate_tool_choice_specific_function(self):
        """Test translating tool_choice with specific function"""
        tool_choice = {
            "type": "function",
            "function": {"name": "get_weather"},
        }

        result = _translate_tool_choice_to_vertex(tool_choice)

        assert result == {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["get_weather"],
            }
        }

    def test_translate_tool_choice_null(self):
        """Test translating tool_choice=None"""
        result = _translate_tool_choice_to_vertex(None)

        assert result is None

    def test_translate_unknown_tool_choice_string(self):
        """Test translating unknown tool_choice string defaults to AUTO"""
        result = _translate_tool_choice_to_vertex("unknown_value")

        assert result == {"functionCallingConfig": {"mode": "AUTO"}}

    def test_translate_tool_choice_function_missing_name(self):
        """Test translating tool_choice function without name"""
        tool_choice = {
            "type": "function",
            "function": {},  # Missing name
        }

        result = _translate_tool_choice_to_vertex(tool_choice)

        assert result == {"functionCallingConfig": {"mode": "ANY"}}

    def test_translate_tool_choice_function_is_none(self):
        """Test translating tool_choice when function value is None"""
        tool_choice = {
            "type": "function",
            "function": None,  # function is explicitly None
        }

        result = _translate_tool_choice_to_vertex(tool_choice)

        assert result == {"functionCallingConfig": {"mode": "ANY"}}


class TestGoogleVertexToolsSupport:
    """Test that Google Vertex client extracts tools parameter"""

    @patch("src.services.google_vertex_client.initialize_vertex_ai")
    @patch("src.services.google_vertex_client._ensure_vertex_imports")
    def test_tools_extracted_from_kwargs(self, mock_ensure_imports, mock_init_vertex):
        """Test that tools are extracted from kwargs"""
        # Mock the lazy import to return a mock GenerativeModel class
        mock_generative_model_class = MagicMock()
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "test"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 5
        mock_response.usage_metadata.candidates_token_count = 10
        mock_response.candidates = [MagicMock()]
        mock_response.candidates[0].finish_reason = 1  # STOP

        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model_class.return_value = mock_model_instance

        # Mock _ensure_vertex_imports to return our mocked GenerativeModel class
        mock_ensure_imports.return_value = (MagicMock(), mock_generative_model_class)

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        # Check that tools were detected (warning should be logged)
        with patch("src.services.google_vertex_client.logger") as mock_logger:
            make_google_vertex_request_openai(
                messages=[{"role": "user", "content": "test"}],
                model="gemini-2.0-flash",
                tools=tools,
            )

            # Check that warning or info was logged about tools
            all_calls = []
            all_calls.extend(mock_logger.warning.call_args_list)
            all_calls.extend(mock_logger.info.call_args_list)

            tools_logged = any("tools" in str(call).lower() for call in all_calls if call)
            assert tools_logged, "Should log about tools parameter"

    @patch("src.services.google_vertex_client.initialize_vertex_ai")
    @patch("src.services.google_vertex_client._ensure_vertex_imports")
    def test_tools_request_completes_successfully(self, mock_ensure_imports, mock_init_vertex):
        """Test that requests with tools complete successfully"""
        # Mock the lazy import to return a mock GenerativeModel class
        mock_generative_model_class = MagicMock()
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "test"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 5
        mock_response.usage_metadata.candidates_token_count = 10
        mock_response.candidates = [MagicMock()]
        mock_response.candidates[0].finish_reason = 1  # STOP

        mock_model_instance.generate_content.return_value = mock_response
        mock_generative_model_class.return_value = mock_model_instance

        # Mock _ensure_vertex_imports to return our mocked GenerativeModel class
        mock_ensure_imports.return_value = (MagicMock(), mock_generative_model_class)

        tools = [{"type": "function", "function": {"name": "test"}}]

        result = make_google_vertex_request_openai(
            messages=[{"role": "user", "content": "test"}],
            model="gemini-2.0-flash",
            tools=tools,
        )

        # Check that generate_content was called
        assert mock_model_instance.generate_content.called

        # Check that result has expected structure
        assert "choices" in result
        assert "usage" in result
