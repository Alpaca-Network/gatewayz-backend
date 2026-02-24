"""
Braintrust integration for LLM tracing and observability.

DEPRECATED: This module is deprecated. Use src.services.braintrust_service instead.

The braintrust_service module provides proper project association by using
logger.start_span() instead of the standalone start_span() function.

This module is kept for backward compatibility only.

Learn more at https://www.braintrust.dev/docs
"""

import warnings

warnings.warn(
    "braintrust_tracing is deprecated. Use src.services.braintrust_service instead.",
    DeprecationWarning,
    stacklevel=2,
)

import functools
import inspect
import logging

try:
    from braintrust import current_span, init_logger, start_span, traced
except ModuleNotFoundError:
    # Provide graceful degradation when Braintrust SDK isn't installed.
    class _NoopSpan:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def log(self, *args, **kwargs):
            return None

    def current_span():
        return _NoopSpan()

    def init_logger(project="Gatewayz Backend"):
        stub_logger = logging.getLogger(f"braintrust_stub:{project}")
        if not stub_logger.handlers:
            handler = logging.StreamHandler()
            stub_logger.addHandler(handler)
        stub_logger.setLevel(logging.INFO)
        stub_logger.info("Braintrust SDK not installed - no-op tracing enabled.")
        return stub_logger

    def start_span(*args, **kwargs):
        return _NoopSpan()

    def traced(*decorator_args, **decorator_kwargs):
        def decorator(func):
            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    return await func(*args, **kwargs)

                return async_wrapper

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                return func(*args, **kwargs)

            return sync_wrapper

        # Support bare decorator usage: @traced without parentheses
        if decorator_args and callable(decorator_args[0]) and not decorator_kwargs:
            return decorator(decorator_args[0])

        return decorator


# NOTE: This module-level logger initialization is DEPRECATED and kept only for
# backward compatibility. It demonstrates the OLD pattern that caused orphaned spans.
# New code should use src.services.braintrust_service which properly stores the logger
# and uses logger.start_span() for correct project association.
logger = init_logger(project="Gatewayz Backend")


def call_my_llm(input: str, params: dict) -> dict:
    """
    Replace this with your custom LLM implementation.

    This is a placeholder that should be replaced with actual LLM calls
    to your inference endpoints.
    """
    return {
        "completion": "Hello, world!",
        "metrics": {
            "prompt_tokens": len(input),
            "completion_tokens": 10,
        },
    }


# notrace_io=True prevents logging the function arguments as input,
# and lets us log a more specific input format.
@traced(type="llm", name="Custom LLM", notrace_io=True)
def invoke_custom_llm(llm_input: str, params: dict):
    """
    Invoke a custom LLM with Braintrust tracing.

    Args:
        llm_input: The input prompt for the LLM
        params: Additional parameters for the LLM call (e.g., temperature, max_tokens)

    Returns:
        The completion content from the LLM
    """
    result = call_my_llm(llm_input, params)
    content = result["completion"]

    # Log detailed span information
    current_span().log(
        input=[{"role": "user", "content": llm_input}],
        output=content,
        metrics={
            "prompt_tokens": result["metrics"]["prompt_tokens"],
            "completion_tokens": result["metrics"]["completion_tokens"],
            "tokens": result["metrics"]["prompt_tokens"] + result["metrics"]["completion_tokens"],
        },
        metadata=params,
    )

    return content


def my_route_handler(req):
    """
    Example route handler with Braintrust tracing.

    This demonstrates how to trace an entire request/response cycle,
    including nested LLM calls.

    Args:
        req: The incoming request object

    Returns:
        The LLM response
    """
    with start_span() as span:
        result = invoke_custom_llm(
            llm_input=req.body,
            params={"temperature": 0.1},
        )

        # Log the overall request/response
        span.log(input=req.body, output=result)

        return result


# Example usage for FastAPI integration
@traced(name="chat_completion_endpoint")
async def traced_chat_completion(messages: list, model: str, **kwargs):
    """
    Example traced endpoint for chat completions.

    This can be integrated into your existing FastAPI routes.
    """
    with start_span(name=f"chat_completion_{model}") as span:
        # Log the input
        span.log(
            input={"messages": messages, "model": model, "params": kwargs},
        )

        # Make your LLM call here
        # result = await your_llm_service.chat_completion(messages, model, **kwargs)

        # For demonstration purposes
        result = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "This is a traced response",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        }

        # Log the output and metrics
        span.log(
            output=result,
            metrics={
                "prompt_tokens": result["usage"]["prompt_tokens"],
                "completion_tokens": result["usage"]["completion_tokens"],
                "total_tokens": result["usage"]["total_tokens"],
            },
        )

        return result
