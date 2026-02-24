"""
Tests for Selective GZip Middleware

This test suite verifies that the SelectiveGZipMiddleware correctly:
1. Skips compression for SSE streaming responses (text/event-stream)
2. Skips compression for responses with X-Accel-Buffering: no header
3. Applies compression for regular JSON responses above minimum_size
4. Does not compress responses below minimum_size
"""

import gzip

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from src.middleware.selective_gzip_middleware import SelectiveGZipMiddleware


@pytest.fixture
def app_with_selective_gzip():
    """Create a test FastAPI app with SelectiveGZipMiddleware"""
    app = FastAPI()

    # Add SelectiveGZipMiddleware with low minimum_size for testing
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=100)

    @app.get("/json-small")
    async def small_json():
        """Small JSON response - should NOT be compressed"""
        return {"status": "ok"}

    @app.get("/json-large")
    async def large_json():
        """Large JSON response - should be compressed"""
        return {"data": "x" * 500, "more_data": "y" * 500}

    @app.get("/sse-stream")
    async def sse_stream():
        """SSE streaming response - should NOT be compressed"""

        async def generate():
            for i in range(5):
                yield f"data: message {i}\n\n"

        # Include anti-buffering headers like in production code
        headers = {
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        }
        return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)

    @app.get("/sse-stream-no-headers")
    async def sse_stream_no_headers():
        """SSE streaming response without explicit headers - should NOT be compressed based on content-type"""

        async def generate():
            for i in range(5):
                yield f"data: message {i}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/ndjson-stream")
    async def ndjson_stream():
        """NDJSON streaming response - should NOT be compressed"""

        async def generate():
            for i in range(5):
                yield f'{{"id": {i}}}\n'

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.get("/binary-stream")
    async def binary_stream():
        """Binary streaming with X-Accel-Buffering header - should NOT be compressed"""

        async def generate():
            for i in range(5):
                yield b"binary data chunk "

        headers = {"X-Accel-Buffering": "no"}
        return StreamingResponse(generate(), media_type="application/octet-stream", headers=headers)

    return app


@pytest.fixture
def client(app_with_selective_gzip):
    """Create test client with gzip accept-encoding"""
    return TestClient(app_with_selective_gzip)


class TestSelectiveGZipMiddleware:
    """Test SelectiveGZipMiddleware functionality"""

    def test_small_json_not_compressed(self, client):
        """Test that small JSON responses are NOT compressed (below minimum_size)"""
        response = client.get("/json-small", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        # Small response should not be compressed
        assert (
            "content-encoding" not in response.headers
            or response.headers.get("content-encoding") != "gzip"
        )

    def test_large_json_compressed(self, client):
        """Test that large JSON responses ARE compressed"""
        response = client.get("/json-large", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        # Large response should be compressed
        assert response.headers.get("content-encoding") == "gzip"

        # Verify we can decompress the content
        decompressed = gzip.decompress(response.content)
        assert b"data" in decompressed
        assert b"more_data" in decompressed

    def test_sse_stream_not_compressed(self, client):
        """Test that SSE streaming responses are NOT compressed"""
        response = client.get("/sse-stream", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200

        # SSE should NOT be gzip compressed (would break streaming)
        assert response.headers.get("content-encoding") != "gzip"

        # Verify content-type is preserved
        assert "text/event-stream" in response.headers.get("content-type", "")

        # Verify anti-buffering headers are present
        assert response.headers.get("x-accel-buffering") == "no"
        assert "no-cache" in response.headers.get("cache-control", "")

        # Verify we can read the SSE content directly
        content = response.content.decode("utf-8")
        assert "data: message 0" in content
        assert "data: message 4" in content

    def test_sse_stream_without_explicit_headers_not_compressed(self, client):
        """Test that SSE streaming is NOT compressed even without explicit headers"""
        response = client.get("/sse-stream-no-headers", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200

        # SSE should NOT be gzip compressed based on content-type alone
        assert response.headers.get("content-encoding") != "gzip"

        # Verify content is readable
        content = response.content.decode("utf-8")
        assert "data: message 0" in content

    def test_ndjson_stream_not_compressed(self, client):
        """Test that NDJSON streaming responses are NOT compressed"""
        response = client.get("/ndjson-stream", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200

        # NDJSON streaming should NOT be compressed
        assert response.headers.get("content-encoding") != "gzip"

        # Verify content-type is preserved
        assert "application/x-ndjson" in response.headers.get("content-type", "")

    def test_binary_stream_with_no_buffering_header_not_compressed(self, client):
        """Test that responses with X-Accel-Buffering: no are NOT compressed"""
        response = client.get("/binary-stream", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200

        # Response with X-Accel-Buffering: no should NOT be compressed
        assert response.headers.get("content-encoding") != "gzip"

    def test_no_gzip_without_accept_encoding(self, client):
        """Test that responses are NOT compressed when client doesn't accept gzip"""
        response = client.get("/json-large")  # No Accept-Encoding header
        assert response.status_code == 200
        # Should not be compressed without Accept-Encoding: gzip
        assert response.headers.get("content-encoding") != "gzip"

    def test_vary_header_added_for_compressed_response(self, client):
        """Test that Vary: Accept-Encoding header is added for compressed responses"""
        response = client.get("/json-large", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.headers.get("content-encoding") == "gzip"
        # Vary header should include Accept-Encoding
        vary = response.headers.get("vary", "")
        assert "Accept-Encoding" in vary or "accept-encoding" in vary.lower()


class TestSelectiveGZipMiddlewareEdgeCases:
    """Test edge cases for SelectiveGZipMiddleware"""

    def test_streaming_media_types_constant(self):
        """Test that STREAMING_MEDIA_TYPES contains expected types"""
        assert "text/event-stream" in SelectiveGZipMiddleware.STREAMING_MEDIA_TYPES
        assert "application/x-ndjson" in SelectiveGZipMiddleware.STREAMING_MEDIA_TYPES
        assert "application/stream+json" in SelectiveGZipMiddleware.STREAMING_MEDIA_TYPES

    def test_compress_method(self):
        """Test the internal _compress method"""
        middleware = SelectiveGZipMiddleware(app=None, minimum_size=100)
        original = b"Hello World! " * 100
        compressed = middleware._compress(original)

        # Verify it's actually compressed (smaller than original)
        assert len(compressed) < len(original)

        # Verify we can decompress it
        decompressed = gzip.decompress(compressed)
        assert decompressed == original


class TestStreamingHeadersIntegration:
    """Test that streaming headers are correctly set in production-like scenarios"""

    def test_sse_headers_complete(self, client):
        """Test that all expected SSE headers are present"""
        response = client.get("/sse-stream", headers={"Accept-Encoding": "gzip"})

        # Check all anti-buffering headers
        assert response.headers.get("x-accel-buffering") == "no"
        assert "no-cache" in response.headers.get("cache-control", "")
        assert "no-transform" in response.headers.get("cache-control", "")
        assert response.headers.get("connection") == "keep-alive"

        # Content-type should be SSE
        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_chunks_received_individually(self, client):
        """Test that SSE chunks can be received as they stream"""
        # Note: TestClient buffers responses, so this mainly verifies
        # the response format is correct for streaming
        response = client.get("/sse-stream", headers={"Accept-Encoding": "gzip"})

        content = response.content.decode("utf-8")
        lines = content.strip().split("\n\n")

        # Should have 5 SSE messages
        assert len(lines) == 5

        # Each should be a properly formatted SSE event
        for i, line in enumerate(lines):
            assert line == f"data: message {i}"
