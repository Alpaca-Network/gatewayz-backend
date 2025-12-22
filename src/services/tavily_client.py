"""
Tavily Web Search Client

Provides web search capabilities for AI agents using the Tavily API.
Tavily is designed specifically for AI applications and returns structured,
relevant search results optimized for LLM consumption.

API Documentation: https://docs.tavily.com/
"""

import logging
from typing import Any, TypedDict

import httpx

from src.config import Config

logger = logging.getLogger(__name__)


class SearchResult(TypedDict):
    """Individual search result from Tavily."""

    title: str
    url: str
    content: str
    score: float


class TavilySearchResponse(TypedDict):
    """Response from Tavily search API."""

    query: str
    results: list[SearchResult]
    answer: str | None  # AI-generated answer summarizing the results


class TavilySearchError(Exception):
    """Exception raised when Tavily search fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def search_web(
    query: str,
    max_results: int | None = None,
    search_depth: str | None = None,
    include_answer: bool | None = None,
) -> TavilySearchResponse:
    """
    Execute a web search using Tavily API.

    Args:
        query: The search query string
        max_results: Maximum number of results to return (1-10, default from config)
        search_depth: "basic" for faster results or "advanced" for more comprehensive
        include_answer: Whether to include Tavily's AI-generated answer summary

    Returns:
        TavilySearchResponse containing query, results list, and optional answer

    Raises:
        TavilySearchError: When the API call fails or is not configured
        ValueError: When TAVILY_API_KEY is not configured
    """
    if not Config.TAVILY_API_KEY:
        raise ValueError("TAVILY_API_KEY not configured. Please set the environment variable.")

    if not query or not query.strip():
        raise ValueError("Search query cannot be empty")

    # Use config defaults if not specified
    max_results = max_results or Config.TAVILY_MAX_RESULTS
    search_depth = search_depth or Config.TAVILY_SEARCH_DEPTH
    include_answer = include_answer if include_answer is not None else Config.TAVILY_INCLUDE_ANSWER

    # Clamp max_results to valid range
    max_results = max(1, min(max_results, 10))

    logger.info(
        "Executing Tavily search: query=%s, max_results=%d, depth=%s",
        query[:100],
        max_results,
        search_depth,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": Config.TAVILY_API_KEY,
                    "query": query.strip(),
                    "max_results": max_results,
                    "search_depth": search_depth,
                    "include_answer": include_answer,
                    "include_raw_content": False,  # We don't need raw HTML
                    "include_images": False,  # Skip images for now
                },
                timeout=30.0,
            )

            if response.status_code == 401:
                raise TavilySearchError("Invalid Tavily API key", status_code=401)
            elif response.status_code == 429:
                raise TavilySearchError("Tavily rate limit exceeded", status_code=429)
            elif response.status_code >= 400:
                error_detail = response.text[:200] if response.text else "Unknown error"
                raise TavilySearchError(
                    f"Tavily API error: {error_detail}",
                    status_code=response.status_code,
                )

            data = response.json()

            # Parse and structure the response
            results: list[SearchResult] = []
            for r in data.get("results", []):
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        content=r.get("content", ""),
                        score=float(r.get("score", 0.0)),
                    )
                )

            search_response = TavilySearchResponse(
                query=query,
                results=results,
                answer=data.get("answer"),
            )

            logger.info(
                "Tavily search completed: query=%s, results_count=%d, has_answer=%s",
                query[:50],
                len(results),
                search_response["answer"] is not None,
            )

            return search_response

    except httpx.TimeoutException as e:
        logger.error("Tavily search timeout: query=%s, error=%s", query[:50], str(e))
        raise TavilySearchError("Search request timed out") from e
    except httpx.RequestError as e:
        logger.error("Tavily search request error: query=%s, error=%s", query[:50], str(e))
        raise TavilySearchError(f"Search request failed: {e}") from e


def format_search_results_for_llm(response: TavilySearchResponse) -> str:
    """
    Format search results as a text string suitable for LLM consumption.

    This creates a markdown-formatted summary that can be included in the
    conversation context as a tool result.

    Args:
        response: The TavilySearchResponse from search_web()

    Returns:
        Formatted string with search results
    """
    parts = [f"## Web Search Results for: {response['query']}\n"]

    if response.get("answer"):
        parts.append(f"**Summary:** {response['answer']}\n")

    parts.append("### Sources:\n")

    for i, result in enumerate(response["results"], 1):
        parts.append(f"{i}. **[{result['title']}]({result['url']})**")
        if result["content"]:
            # Truncate long content
            content = result["content"][:500]
            if len(result["content"]) > 500:
                content += "..."
            parts.append(f"   {content}\n")

    return "\n".join(parts)


def is_web_search_available() -> bool:
    """Check if web search is configured and enabled."""
    return bool(Config.TAVILY_API_KEY) and Config.WEB_SEARCH_ENABLED
