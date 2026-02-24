"""
Tests for the Web Search Tool.

Tests cover:
- Tool definition format
- Search execution with various parameters
- Error handling
- API key validation
- Rate limiting
- Timeout handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.tools import (
    AVAILABLE_TOOLS,
    execute_tool,
    get_tool_by_name,
)
from src.services.tools.base import ToolResult
from src.services.tools.web_search import WebSearchTool


class TestWebSearchToolDefinition:
    """Tests for WebSearchTool definition."""

    def test_tool_registered(self):
        """Test that web_search tool is registered."""
        assert "web_search" in AVAILABLE_TOOLS
        assert AVAILABLE_TOOLS["web_search"] == WebSearchTool

    def test_get_definition(self):
        """Test tool definition format."""
        definition = WebSearchTool.get_definition()

        assert definition["type"] == "function"
        assert definition["function"]["name"] == "web_search"
        assert "description" in definition["function"]
        assert "Search the web" in definition["function"]["description"]

        # Check parameters
        params = definition["function"]["parameters"]
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert "search_depth" in params["properties"]
        assert "max_results" in params["properties"]
        assert "include_answer" in params["properties"]
        assert "include_domains" in params["properties"]
        assert "exclude_domains" in params["properties"]
        assert "query" in params["required"]

    def test_search_depth_enum(self):
        """Test that search_depth has correct enum values."""
        definition = WebSearchTool.get_definition()
        search_depth = definition["function"]["parameters"]["properties"]["search_depth"]

        assert "enum" in search_depth
        assert "basic" in search_depth["enum"]
        assert "advanced" in search_depth["enum"]

    def test_max_results_bounds(self):
        """Test that max_results has correct bounds."""
        definition = WebSearchTool.get_definition()
        max_results = definition["function"]["parameters"]["properties"]["max_results"]

        assert max_results["minimum"] == 1
        assert max_results["maximum"] == 10
        assert max_results["default"] == 5


class TestWebSearchToolExecution:
    """Tests for WebSearchTool execution."""

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """Test successful search execution."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Test Result 1",
                    "url": "https://example.com/1",
                    "content": "This is test content 1",
                    "score": 0.95,
                },
                {
                    "title": "Test Result 2",
                    "url": "https://example.com/2",
                    "content": "This is test content 2",
                    "score": 0.85,
                },
            ],
            "answer": "This is the AI-generated answer.",
        }

        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()
                result = await tool.execute(query="test query")

                assert result.success is True
                assert result.result["query"] == "test query"
                assert len(result.result["results"]) == 2
                assert result.result["results"][0]["title"] == "Test Result 1"
                assert result.result["answer"] == "This is the AI-generated answer."

    @pytest.mark.asyncio
    async def test_execute_without_query(self):
        """Test execution without query parameter."""
        tool = WebSearchTool()
        result = await tool.execute()

        assert result.success is False
        assert "required" in result.error.lower()
        assert result.metadata.get("error_type") == "validation"

    @pytest.mark.asyncio
    async def test_execute_with_empty_query(self):
        """Test execution with empty query."""
        tool = WebSearchTool()
        result = await tool.execute(query="")

        assert result.success is False
        assert "empty" in result.error.lower() or "required" in result.error.lower()
        assert result.metadata.get("error_type") == "validation"

    @pytest.mark.asyncio
    async def test_execute_without_api_key(self):
        """Test execution without API key configured."""
        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = None

            tool = WebSearchTool()
            result = await tool.execute(query="test query")

            assert result.success is False
            assert "not configured" in result.error.lower()
            assert result.metadata.get("error_type") == "configuration"

    @pytest.mark.asyncio
    async def test_execute_with_invalid_api_key(self):
        """Test execution with invalid API key."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "invalid-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()
                result = await tool.execute(query="test query")

                assert result.success is False
                assert "invalid" in result.error.lower()
                assert result.metadata.get("error_type") == "authentication"

    @pytest.mark.asyncio
    async def test_execute_rate_limit(self):
        """Test execution with rate limit error."""
        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()
                result = await tool.execute(query="test query")

                assert result.success is False
                assert "rate limit" in result.error.lower()
                assert result.metadata.get("error_type") == "rate_limit"

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Test execution timeout handling."""
        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.side_effect = httpx.TimeoutException("Request timed out")
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()
                result = await tool.execute(query="test query")

                assert result.success is False
                assert "timed out" in result.error.lower()
                assert result.metadata.get("error_type") == "timeout"

    @pytest.mark.asyncio
    async def test_execute_connection_error(self):
        """Test execution connection error handling."""
        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.side_effect = httpx.RequestError("Connection failed")
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()
                result = await tool.execute(query="test query")

                assert result.success is False
                assert "connect" in result.error.lower() or "failed" in result.error.lower()
                assert result.metadata.get("error_type") == "connection"

    @pytest.mark.asyncio
    async def test_execute_with_all_options(self):
        """Test execution with all optional parameters."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Result",
                    "url": "https://example.com",
                    "content": "Content",
                    "score": 0.9,
                }
            ],
            "answer": "Answer",
        }

        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()
                result = await tool.execute(
                    query="test query",
                    search_depth="advanced",
                    max_results=10,
                    include_answer=True,
                    include_domains=["example.com"],
                    exclude_domains=["spam.com"],
                )

                assert result.success is True

                # Verify API was called with correct parameters
                call_args = mock_client_instance.post.call_args
                payload = call_args.kwargs["json"]
                assert payload["query"] == "test query"
                assert payload["search_depth"] == "advanced"
                assert payload["max_results"] == 10
                assert payload["include_answer"] is True
                assert payload["include_domains"] == ["example.com"]
                assert payload["exclude_domains"] == ["spam.com"]

    @pytest.mark.asyncio
    async def test_execute_max_results_clamped(self):
        """Test that max_results is clamped to valid range."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "answer": None}

        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()

                # Test with value > 10 (should be clamped to 10)
                await tool.execute(query="test", max_results=100)
                payload = mock_client_instance.post.call_args.kwargs["json"]
                assert payload["max_results"] == 10

                # Test with value < 1 (should be clamped to 1)
                await tool.execute(query="test", max_results=-5)
                payload = mock_client_instance.post.call_args.kwargs["json"]
                assert payload["max_results"] == 1

    @pytest.mark.asyncio
    async def test_execute_no_results(self):
        """Test execution with no search results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}

        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                tool = WebSearchTool()
                result = await tool.execute(query="very obscure query")

                assert result.success is True
                assert result.result["results"] == []
                assert result.result["results_count"] == 0


class TestWebSearchToolIntegration:
    """Integration tests for WebSearchTool with tool registry."""

    def test_get_tool_by_name(self):
        """Test getting WebSearchTool by name."""
        tool_class = get_tool_by_name("web_search")
        assert tool_class is not None
        assert tool_class == WebSearchTool

    @pytest.mark.asyncio
    async def test_execute_via_registry(self):
        """Test executing web_search via execute_tool function."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"title": "Test", "url": "https://test.com", "content": "Content", "score": 0.9}
            ],
            "answer": "Answer",
        }

        with patch("src.services.tools.web_search.Config") as mock_config:
            mock_config.TAVILY_API_KEY = "test-api-key"

            with patch("httpx.AsyncClient") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_client_instance

                result = await execute_tool("web_search", {"query": "test"})

                assert result.success is True
                assert result.result["results_count"] == 1

    def test_tool_definition_in_all_definitions(self):
        """Test that web_search is included in get_tool_definitions."""
        from src.services.tools import get_tool_definitions

        definitions = get_tool_definitions()
        tool_names = [d["function"]["name"] for d in definitions]

        assert "web_search" in tool_names
