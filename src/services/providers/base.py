"""Canonical provider-adapter contract.

Every provider client in ``src/services/providers/`` exposes three module-level
callables that are registered in ``PROVIDER_ROUTING`` (see
``src/handlers/provider_registry.py``). That contract used to be implicit and
untyped. This module makes it explicit so new providers have a precise target
to implement and a conformance test can enforce uniformity.

The contract (matches what ``ChatInferenceHandler`` depends on):

``request(messages, model, **params) -> raw``
    Non-streaming call. ``raw`` is an OpenAI-SDK-shaped response object exposing:
      ``raw.choices[0].message.content`` : str
      ``raw.choices[0].finish_reason``   : str | None
      ``raw.usage.prompt_tokens``        : int
      ``raw.usage.completion_tokens``    : int
      ``raw.usage.total_tokens``         : int

``stream(messages, model, **params) -> Iterator[chunk]``
    A **sync** generator yielding OpenAI-SDK-shaped delta chunks.

``process(raw) -> dict``
    Converts ``raw`` to the OpenAI dict shape
    ``{id, object, created, model, choices, usage}``. NOTE: the authenticated
    ``ChatInferenceHandler`` path reads ``raw`` directly and does NOT call
    ``process()``; only the anonymous raw-dispatch path in ``chat.py`` uses it.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol, TypedDict, runtime_checkable


class ProviderParams(TypedDict, total=False):
    """Optional generation parameters the handler forwards to a provider call."""

    temperature: float | None
    max_tokens: int | None
    top_p: float | None
    frequency_penalty: float | None
    presence_penalty: float | None
    stop: str | list[str] | None
    tools: list[dict[str, Any]] | None
    tool_choice: str | dict[str, Any] | None
    response_format: dict[str, Any] | None
    user: str | None


# Function-form aliases (the current registry stores bare functions).
ProviderRequestFn = Callable[..., Any]
ProviderProcessFn = Callable[[Any], dict[str, Any]]
ProviderStreamFn = Callable[..., Iterator[Any]]


class ProviderRouting(TypedDict):
    """A single ``PROVIDER_ROUTING`` entry: the three callables for a provider.

    Values are ``None`` when the provider is disabled (its client is not loaded).
    """

    request: ProviderRequestFn | None
    process: ProviderProcessFn | None
    stream: ProviderStreamFn | None


@runtime_checkable
class ProviderAdapter(Protocol):
    """Object-form contract for the reference adapter and future class adapters.

    Existing providers satisfy the function-form (``ProviderRouting``); new
    adapters may implement this object form. Both are valid contract shapes.
    """

    def request(self, messages: list[dict[str, Any]], model: str, **params: Any) -> Any: ...

    def stream(
        self, messages: list[dict[str, Any]], model: str, **params: Any
    ) -> Iterator[Any]: ...

    def process(self, response: Any) -> dict[str, Any]: ...
