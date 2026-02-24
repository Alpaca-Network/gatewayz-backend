"""
Web Search Tool using Tavily API.

This tool performs web searches to retrieve current information from the internet.
It uses Tavily's search API which provides AI-optimized search results with
optional answer generation.

Features:
- Real-time web search for current information
- AI-generated answer summaries
- Configurable search depth (basic/advanced)
- Domain filtering (include/exclude)
- Relevance scoring for results
"""

import logging
from typing import Any

import httpx

from src.config.config import Config
from src.services.tools.base import BaseTool, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# Tavily API Configuration
TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT = 30.0  # 30 second timeout for search requests


class WebSearchTool(BaseTool):
    """Tool for searching the web using Tavily API.

    This tool performs real-time web searches to retrieve current information.
    It's useful for queries about recent events, current data, live information,
    or anything that requires up-to-date knowledge beyond the model's training data.

    The tool returns search results with titles, URLs, content snippets, and
    relevance scores, plus an optional AI-generated answer summarizing the results.
    """

    @classmethod
    def get_definition(cls) -> ToolDefinition:
        """Get the OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web for current, real-time information. "
                    "Use this tool when you need up-to-date information about recent events, "
                    "current news, live data, prices, weather, sports scores, or any topic "
                    "that requires knowledge beyond your training data. "
                    "Returns search results with titles, URLs, and content snippets."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "The search query. Be specific and include relevant keywords "
                                "for better results. For example: 'latest AI news January 2025' "
                                "or 'current Bitcoin price USD'."
                            ),
                        },
                        "search_depth": {
                            "type": "string",
                            "enum": ["basic", "advanced"],
                            "default": "basic",
                            "description": (
                                "Search depth: 'basic' for quick searches (faster, fewer results), "
                                "'advanced' for comprehensive searches (slower, more thorough)."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                            "description": "Maximum number of search results to return (1-10).",
                        },
                        "include_answer": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Whether to include an AI-generated answer summarizing the search results."
                            ),
                        },
                        "include_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of domains to specifically include in the search "
                                "(e.g., ['wikipedia.org', 'reuters.com'])."
                            ),
                        },
                        "exclude_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "List of domains to exclude from the search "
                                "(e.g., ['pinterest.com', 'quora.com'])."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the web search.

        Args:
            **kwargs: Tool parameters:
                - query: Search query (required)
                - search_depth: 'basic' or 'advanced' (default: 'basic')
                - max_results: Number of results 1-10 (default: 5)
                - include_answer: Whether to include AI answer (default: True)
                - include_domains: List of domains to include
                - exclude_domains: List of domains to exclude

        Returns:
            ToolResult with search results or error
        """
        # Extract parameters with defaults
        query = kwargs.get("query")
        search_depth = kwargs.get("search_depth", "basic")
        max_results = kwargs.get("max_results", 5)
        include_answer = kwargs.get("include_answer", True)
        include_domains = kwargs.get("include_domains", [])
        exclude_domains = kwargs.get("exclude_domains", [])

        # Validate required parameters
        if not query:
            return self._error("query parameter is required", error_type="validation")

        if not isinstance(query, str) or len(query.strip()) == 0:
            return self._error("query must be a non-empty string", error_type="validation")

        # Validate max_results
        max_results = max(1, min(10, int(max_results)))

        # Check for API key
        api_key = getattr(Config, "TAVILY_API_KEY", None)
        if not api_key:
            return self._error(
                "Web search is not configured. Please set TAVILY_API_KEY.",
                error_type="configuration",
            )

        try:
            logger.info(
                f"Executing web search: query='{query[:50]}...', "
                f"depth={search_depth}, max_results={max_results}"
            )

            # Build request payload
            payload: dict[str, Any] = {
                "api_key": api_key,
                "query": query.strip(),
                "search_depth": search_depth,
                "max_results": max_results,
                "include_answer": include_answer,
                "include_raw_content": False,  # We only need snippets
                "include_images": False,  # Text search only
            }

            # Add domain filters if provided
            if include_domains:
                payload["include_domains"] = include_domains
            if exclude_domains:
                payload["exclude_domains"] = exclude_domains

            # Make the API request
            async with httpx.AsyncClient(timeout=TAVILY_TIMEOUT) as client:
                response = await client.post(TAVILY_API_URL, json=payload)

                if response.status_code == 401:
                    return self._error(
                        "Invalid Tavily API key",
                        error_type="authentication",
                    )

                if response.status_code == 429:
                    return self._error(
                        "Search rate limit exceeded. Please try again later.",
                        error_type="rate_limit",
                    )

                if response.status_code != 200:
                    logger.error(f"Tavily API error: {response.status_code} - {response.text}")
                    return self._error(
                        f"Search request failed with status {response.status_code}",
                        error_type="api_error",
                    )

                data = response.json()

            # Extract and format results
            results = []
            for item in data.get("results", []):
                results.append(
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": item.get("content", ""),
                        "score": item.get("score", 0.0),
                    }
                )

            # Build response
            result_data = {
                "query": query,
                "results": results,
                "results_count": len(results),
            }

            # Include AI-generated answer if available
            if include_answer and data.get("answer"):
                result_data["answer"] = data["answer"]

            logger.info(
                f"Web search completed: query='{query[:30]}...', "
                f"results={len(results)}, has_answer={bool(data.get('answer'))}"
            )

            return self._success(
                result=result_data,
                query=query,
                search_depth=search_depth,
                results_count=len(results),
            )

        except httpx.TimeoutException:
            logger.warning(f"Web search timeout for query: {query[:50]}...")
            return self._error(
                "Search request timed out. Please try again.",
                error_type="timeout",
            )

        except httpx.RequestError as e:
            logger.error(f"Web search request error: {e}")
            return self._error(
                "Failed to connect to search service",
                error_type="connection",
            )

        except Exception as e:
            logger.exception(f"Unexpected web search error: {e}")
            return self._error(
                "An unexpected error occurred during web search",
                error_type="unexpected",
            )
