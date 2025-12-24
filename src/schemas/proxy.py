from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, field_validator

ALLOWED_CHAT_ROLES = {"system", "user", "assistant"}


class Message(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, role: str) -> str:
        if role not in ALLOWED_CHAT_ROLES:
            raise ValueError(
                f"Invalid message role '{role}'. "
                f"Supported roles are: {', '.join(sorted(ALLOWED_CHAT_ROLES))}."
            )
        return role

    @field_validator("content")
    @classmethod
    def validate_content(cls, content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Message content must be a non-empty string.")
        return content


class ProxyRequest(BaseModel):
    model: str
    messages: list[Message]
    max_tokens: int | None = 4096
    temperature: float | None = 1.0
    top_p: float | None = 1.0
    frequency_penalty: float | None = 0.0
    presence_penalty: float | None = 0.0
    stream: bool | None = False
    tools: list[dict] | None = None  # Function calling tools
    provider: str | None = None  # Provider selection: "openrouter", etc

    class Config:
        extra = "allow"

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, messages: list[Message]) -> list[Message]:
        if not messages:
            raise ValueError("messages must contain at least one message.")
        return messages


class ResponseFormatType(str, Enum):
    text = "text"
    json_object = "json_object"
    json_schema = "json_schema"


class ResponseFormat(BaseModel):
    type: ResponseFormatType = ResponseFormatType.text
    json_schema: dict[str, Any] | None = None


class InputMessage(BaseModel):
    """
    Unified input message for v1/responses endpoint.
    Supports multimodal input (text, images, etc.)
    """

    role: str
    content: str | list[dict[str, Any]]  # String or multimodal content array


class ResponseRequest(BaseModel):
    """
    Unified API request schema for v1/responses endpoint.
    This is the newer, more flexible alternative to v1/chat/completions.
    """

    model: str
    input: list[InputMessage]  # Replaces 'messages' in chat/completions
    max_tokens: int | None = 4096
    temperature: float | None = 1.0
    top_p: float | None = 1.0
    frequency_penalty: float | None = 0.0
    presence_penalty: float | None = 0.0
    stream: bool | None = False
    tools: list[dict] | None = None  # Function calling tools
    response_format: ResponseFormat | None = None
    provider: str | None = None

    class Config:
        extra = "allow"

    @field_validator("input")
    @classmethod
    def validate_input(cls, messages: list[InputMessage]) -> list[InputMessage]:
        if not messages:
            raise ValueError("input must contain at least one message.")
        return messages


# ============================================================================
# Anthropic Messages API Schemas
# Compatible with: https://platform.claude.com/docs/en/api/messages
# ============================================================================


class CacheControl(BaseModel):
    """Cache control configuration for content blocks.

    See: https://platform.claude.com/docs/en/api/messages#caching-configuration
    """

    type: Literal["ephemeral"] = "ephemeral"
    ttl: Literal["5m", "1h"] | None = None  # Defaults to 5m if not specified

    class Config:
        extra = "allow"


class ImageSource(BaseModel):
    """Image source for image content blocks.

    See: https://platform.claude.com/docs/en/api/messages#imageblockparam
    """

    type: Literal["base64", "url"]
    data: str | None = None  # For base64 type
    url: str | None = None  # For url type
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"] | None = None

    class Config:
        extra = "allow"


class DocumentSource(BaseModel):
    """Document source for document content blocks.

    See: https://platform.claude.com/docs/en/api/messages#documentblockparam
    """

    type: Literal["base64", "url", "text", "content"]
    data: str | None = None
    url: str | None = None
    media_type: Literal["application/pdf", "text/plain"] | None = None

    class Config:
        extra = "allow"


class CitationConfig(BaseModel):
    """Citation configuration for document blocks."""

    enabled: bool = False

    class Config:
        extra = "allow"


