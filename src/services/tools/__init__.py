"""
Server-side tools for Gatewayz chat system.

This module provides tools that can be executed server-side in response to
AI model tool calls. Tools follow the OpenAI function calling format.
"""

from src.services.tools.base import BaseTool, ToolDefinition, ToolResult
from src.services.tools.text_to_speech import TextToSpeechTool
from src.services.tools.web_search import WebSearchTool

# Registry of available tools
AVAILABLE_TOOLS: dict[str, type[BaseTool]] = {
    "text_to_speech": TextToSpeechTool,
    "web_search": WebSearchTool,
}


def get_tool_definitions() -> list[ToolDefinition]:
    """Get all tool definitions for use in chat completion requests.

    Returns:
        List of tool definitions in OpenAI format
    """
    return [tool_class.get_definition() for tool_class in AVAILABLE_TOOLS.values()]


def get_tool_by_name(name: str) -> type[BaseTool] | None:
    """Get a tool class by name.

    Args:
        name: The tool name

    Returns:
        Tool class or None if not found
    """
    return AVAILABLE_TOOLS.get(name)


async def execute_tool(tool_name: str, parameters: dict | None = None) -> ToolResult:
    """Execute a tool by name with given parameters.

    Args:
        tool_name: The tool name
        parameters: Tool parameters as a dictionary

    Returns:
        ToolResult with execution result

    Raises:
        ValueError: If tool not found
    """
    tool_class = get_tool_by_name(tool_name)
    if not tool_class:
        raise ValueError(f"Tool '{tool_name}' not found")

    tool = tool_class()
    return await tool.execute(**(parameters or {}))


__all__ = [
    "BaseTool",
    "ToolDefinition",
    "ToolResult",
    "TextToSpeechTool",
    "WebSearchTool",
    "AVAILABLE_TOOLS",
    "get_tool_definitions",
    "get_tool_by_name",
    "execute_tool",
]
