from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, field_validator, model_validator

ALLOWED_CHAT_ROLES = {"system", "user", "assistant", "tool", "function", "developer"}


class Message(BaseModel):
    """
    Message format for OpenAI Chat Completions API.
    Supports text content, multimodal content, and tool/function message types.
    """

    role: str
    content: str | list[dict[str, Any]] | None = None  # Can be null for tool calls
    name: str | None = None  # Optional name for function messages
    tool_calls: list[dict[str, Any]] | None = None  # Tool calls from assistant
    tool_call_id: str | None = None  # For tool response messages

    class Config:
        extra = "allow"

    @field_validator("role")
    @classmethod
    def validate_role(cls, role: str) -> str:
        if role not in ALLOWED_CHAT_ROLES:
            raise ValueError(
                f"Invalid message role '{role}'. "
                f"Supported roles are: {', '.join(sorted(ALLOWED_CHAT_ROLES))}."
            )
        return role

    @model_validator(mode="after")
    def validate_content_for_role(self) -> "Message":
        """Validate that content is provided for roles that require it.

        - user, system, and developer roles require non-empty content
        - assistant role allows null content when tool_calls is present
        - tool and function roles require non-empty content (the tool response)
        """
        role = self.role
        content = self.content
        tool_calls = self.tool_calls

        # Helper to check if content is empty (None or empty string after stripping)
        def is_empty_content(c: Any) -> bool:
            if c is None:
                return True
            if isinstance(c, str):
                return not c.strip()
            # For list content (multimodal), check if empty
            if isinstance(c, list):
                return len(c) == 0
            return False

        # User, system, and developer messages must have non-empty content
        if role in ("user", "system", "developer") and is_empty_content(content):
            raise ValueError(f"'{role}' messages must have non-empty content.")

        # Tool/function responses must have non-empty content
        if role in ("tool", "function") and is_empty_content(content):
            raise ValueError(f"'{role}' messages must have non-empty content (the tool response).")

        # Assistant messages can have null content only if tool_calls is present
        if role == "assistant" and is_empty_content(content) and not tool_calls:
            raise ValueError(
                "Assistant messages must have either non-empty content or tool_calls."
            )

        return self


class StreamOptions(BaseModel):
    """Options for streaming response."""

    include_usage: bool | None = None  # Include usage statistics in streaming chunks


