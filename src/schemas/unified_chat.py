"""
Unified chat request/response schemas.
Accepts all API formats and normalizes to internal representation.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from src.services.format_detection import ChatFormat


class UnifiedChatRequest(BaseModel):
    """
    Universal chat request schema that accepts ALL formats.

    This schema accepts requests in:
    - OpenAI format (messages)
    - Anthropic format (system + messages)
    - Responses API format (input)

    The format is auto-detected unless explicitly specified.
    """

    # Format specification (auto-detected if not provided)
    format: Literal["openai", "anthropic", "responses", "auto"] | None = "auto"

    # Core required fields
    model: str = Field(..., description="Model ID to use")

    # Message fields (at least one required)
    messages: list[dict[str, Any]] | None = Field(
        None,
        description="Messages array (OpenAI/Anthropic format)"
    )
    input: list[dict[str, Any]] | None = Field(
        None,
        description="Input array (Responses API format)"
    )

    # Common optional parameters
    max_tokens: int | None = Field(None, ge=1, le=128000)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    frequency_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    presence_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    stream: bool = False

    # Anthropic-specific fields
    system: str | None = Field(
        None,
        description="System prompt (Anthropic format)"
    )
    stop_sequences: list[str] | None = Field(
        None,
        description="Stop sequences (Anthropic)"
    )

    # Responses API-specific fields
    response_format: dict[str, Any] | None = Field(
        None,
        description="Response format specification (Responses API)"
    )

    # Provider selection
    provider: str | None = Field(
        None,
        description="Specific provider to use (optional)"
    )

    # Function calling
    tools: list[dict[str, Any]] | None = Field(
        None,
        description="Function calling tools"
    )
    tool_choice: str | dict[str, Any] | None = None

    # Extra fields allowed for provider-specific parameters
    class Config:
        extra = "allow"

    @model_validator(mode="after")
    def validate_messages_or_input(self) -> "UnifiedChatRequest":
        """Validate that at least one of messages or input is provided"""
        if not self.messages and not self.input:
            raise ValueError(
                "Either 'messages' or 'input' must be provided"
            )
        return self

    def get_normalized_messages(self) -> list[dict[str, Any]]:
        """
        Convert to normalized message format (OpenAI style).

        All formats are converted to OpenAI-style messages internally:
        [{"role": "system"|"user"|"assistant", "content": "..."}]

        Returns:
            List of normalized message dicts

        Raises:
            ValueError: If no messages/input provided
        """

        # Responses API format: input → messages
        if self.input:
            return self.input.copy()

        # OpenAI/Anthropic format
        if self.messages:
            messages = self.messages.copy()

            # If Anthropic format with system prompt, prepend it
            if self.system:
                # Check if system message already exists
                has_system = any(m.get("role") == "system" for m in messages)

                if not has_system:
                    messages.insert(0, {
                        "role": "system",
                        "content": self.system
                    })

            return messages

        raise ValueError("No messages provided")

    def get_optional_params(self) -> dict[str, Any]:
        """
        Extract optional parameters for provider request.

        Returns:
            Dict of non-None optional parameters
        """
        params = {}

        # Standard parameters
        if self.max_tokens is not None:
            params["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.top_p is not None:
            params["top_p"] = self.top_p
        if self.frequency_penalty is not None:
            params["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty is not None:
            params["presence_penalty"] = self.presence_penalty

        # Function calling
        if self.tools is not None:
            params["tools"] = self.tools
        if self.tool_choice is not None:
            params["tool_choice"] = self.tool_choice

        # Anthropic-specific
        if self.stop_sequences is not None:
            params["stop"] = self.stop_sequences

        # Responses API-specific
        if self.response_format is not None:
            params["response_format"] = self.response_format

        return params


class UnifiedChatResponse(BaseModel):
    """
    Internal unified response format.
    Gets converted to specific API format before returning to client.
    """

    id: str
    created: int
    model: str
    content: str
    finish_reason: str | None = "stop"

    # Usage information
    usage: dict[str, int] | None = None

    # Gateway metadata
    gateway_usage: dict[str, Any] | None = None

    # Function calling
    tool_calls: list[dict[str, Any]] | None = None
