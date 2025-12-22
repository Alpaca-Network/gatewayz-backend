"""
Tool Executor Service

Handles server-side execution of AI tool calls. When a model responds with
tool_calls, this service executes the appropriate tools and returns results
that can be sent back to the model.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.config import Config
from src.services.tavily_client import (
    TavilySearchError,
    TavilySearchResponse,
    format_search_results_for_llm,
    search_web,
)
from src.services.tool_definitions import is_server_side_tool

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionResult:
    """Result of executing a tool."""

    tool_call_id: str
    name: str
    result: Any
    success: bool = True
    error: str | None = None

    def to_tool_message(self) -> dict[str, Any]:
        """
        Convert to OpenAI tool message format for sending back to the model.

        Returns:
            Dict in the format expected by chat completions API
        """
        if self.success:
            # For successful results, format as JSON string
            if isinstance(self.result, dict):
                content = json.dumps(self.result)
            elif isinstance(self.result, str):
                content = self.result
            else:
                content = str(self.result)
        else:
            content = f"Error: {self.error}"

        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": content,
        }

    def to_sse_event(self) -> dict[str, Any]:
        """
        Convert to SSE event format for streaming to the frontend.

        Returns:
            Dict suitable for JSON serialization and SSE streaming
        """
        return {
            "type": "tool_result",
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "success": self.success,
            "result": self.result if self.success else None,
            "error": self.error,
        }


async def execute_tool_call(
    tool_call_id: str,
    name: str,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    """
    Execute a single tool call and return the result.

    This function dispatches to the appropriate tool handler based on
    the tool name and returns a structured result.

    Args:
        tool_call_id: Unique ID from the model's tool call
        name: Tool name (e.g., "web_search")
        arguments: Tool arguments as parsed from the model's JSON

    Returns:
        ToolExecutionResult with execution outcome
    """
    logger.info(
        "Executing tool: name=%s, tool_call_id=%s, args=%s",
        name,
        tool_call_id,
        str(arguments)[:200],
    )

    try:
        if name == "web_search":
            return await _execute_web_search(tool_call_id, arguments)
        else:
            logger.warning("Unknown tool requested: %s", name)
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                name=name,
                result=None,
                success=False,
                error=f"Unknown tool: {name}",
            )

    except Exception as e:
        logger.exception("Tool execution failed: name=%s, error=%s", name, str(e))
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            name=name,
            result=None,
            success=False,
            error=str(e),
        )


async def _execute_web_search(
    tool_call_id: str,
    arguments: dict[str, Any],
) -> ToolExecutionResult:
    """
    Execute a web search using Tavily.

    Args:
        tool_call_id: The tool call ID
        arguments: Should contain "query" key

    Returns:
        ToolExecutionResult with search results
    """
    if not Config.WEB_SEARCH_ENABLED:
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            name="web_search",
            result=None,
            success=False,
            error="Web search is not enabled on this server",
        )

    query = arguments.get("query", "").strip()
    if not query:
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            name="web_search",
            result=None,
            success=False,
            error="Search query is required",
        )

    try:
        search_response: TavilySearchResponse = await search_web(
            query=query,
            max_results=Config.TAVILY_MAX_RESULTS,
            search_depth=Config.TAVILY_SEARCH_DEPTH,
            include_answer=Config.TAVILY_INCLUDE_ANSWER,
        )

        # Format results for the LLM
        formatted_results = format_search_results_for_llm(search_response)

        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            name="web_search",
            result={
                "query": search_response["query"],
                "results": search_response["results"],
                "answer": search_response.get("answer"),
                "formatted": formatted_results,
            },
            success=True,
        )

    except TavilySearchError as e:
        logger.error("Tavily search failed: %s", str(e))
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            name="web_search",
            result=None,
            success=False,
            error=f"Search failed: {e}",
        )
    except ValueError as e:
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            name="web_search",
            result=None,
            success=False,
            error=str(e),
        )


async def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
) -> list[ToolExecutionResult]:
    """
    Execute multiple tool calls.

    Args:
        tool_calls: List of tool calls from the model response

    Returns:
        List of ToolExecutionResult objects
    """
    results = []

    for tool_call in tool_calls:
        tool_call_id = tool_call.get("id", "")
        function_data = tool_call.get("function", {})
        name = function_data.get("name", "")

        # Parse arguments from JSON string
        arguments_str = function_data.get("arguments", "{}")
        try:
            arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
        except json.JSONDecodeError as e:
            logger.error("Failed to parse tool arguments: %s", str(e))
            results.append(
                ToolExecutionResult(
                    tool_call_id=tool_call_id,
                    name=name,
                    result=None,
                    success=False,
                    error=f"Invalid tool arguments: {e}",
                )
            )
            continue

        # Only execute server-side tools
        if is_server_side_tool(name):
            result = await execute_tool_call(tool_call_id, name, arguments)
            results.append(result)

    return results


def build_tool_messages(results: list[ToolExecutionResult]) -> list[dict[str, Any]]:
    """
    Convert tool execution results to messages for the model.

    Args:
        results: List of ToolExecutionResult objects

    Returns:
        List of tool messages in OpenAI format
    """
    return [r.to_tool_message() for r in results]


def create_tool_call_sse_event(
    tool_call_id: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """
    Create an SSE event to notify the frontend that a tool is being called.

    Args:
        tool_call_id: The tool call ID
        name: Tool name
        arguments: Tool arguments

    Returns:
        Dict for SSE streaming
    """
    return {
        "type": "tool_call",
        "tool_call_id": tool_call_id,
        "name": name,
        "arguments": arguments,
    }