class ProxyRequest(BaseModel):
    """
    OpenAI-compatible Chat Completions API request schema.
    Endpoint: POST /v1/chat/completions

    This schema aligns with OpenAI's Chat Completions API specification.
    See: https://platform.openai.com/docs/api-reference/chat/create
    """

    # Required parameters
    model: str = Field(..., description="ID of the model to use")
    messages: list[Message] = Field(
        ..., description="A list of messages comprising the conversation so far"
    )

    # Optional sampling parameters
    max_tokens: int | None = Field(
        default=4096,
        description="The maximum number of tokens that can be generated in the chat completion",
    )
    temperature: float | None = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        description="Sampling temperature between 0 and 2. Higher values make output more random",
    )
    top_p: float | None = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling: consider tokens with top_p probability mass",
    )
    n: int | None = Field(
        default=1,
        ge=1,
        description="How many chat completion choices to generate for each input message",
    )
    stop: str | list[str] | None = Field(
        default=None,
        description="Up to 4 sequences where the API will stop generating further tokens",
    )

    # Penalty parameters
    frequency_penalty: float | None = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
        description="Penalize new tokens based on their existing frequency in the text",
    )
    presence_penalty: float | None = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
        description="Penalize new tokens based on whether they appear in the text so far",
    )

    # Streaming parameters
    stream: bool | None = Field(
        default=False, description="If set, partial message deltas will be sent"
    )
    stream_options: StreamOptions | None = Field(
        default=None, description="Options for streaming response. Only set when stream is true"
    )

    # Tool/function calling parameters
    tools: list[dict] | None = Field(
        default=None, description="A list of tools the model may call"
    )
    tool_choice: str | dict | None = Field(
        default=None,
        description="Controls which tool is called. 'none', 'auto', 'required', or specific tool",
    )
    parallel_tool_calls: bool | None = Field(
        default=True, description="Whether to enable parallel function calling during tool use"
    )

    # Response format parameters
    response_format: dict | None = Field(
        default=None,
        description="Format for model output: 'text', 'json_object', or 'json_schema'",
    )

    # Advanced parameters
    logprobs: bool | None = Field(
        default=None,
        description="Whether to return log probabilities of the output tokens",
    )
    top_logprobs: int | None = Field(
        default=None,
        ge=0,
        le=20,
        description="Number of most likely tokens to return at each position (0-20)",
    )
    logit_bias: dict[str, int] | None = Field(
        default=None,
        description="Modify likelihood of specified tokens appearing in the completion",
    )
    seed: int | None = Field(
        default=None,
        description="Seed for deterministic sampling. Results may still vary",
    )
    user: str | None = Field(
        default=None,
        description="Unique identifier for your end-user for abuse monitoring",
    )
    service_tier: Literal["auto", "default"] | None = Field(
        default=None, description="Latency tier for processing the request"
    )

    # Gateway-specific parameters (not part of OpenAI API)
    provider: str | None = Field(
        default=None, description="Provider selection: 'openrouter', 'featherless', etc"
    )
    auto_web_search: bool | Literal["auto"] | None = Field(
        default="auto",
        description=(
            "Enable automatic web search for queries that would benefit from real-time information. "
            "Set to True to always search, False to disable, or 'auto' (default) to let the system "
            "decide based on query analysis. When enabled, search results are prepended to the context."
        ),
    )
    web_search_threshold: float | None = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence threshold for auto web search (0.0-1.0). Lower values trigger search more often. "
            "Only used when auto_web_search is 'auto'. Default is 0.5."
        ),
    )

    class Config:
        extra = "allow"

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, messages: list[Message]) -> list[Message]:
        if not messages:
            raise ValueError("messages must contain at least one message.")
        return messages

    @field_validator("stop")
    @classmethod
    def validate_stop(cls, stop: str | list[str] | None) -> str | list[str] | None:
        if stop is None:
            return None
        if isinstance(stop, str):
            return stop
        if isinstance(stop, list):
            if len(stop) > 4:
                raise ValueError("stop can contain at most 4 sequences")
            return stop
        raise ValueError("stop must be a string or list of strings")


class ResponseFormatType(str, Enum):  # noqa: UP042
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

    @model_validator(mode="after")
    def validate_source_fields(self) -> "ImageSource":
        """Validate that required fields are present based on type."""
        if self.type == "base64":
            if not self.data:
                raise ValueError("'data' field is required when type is 'base64'")
            if not self.media_type:
                raise ValueError("'media_type' field is required when type is 'base64'")
        elif self.type == "url":
            if not self.url:
                raise ValueError("'url' field is required when type is 'url'")
        return self


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

    @model_validator(mode="after")
    def validate_source_fields(self) -> "DocumentSource":
        """Validate that required fields are present based on type."""
        if self.type == "base64":
            if not self.data:
                raise ValueError("'data' field is required when type is 'base64'")
            if not self.media_type:
                raise ValueError("'media_type' field is required when type is 'base64'")
        elif self.type == "url":
            if not self.url:
                raise ValueError("'url' field is required when type is 'url'")
        elif self.type == "text":
            if not self.data:
                raise ValueError("'data' field is required when type is 'text'")
        # 'content' type has different structure, validated elsewhere
        return self


class CitationConfig(BaseModel):
    """Citation configuration for document blocks."""

    enabled: bool = False

    class Config:
        extra = "allow"


class ToolResultContentBlock(BaseModel):
    """Content block within a tool_result (text or image).

    Tool results can contain text or image content blocks.
    See: https://platform.claude.com/docs/en/api/messages#tool-results
    """

    type: Literal["text", "image"]
    text: str | None = None  # For type="text"
    source: ImageSource | dict[str, Any] | None = None  # For type="image"

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
    content: str | list[ToolResultContentBlock] | None = None  # Tool result content (string or content blocks)
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


# Discriminated union for tool_choice - Pydantic uses the 'type' field to determine which model to use
# This ensures proper serialization/deserialization across different clients
ToolChoice = Annotated[
    ToolChoiceAuto | ToolChoiceAny | ToolChoiceNone | ToolChoiceTool,
    Discriminator("type"),
]


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
    tool_choice: ToolChoice | dict[str, Any] | None = None  # Discriminated union by 'type' field

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
