"""
Base adapter interface for chat format transformations.

All chat adapters (OpenAI, Anthropic, AI SDK, etc.) must implement this interface.
This ensures consistent conversion between external formats and internal schemas.
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from src.schemas.internal.chat import InternalChatRequest, InternalChatResponse, InternalStreamChunk


class BaseChatAdapter(ABC):
    """
    Abstract base class for chat format adapters.

    Adapters convert between external API formats (OpenAI, Anthropic, etc.)
    and the internal unified format used by ChatInferenceHandler.

    Each adapter must implement:
    1. to_internal_request: External request → Internal request
    2. from_internal_response: Internal response → External response
    3. from_internal_stream: Internal stream chunks → External stream format
    4. format_name: Identifier for the external format
    """

    @abstractmethod
    def to_internal_request(self, external_request: dict[str, Any]) -> InternalChatRequest:
        """
        Convert external API request format to internal format.

        Args:
            external_request: Request in external format (OpenAI, Anthropic, etc.)

        Returns:
            InternalChatRequest: Normalized internal request format

        Example:
            >>> adapter = OpenAIChatAdapter()
            >>> openai_req = {"messages": [...], "model": "gpt-4", ...}
            >>> internal_req = adapter.to_internal_request(openai_req)
        """
        pass

    @abstractmethod
    def from_internal_response(self, internal_response: InternalChatResponse) -> dict[str, Any]:
        """
        Convert internal response format to external API format.

        Args:
            internal_response: Response from ChatInferenceHandler

        Returns:
            Dict: Response in external format (OpenAI, Anthropic, etc.)

        Example:
            >>> adapter = OpenAIChatAdapter()
            >>> internal_resp = InternalChatResponse(...)
            >>> openai_resp = adapter.from_internal_response(internal_resp)
        """
        pass

    @abstractmethod
    async def from_internal_stream(
        self, internal_stream: AsyncIterator[InternalStreamChunk]
    ) -> AsyncIterator[str]:
        """
        Convert internal streaming chunks to external streaming format.

        Args:
            internal_stream: AsyncIterator of InternalStreamChunk from handler

        Yields:
            str: Formatted streaming chunks (SSE format, JSON lines, etc.)

        Example:
            >>> adapter = OpenAIChatAdapter()
            >>> async for chunk in adapter.from_internal_stream(internal_stream):
            ...     # chunk is "data: {...}\\n\\n" in OpenAI SSE format
            ...     yield chunk
        """
        pass

    @property
    @abstractmethod
    def format_name(self) -> str:
        """
        Return identifier for this format.

        Returns:
            str: Format name (e.g., 'openai', 'anthropic', 'ai-sdk')

        Used for logging and debugging.
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} format={self.format_name}>"
