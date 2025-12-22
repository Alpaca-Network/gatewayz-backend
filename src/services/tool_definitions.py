"""
Tool Definitions for AI Function Calling

Defines OpenAI-compatible tool schemas for server-side tool execution.
These tools are injected into chat requests when the user enables them.
"""

from typing import Any

from src.config import Config
from src.services.tavily_client import is_web_search_available

# Web Search Tool Definition (OpenAI function calling format)
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information. Use this tool when the user asks about "
            "recent events, news, current data, or information that may not be in your training data. "
            "Also use it when the user explicitly asks you to search or look something up online."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant information on the web",
                }
            },
            "required": ["query"],
        },
    },
}


def get_available_tools() -> list[dict[str, Any]]:
    """
    Get all available tools based on current configuration.

    Returns:
        List of tool definitions that are configured and enabled
    """
    tools = []

    if is_web_search_available():
        tools.append(WEB_SEARCH_TOOL)

    return tools


def get_enabled_tools(
    enable_web_search: bool = False,
    user_tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Get list of tools to include in a chat request based on user preferences.

    This merges server-side tools (like web_search) with any user-provided tools.
    Server-side tools are only included if:
    1. The user has opted in (enable_web_search=True)
    2. The tool is configured on the server (e.g., TAVILY_API_KEY is set)

    Args:
        enable_web_search: Whether the user wants web search capability
        user_tools: Additional tools provided by the user in the request

    Returns:
        Combined list of tool definitions
    """
    tools = list(user_tools) if user_tools else []

    # Add web search if enabled and available
    if enable_web_search and is_web_search_available():
        # Check if user already provided a web_search tool
        existing_names = {
            t.get("function", {}).get("name") for t in tools if t.get("type") == "function"
        }
        if "web_search" not in existing_names:
            tools.append(WEB_SEARCH_TOOL)

    return tools


def is_server_side_tool(tool_name: str) -> bool:
    """
    Check if a tool should be executed server-side.

    Server-side tools are executed by the backend before returning
    results to the model, rather than passed through to the client.

    Args:
        tool_name: Name of the tool to check

    Returns:
        True if the tool should be executed server-side
    """
    server_side_tools = {"web_search"}
    return tool_name in server_side_tools


def get_tool_by_name(tool_name: str) -> dict[str, Any] | None:
    """
    Get a tool definition by its name.

    Args:
        tool_name: The function name of the tool

    Returns:
        Tool definition dict or None if not found
    """
    tool_map = {
        "web_search": WEB_SEARCH_TOOL,
    }
    return tool_map.get(tool_name)
