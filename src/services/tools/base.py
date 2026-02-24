"""
Base classes and types for server-side tools.

This module provides the foundation for all server-side tools in the Gatewayz
chat system. Tools follow the OpenAI function calling format.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypedDict


class ToolFunctionParameters(TypedDict, total=False):
    """Type definition for tool function parameters."""

    type: str
    properties: dict[str, Any]
    required: list[str]


class ToolFunction(TypedDict):
    """Type definition for a tool function."""

    name: str
    description: str
    parameters: ToolFunctionParameters


class ToolDefinition(TypedDict):
    """Type definition for an OpenAI-compatible tool definition."""

    type: str
    function: ToolFunction


@dataclass
class ToolResult:
    """Result of a tool execution.

    Attributes:
        success: Whether the execution was successful
        result: The result data (for successful executions)
        error: Error message (for failed executions)
        metadata: Additional metadata about the execution
    """

    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseTool(ABC):
    """Abstract base class for all server-side tools.

    All tools must implement:
    - get_definition(): Returns the OpenAI-compatible tool definition
    - execute(): Executes the tool with given parameters

    Example:
        class MyTool(BaseTool):
            @classmethod
            def get_definition(cls) -> ToolDefinition:
                return {
                    "type": "function",
                    "function": {
                        "name": "my_tool",
                        "description": "Does something useful",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "param1": {"type": "string", "description": "A parameter"}
                            },
                            "required": ["param1"]
                        }
                    }
                }

            async def execute(self, param1: str) -> ToolResult:
                try:
                    result = do_something(param1)
                    return self._success(result=result)
                except Exception as e:
                    return self._error(str(e))
    """

    @classmethod
    @abstractmethod
    def get_definition(cls) -> ToolDefinition:
        """Get the OpenAI-compatible tool definition.

        Returns:
            ToolDefinition with type, function name, description, and parameters
        """
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            ToolResult with success/failure status and result data
        """
        pass

    def _success(self, result: dict[str, Any] | None = None, **metadata) -> ToolResult:
        """Create a successful ToolResult.

        Args:
            result: The result data
            **metadata: Additional metadata

        Returns:
            ToolResult with success=True
        """
        return ToolResult(success=True, result=result, metadata=metadata)

    def _error(self, error: str, **metadata) -> ToolResult:
        """Create a failed ToolResult.

        Args:
            error: Error message
            **metadata: Additional metadata

        Returns:
            ToolResult with success=False
        """
        return ToolResult(success=False, error=error, metadata=metadata)