class ContentBlock(BaseModel):
    """Content block for Anthropic Messages API.

    Supports multiple content types:
    - text: Text content with optional cache_control and citations
    - image: Image content with base64 or URL source
    - document: Document content (PDF, text) with optional citations
    - tool_use: Tool/function call from assistant
    - tool_result: Result of a tool call from user

    See: https://platform.claude.com/docs/en/api/messages#content-types
    """

    type: str  # "text", "image", "document", "tool_use", "tool_result"

    # Text block fields
    text: str | None = None
    cache_control: CacheControl | None = None
    citations: list[dict[str, Any]] | None = None

    # Image block fields
    source: ImageSource | DocumentSource | dict[str, Any] | None = None

    # Document block fields
    title: str | None = None
    context: str | None = None

    # Tool use block fields (from assistant)
    id: str | None = None  # Tool use ID
    name: str | None = None  # Tool name
    input: dict[str, Any] | None = None  # Tool input parameters

    # Tool result block fields (from user)
    tool_use_id: str | None = None  # References the tool_use id
    content: str | list[Any] | None = None  # Tool result content
    is_error: bool | None = None  # Whether the tool call resulted in an error

    class Config:
        extra = "allow"


class AnthropicMessage(BaseModel):
    """Message format for Anthropic Messages API.

    See: https://platform.claude.com/docs/en/api/messages#message-structure
    """

    role: str  # "user" or "assistant"
    content: str | list[ContentBlock]  # String or content blocks

    @field_validator("role")
    @classmethod
    def validate_role(cls, role: str) -> str:
        allowed_roles = {"user", "assistant"}
        if role not in allowed_roles:
            raise ValueError(
                f"Invalid Anthropic message role '{role}'. "
                f"Supported roles are: {', '.join(sorted(allowed_roles))}."
            )
        return role

    @field_validator("content")
    @classmethod
    def validate_content(
        cls, content: str | list[ContentBlock]
    ) -> str | list[ContentBlock]:
        if isinstance(content, str):
            if not content.strip():
                raise ValueError("Message content must be a non-empty string.")
        elif isinstance(content, list):
            if len(content) == 0:
                raise ValueError("Message content blocks cannot be empty.")
        else:
            raise ValueError("Message content must be a string or list of content blocks.")
        return content


class SystemContentBlock(BaseModel):
    """System content block for system prompts.

    See: https://platform.claude.com/docs/en/api/messages#system-prompt
    """

    type: Literal["text"] = "text"
    text: str
    cache_control: CacheControl | None = None

    class Config:
        extra = "allow"


class ToolChoiceAuto(BaseModel):
    """Tool choice: auto - model decides whether to use tools."""

    type: Literal["auto"] = "auto"
    disable_parallel_tool_use: bool | None = None

    class Config:
        extra = "allow"


class ToolChoiceAny(BaseModel):
    """Tool choice: any - model must use at least one tool."""

    type: Literal["any"] = "any"
    disable_parallel_tool_use: bool | None = None

    class Config:
        extra = "allow"


class ToolChoiceNone(BaseModel):
    """Tool choice: none - model cannot use any tools."""

    type: Literal["none"] = "none"

    class Config:
        extra = "allow"


class ToolChoiceTool(BaseModel):
    """Tool choice: tool - model must use the specified tool."""

    type: Literal["tool"] = "tool"
    name: str  # Name of the specific tool to use
    disable_parallel_tool_use: bool | None = None

    class Config:
        extra = "allow"


class ThinkingConfig(BaseModel):
    """Extended thinking configuration.

    See: https://platform.claude.com/docs/en/api/messages#extended-thinking-configuration
    """

    type: Literal["enabled", "disabled"] = "disabled"
    budget_tokens: int | None = None  # Minimum 1024, must be less than max_tokens

    class Config:
        extra = "allow"

    @field_validator("budget_tokens")
    @classmethod
    def validate_budget_tokens(cls, budget_tokens: int | None) -> int | None:
        if budget_tokens is not None and budget_tokens < 1024:
            raise ValueError("budget_tokens must be at least 1024.")
        return budget_tokens


