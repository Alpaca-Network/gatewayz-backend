"""
Selective GZip middleware that skips compression for streaming responses.

SSE (Server-Sent Events) and other streaming responses should not be compressed
because GZip requires buffering the entire response before compression, which
defeats the purpose of streaming.
"""

import gzip
import io

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SelectiveGZipMiddleware:
    """
    GZip middleware that skips compression for streaming responses.

    This middleware applies GZip compression to responses but bypasses it
    for responses with media types that should not be buffered:
    - text/event-stream (SSE)
    - application/x-ndjson (streaming JSON)
    - Any response with X-Accel-Buffering: no header

    Args:
        app: The ASGI application to wrap
        minimum_size: Minimum response size to trigger compression (default: 500 bytes)
        compresslevel: GZip compression level 1-9 (default: 9)
    """

    # Media types that should never be compressed (streaming responses)
    STREAMING_MEDIA_TYPES = frozenset(
        [
            "text/event-stream",
            "application/x-ndjson",
            "application/stream+json",
        ]
    )

    def __init__(
        self,
        app: ASGIApp,
        # 1 KB minimum: responses smaller than this gain little from compression
        # (gzip header overhead ~20 bytes makes compression counterproductive under ~1 KB).
        # Configurable via GZIP_MINIMUM_SIZE env var in Config.
        minimum_size: int = 1024,
        compresslevel: int = 9,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check if client accepts gzip encoding
        headers = Headers(scope=scope)
        accept_encoding = headers.get("accept-encoding", "")
        if "gzip" not in accept_encoding.lower():
            # Client doesn't accept gzip, pass through
            await self.app(scope, receive, send)
            return

        # State for response handling
        is_streaming = False
        initial_message: Message | None = None
        body_parts: list[bytes] = []
        gzip_applied = False

        async def send_wrapper(message: Message) -> None:
            nonlocal is_streaming, initial_message, body_parts, gzip_applied

            if message["type"] == "http.response.start":
                initial_message = message
                headers = MutableHeaders(raw=list(message.get("headers", [])))

                # Check if this is a streaming response
                content_type = headers.get("content-type", "").lower()
                for streaming_type in self.STREAMING_MEDIA_TYPES:
                    if streaming_type in content_type:
                        is_streaming = True
                        break

                # Check for explicit no-buffering header
                if headers.get("x-accel-buffering", "").lower() == "no":
                    is_streaming = True

                if is_streaming:
                    # For streaming responses, send immediately without gzip
                    await send(message)
                # For non-streaming, we buffer to check size

            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)

                if is_streaming:
                    # Streaming: send body immediately without compression
                    await send(message)
                else:
                    # Non-streaming: buffer body
                    if body:
                        body_parts.append(body)

                    if not more_body:
                        # End of response, apply gzip if appropriate
                        full_body = b"".join(body_parts)
                        if len(full_body) >= self.minimum_size and initial_message:
                            # Apply gzip compression
                            compressed_body = self._compress(full_body)

                            # Update headers
                            headers = MutableHeaders(
                                raw=list(initial_message.get("headers", []))
                            )
                            headers["content-encoding"] = "gzip"
                            headers["content-length"] = str(len(compressed_body))
                            # Remove vary header conflicts
                            vary = headers.get("vary", "")
                            if vary and "accept-encoding" not in vary.lower():
                                headers["vary"] = f"{vary}, Accept-Encoding"
                            elif not vary:
                                headers["vary"] = "Accept-Encoding"

                            initial_message["headers"] = headers.raw
                            await send(initial_message)
                            await send(
                                {
                                    "type": "http.response.body",
                                    "body": compressed_body,
                                    "more_body": False,
                                }
                            )
                            gzip_applied = True
                        else:
                            # Body too small, send uncompressed
                            if initial_message:
                                await send(initial_message)
                            await send(
                                {
                                    "type": "http.response.body",
                                    "body": full_body,
                                    "more_body": False,
                                }
                            )

        await self.app(scope, receive, send_wrapper)

    def _compress(self, data: bytes) -> bytes:
        """Compress data using gzip."""
        buffer = io.BytesIO()
        with gzip.GzipFile(
            mode="wb", fileobj=buffer, compresslevel=self.compresslevel
        ) as f:
            f.write(data)
        return buffer.getvalue()
