"""
Internal unified chat schemas.

These schemas represent the INTERNAL format used by the ChatInferenceHandler.
All external formats (OpenAI, Anthropic, AI SDK) are converted TO these schemas,
processed by the handler, then converted back FROM these schemas.

This provides a single source of truth for what a "chat request" is internally.
"""

from typing import Optional, Literal, Union, List, Any, Dict
from pydantic import BaseModel, Field


class InternalMessage(BaseModel):
    """
    Unified message format (internal).

    Represents a single message in a conversation, normalized across all formats.
    Supports text content, multimodal content, tool calls, and system messages.
    """

    role: Literal["system", "user", "assistant", "tool"] = Field(
        ..., description="Role of the message sender"
    )
    content: Union[str, List[Dict[str, Any]], None] = Field(
        ..., description="Message content - can be text, multimodal array, or None for tool responses"
    )
    name: Optional[str] = Field(None, description="Optional name of the sender (for function calls)")
    tool_call_id: Optional[str] = Field(None, description="ID of the tool call this message is responding to")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(
        None, description="Tool/function calls made by the assistant"
    )


class InternalChatRequest(BaseModel):
    """
    Unified chat request format (internal).

    This is the normalized format that the ChatInferenceHandler expects.
    All adapters convert their external formats to this schema.
    """

    # Core required fields
    messages: List[InternalMessage] = Field(..., description="Conversation messages")
    model: str = Field(..., description="Model identifier (original user-requested model)")

    # Generation parameters (unified across all formats)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: Optional[int] = Field(None, ge=1, description="Maximum tokens to generate")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="Nucleus sampling threshold")
    frequency_penalty: Optional[float] = Field(None, ge=-2.0, le=2.0, description="Frequency penalty")
    presence_penalty: Optional[float] = Field(None, ge=-2.0, le=2.0, description="Presence penalty")
    stop: Optional[Union[str, List[str]]] = Field(None, description="Stop sequences")

    # Streaming
    stream: bool = Field(False, description="Whether to stream the response")

    # Tools/function calling
    tools: Optional[List[Dict[str, Any]]] = Field(None, description="Available tools/functions")
    tool_choice: Optional[Union[str, Dict[str, Any]]] = Field(
        None, description="Tool choice strategy ('auto', 'none', or specific tool)"
    )

    # Response format
    response_format: Optional[Dict[str, Any]] = Field(None, description="Desired response format (e.g., JSON)")

    # Metadata
    user: Optional[str] = Field(None, description="End-user identifier for abuse monitoring")

    # Anthropic-specific (mapped from system parameter)
    system_message: Optional[str] = Field(
        None, description="System message (Anthropic formats use separate field)"
    )

    class Config:
        extra = "allow"  # Allow extra fields for provider-specific parameters


class InternalUsage(BaseModel):
    """
    Unified token usage tracking.

    Standard across all providers and formats.
    """

    prompt_tokens: int = Field(..., ge=0, description="Number of tokens in the prompt")
    completion_tokens: int = Field(..., ge=0, description="Number of tokens in the completion")
    total_tokens: int = Field(..., ge=0, description="Total tokens used (prompt + completion)")

    class Config:
        extra = "allow"  # Allow provider-specific usage fields (e.g., cache tokens)


class InternalChatResponse(BaseModel):
    """
    Unified chat response format (internal).

    This is the normalized response from the ChatInferenceHandler.
    Adapters convert this to external formats (OpenAI, Anthropic, etc.).
    """

    # Core response fields
    id: str = Field(..., description="Unique request identifier")
    model: str = Field(..., description="Model used for generation")
    content: str = Field(..., description="Main response text content")
    usage: InternalUsage = Field(..., description="Token usage statistics")

    # Completion metadata
    finish_reason: Optional[str] = Field(None, description="Reason completion finished (stop, length, etc.)")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="Tool calls made by the model")

    # Provider metadata
    provider_used: str = Field(..., description="Provider that handled the request (openrouter, cerebras, etc.)")
    provider_response_id: Optional[str] = Field(None, description="Original response ID from provider")

    # Cost tracking (calculated by handler)
    cost_usd: float = Field(..., ge=0.0, description="Total cost in USD")
    input_cost_usd: float = Field(..., ge=0.0, description="Cost of input tokens in USD")
    output_cost_usd: float = Field(..., ge=0.0, description="Cost of output tokens in USD")

    # Timing
    processing_time_ms: Optional[float] = Field(None, description="Processing time in milliseconds")

    class Config:
        extra = "allow"  # Allow provider-specific response fields


class InternalStreamChunk(BaseModel):
    """
    Unified streaming chunk format (internal).

    Used during streaming responses. Adapters convert these to external streaming formats.
    """

    id: str = Field(..., description="Request identifier")
    model: str = Field(..., description="Model identifier")
    content: Optional[str] = Field(None, description="Incremental content delta")
    role: Optional[str] = Field(None, description="Role (for first chunk)")
    finish_reason: Optional[str] = Field(None, description="Finish reason (for last chunk)")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="Incremental tool call data")
    usage: Optional[InternalUsage] = Field(None, description="Usage data (typically in final chunk)")
    created: int = Field(..., description="Unix timestamp")

    class Config:
        extra = "allow"