class ToolDefinition(BaseModel):
    """Tool/function definition for function calling.

    See: https://platform.claude.com/docs/en/api/messages#tool-definitions
    """

    name: str
    description: str | None = None
    input_schema: dict[str, Any]
    cache_control: CacheControl | None = None

    class Config:
        extra = "allow"


class MessagesRequest(BaseModel):
    """
    Anthropic Messages API request schema (Claude API compatible).
    Endpoint: POST /v1/messages

    See: https://platform.claude.com/docs/en/api/messages

    Key differences from OpenAI:
    - Uses 'messages' array (like OpenAI) but 'system' is separate parameter
    - 'max_tokens' is REQUIRED (not optional)
    - Content can be string or array of content blocks
    - No frequency_penalty or presence_penalty
    - Supports tool use (function calling)
    - Supports extended thinking configuration
    """

    # Required parameters
    model: str  # e.g., "claude-sonnet-4-5-20250929", "claude-opus-4-5-20251101"
    messages: list[AnthropicMessage]
    max_tokens: int  # REQUIRED for Anthropic API

    # Optional parameters
    system: str | list[SystemContentBlock] | None = None  # System prompt (string or content blocks)
    temperature: float | None = 1.0  # 0.0 (analytical) to 1.0 (creative)
    top_p: float | None = None  # Nucleus sampling (use instead of temperature)
    top_k: int | None = None  # Sample from top K options
    stop_sequences: list[str] | None = None  # Custom stop sequences
    stream: bool | None = False  # Incrementally stream response
    metadata: dict[str, Any] | None = None  # External identifier (user_id) for abuse detection
    service_tier: Literal["auto", "standard_only"] | None = None  # Service tier selection

    # Tool use parameters
    tools: list[ToolDefinition | dict[str, Any]] | None = None  # Tool definitions
    tool_choice: ToolChoiceAuto | ToolChoiceAny | ToolChoiceNone | ToolChoiceTool | dict[str, Any] | None = None

    # Extended thinking configuration
    thinking: ThinkingConfig | None = None

    # Gateway-specific fields (not part of Anthropic API)
    provider: str | None = None

    class Config:
        extra = "allow"

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, messages: list[AnthropicMessage]) -> list[AnthropicMessage]:
        if not messages:
            raise ValueError("messages must contain at least one message.")
        return messages

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, max_tokens: int) -> int:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer.")
        return max_tokens

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, temperature: float | None) -> float | None:
        if temperature is not None and (temperature < 0.0 or temperature > 1.0):
            raise ValueError("temperature must be between 0.0 and 1.0.")
        return temperature


# ============================================================================
# Anthropic Messages API Response Schemas
# ============================================================================


class UsageResponse(BaseModel):
    """Token usage information in response.

    See: https://platform.claude.com/docs/en/api/messages#response-format
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation: dict[str, int] | None = None  # ephemeral_5m_input_tokens, ephemeral_1h_input_tokens
    server_tool_use: dict[str, int] | None = None  # web_search_requests

    class Config:
        extra = "allow"


class TextBlockResponse(BaseModel):
    """Text content block in response."""

    type: Literal["text"] = "text"
    text: str
    citations: list[dict[str, Any]] | None = None

    class Config:
        extra = "allow"


class ThinkingBlockResponse(BaseModel):
    """Thinking content block in response (extended thinking)."""

    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str

    class Config:
        extra = "allow"


class ToolUseBlockResponse(BaseModel):
    """Tool use content block in response."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]

    class Config:
        extra = "allow"


class MessagesResponse(BaseModel):
    """
    Anthropic Messages API response schema.

    See: https://platform.claude.com/docs/en/api/messages#response-format
    """

    id: str  # e.g., "msg_..."
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    model: str
    content: list[TextBlockResponse | ThinkingBlockResponse | ToolUseBlockResponse | dict[str, Any]]
    stop_reason: Literal["end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"] | None
    stop_sequence: str | None = None  # The stop sequence that was generated, if applicable
    usage: UsageResponse

    # Gateway-specific fields
    gateway_usage: dict[str, Any] | None = None

    class Config:
        extra = "allow"
