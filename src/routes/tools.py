"""
Tools API routes for server-side tool execution.

This module provides endpoints for:
- Listing available tools
- Getting tool definitions for chat completion requests
- Direct tool execution (requires authentication)
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.security.deps import get_api_key
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
        logger.info(f"Executing tool '{request.name}' with params: {list(request.parameters.keys())}")
        result = await execute_tool(request.name, **request.parameters)

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
