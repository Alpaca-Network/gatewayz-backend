"""
Tools API routes for server-side tool execution.

This module provides endpoints for:
- Listing available tools
- Getting tool definitions for chat completion requests
- Direct tool execution (requires authentication)
- Search augmentation for non-tool models
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.security.deps import get_api_key, get_optional_api_key
from src.services.tools import (
    execute_tool,
    get_tool_by_name,
    get_tool_definitions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteRequest(BaseModel):
    """Request body for tool execution."""

    name: str
    parameters: dict[str, Any] = {}


class ToolExecuteResponse(BaseModel):
    """Response from tool execution."""

    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = {}


class SearchAugmentRequest(BaseModel):
    """Request body for search augmentation."""

    query: str = Field(..., min_length=1, description="The search query")
    max_results: int = Field(default=5, ge=1, le=10, description="Maximum results to return")
    include_answer: bool = Field(default=True, description="Include AI-generated summary")


class SearchAugmentResponse(BaseModel):
    """Response with formatted search context for prompt injection."""

    success: bool
    context: str | None = None  # Formatted text to prepend to user message
    error: str | None = None
    results_count: int = 0


@router.get("")
async def list_tools():
    """List all available tools with their definitions.

    This is a public endpoint for tool discovery.

    Returns:
        Dict with tools list and count
    """
    definitions = get_tool_definitions()
    return {
        "tools": definitions,
        "count": len(definitions),
    }


@router.get("/definitions")
async def get_definitions():
    """Get tool definitions formatted for chat completion requests.

    This endpoint returns tool definitions in the format expected by
    OpenAI-compatible chat completion APIs. Public for integration purposes.

    Returns:
        List of tool definitions
    """
    return get_tool_definitions()


@router.get("/{tool_name}")
async def get_tool_info(tool_name: str):
    """Get information about a specific tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Tool definition and metadata

    Raises:
        404: If tool not found
    """
    tool_class = get_tool_by_name(tool_name)
    if not tool_class:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")

    definition = tool_class.get_definition()
    return {
        "name": tool_name,
        "definition": definition,
        "available": True,
    }


@router.post("/execute")
async def execute_tool_endpoint(
    request: ToolExecuteRequest,
    api_key: str = Depends(get_api_key),
) -> ToolExecuteResponse:
    """Execute a tool with given parameters.

    This endpoint allows direct tool execution outside of the chat flow.
    Requires authentication via API key.

    Args:
        request: Tool name and parameters
        api_key: User's API key (injected via dependency)

    Returns:
        Tool execution result

    Raises:
        401: If not authenticated
        404: If tool not found
        500: If execution fails unexpectedly
    """
    tool_class = get_tool_by_name(request.name)
    if not tool_class:
        raise HTTPException(status_code=404, detail=f"Tool '{request.name}' not found")

    try:
        logger.info(
            f"Executing tool '{request.name}' with params: {list(request.parameters.keys())}"
        )
        result = await execute_tool(request.name, request.parameters)

        return ToolExecuteResponse(
            success=result.success,
            result=result.result,
            error=result.error,
            metadata=result.metadata,
        )

    except ValueError as e:
        logger.warning(f"Tool execution validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.exception(f"Tool execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {str(e)}")


@router.post("/search/augment")
async def search_augment(
    request: SearchAugmentRequest,
    api_key: str | None = Depends(get_optional_api_key),
) -> SearchAugmentResponse:
    """Perform web search and return formatted context for prompt augmentation.

    This endpoint is designed for models that don't support native tool calling.
    It executes a web search and returns formatted text that can be prepended
    to the user's message to provide real-time context.

    The formatted context includes:
    - An AI-generated answer summary (if available)
    - Individual search results with titles, snippets, and URLs

    Args:
        request: Search query and options
        api_key: Optional API key (allows both authenticated and guest usage)

    Returns:
        SearchAugmentResponse with formatted context string

    Example response context:
        [Web Search Results]
        Summary: Bitcoin is currently trading at $45,000...

        Sources:
        1. CoinDesk - Bitcoin Price Today
           Bitcoin rose 2% in early trading...
           https://coindesk.com/...
        ...
    """
    try:
        logger.info(f"Search augment request: query='{request.query[:50]}...'")

        # Execute web search using the existing tool
        result = await execute_tool(
            "web_search",
            {
                "query": request.query,
                "max_results": request.max_results,
                "include_answer": request.include_answer,
                "search_depth": "basic",
            },
        )

        if not result.success:
            logger.warning(f"Search augment failed: {result.error}")
            return SearchAugmentResponse(
                success=False,
                error=result.error or "Search failed",
                results_count=0,
            )

        # Format the results as context text
        search_data = result.result or {}
        results = search_data.get("results", [])
        answer = search_data.get("answer")

        if not results and not answer:
            return SearchAugmentResponse(
                success=True,
                context=None,
                error="No results found",
                results_count=0,
            )

        # Build formatted context
        context_parts = ["[Web Search Results]"]

        # Add AI summary if available
        if answer:
            context_parts.append(f"\nSummary: {answer}")

        # Add individual results
        if results:
            context_parts.append("\nSources:")
            for i, item in enumerate(results, 1):
                title = item.get("title", "Untitled")
                content = item.get("content", "")
                url = item.get("url", "")

                # Truncate content to avoid overwhelming the context
                if len(content) > 300:
                    content = content[:297] + "..."

                context_parts.append(f"\n{i}. {title}")
                if content:
                    context_parts.append(f"   {content}")
                if url:
                    context_parts.append(f"   {url}")

        context_parts.append("\n[End of Search Results]\n")

        context = "\n".join(context_parts)

        logger.info(
            f"Search augment success: {len(results)} results, context_length={len(context)}"
        )

        return SearchAugmentResponse(
            success=True,
            context=context,
            results_count=len(results),
        )

    except Exception as e:
        logger.exception(f"Search augment error: {e}")
        return SearchAugmentResponse(
            success=False,
            error="An unexpected error occurred during search",
            results_count=0,
        )
