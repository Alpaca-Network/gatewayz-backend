"""
Root-path aliases for the OpenAI-compatible inference endpoints.

The OpenAI SDK appends ``/chat/completions`` (not ``/v1/chat/completions``)
to ``base_url``, so a client configured with ``base_url="https://api.gatewayz.ai"``
calls ``POST /chat/completions``. Every inference route is mounted only under
``/v1``, so those requests 404'd (hundreds a day in production).

This middleware rewrites the path to its ``/v1`` form before any other layer
sees it, so rate limiting, timeouts, metrics and Sentry all treat the alias
exactly like the canonical route. No route logic is duplicated, and the aliases
stay out of the OpenAPI schema.

``/models`` is not listed: it is already mounted at the root (``main.py``).
"""

from starlette.types import ASGIApp, Receive, Scope, Send

# Exact root paths that are rewritten to "/v1" + path.
ALIASED_PATHS = frozenset(
    {
        "/chat/completions",
        "/completions",
        "/embeddings",
    }
)


class OpenAIPathAliasMiddleware:
    """Pure ASGI middleware: rewrite ``/chat/completions`` → ``/v1/chat/completions`` etc."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") in ALIASED_PATHS:
            path = "/v1" + scope["path"]
            scope = dict(scope)
            scope["path"] = path
            scope["raw_path"] = path.encode("latin-1")
        await self.app(scope, receive, send)
