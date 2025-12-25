from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_CHAT_ROLES = {"system", "user", "assistant", "tool", "function"}


class Message(BaseModel):
    """
    Message format for OpenAI Chat Completions API.
    Supports text content and tool/function message types.
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

        - user and system roles require content
        - assistant role allows null content when tool_calls is present
        - tool and function roles require content (the tool response)
        """
        role = self.role
        content = self.content
        tool_calls = self.tool_calls

        # User and system messages must have content
        if role in ("user", "system") and content is None:
            raise ValueError(f"'{role}' messages must have content.")

        # Tool/function responses must have content
        if role in ("tool", "function") and content is None:
            raise ValueError(f"'{role}' messages must have content (the tool response).")

        # Assistant messages can have null content only if tool_calls is present
        if role == "assistant" and content is None and not tool_calls:
            raise ValueError(
                "Assistant messages must have either content or tool_calls."
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
# ============================================================================


class ContentBlock(BaseModel):
    """Content block for Anthropic Messages API"""

    type: str  # "text", "image", etc.
    text: str | None = None
    source: dict[str, Any] | None = None  # For image blocks

    class Config:
        extra = "allow"


class AnthropicMessage(BaseModel):
    """Message format for Anthropic Messages API"""

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


class MessagesRequest(BaseModel):
    """
    Anthropic Messages API request schema (Claude API compatible).
    Endpoint: POST /v1/messages

    Key differences from OpenAI:
    - Uses 'messages' array (like OpenAI) but 'system' is separate parameter
    - 'max_tokens' is REQUIRED (not optional)
    - Content can be string or array of content blocks
    - No frequency_penalty or presence_penalty
    - Supports tool use (function calling)
    """

    model: str  # e.g., "claude-sonnet-4-5-20250929"
    messages: list[AnthropicMessage]
    max_tokens: int  # REQUIRED for Anthropic API
    system: str | None = None  # System prompt (separate from messages)
    temperature: float | None = 1.0
    top_p: float | None = None
    top_k: int | None = None  # Anthropic-specific
    stop_sequences: list[str] | None = None
    stream: bool | None = False
    metadata: dict[str, Any] | None = None
    tools: list[dict] | None = None  # Tool definitions for function calling
    tool_choice: Any | None = None  # Tool selection: "auto", "required", or specific tool

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
