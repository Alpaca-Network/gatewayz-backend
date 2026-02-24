"""Unit tests for stream_generator function.

Tests the stream_generator function from src/routes/chat.py to ensure
correct behavior for different user scenarios including anonymous users.
"""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest


def create_mock_stream_chunks(content_chunks: list[str], model: str = "gpt-3.5-turbo"):
    """Create mock stream chunks that simulate OpenAI-style streaming response."""
    chunks = []
    for i, content in enumerate(content_chunks):
        chunks.append(
            Mock(
                id=f"chatcmpl-{i}",
                object="chat.completion.chunk",
                created=1234567890,
                model=model,
                choices=[
                    Mock(
                        index=0,
                        delta=Mock(role="assistant" if i == 0 else None, content=content),
                        finish_reason=None,
                    )
                ],
                usage=None,
            )
        )

    # Final chunk with finish_reason
    chunks.append(
        Mock(
            id="chatcmpl-final",
            object="chat.completion.chunk",
            created=1234567890,
            model=model,
            choices=[Mock(index=0, delta=Mock(role=None, content=None), finish_reason="stop")],
            usage=Mock(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
    )
    return chunks


def mock_stream_generator(chunks):
    """Create a generator from mock chunks."""
    for chunk in chunks:
        yield chunk


@pytest.fixture
def mock_enforce_plan_limits():
    """Mock enforce_plan_limits to return allowed."""
    with patch("src.routes.chat.enforce_plan_limits") as mock:
        mock.return_value = {"allowed": True}
        yield mock


@pytest.fixture
def mock_process_stream_completion():
    """Mock the background processing function."""
    with patch(
        "src.routes.chat._process_stream_completion_background", new_callable=AsyncMock
    ) as mock:
        yield mock


class TestStreamGeneratorAnonymous:
    """Tests for stream_generator with anonymous users."""

    @pytest.mark.asyncio
    async def test_anonymous_stream_does_not_call_enforce_plan_limits(
        self, mock_enforce_plan_limits, mock_process_stream_completion
    ):
        """Test that anonymous streaming does not call enforce_plan_limits.

        This test verifies the fix for the TypeError that occurred when
        stream_generator tried to access user["id"] for anonymous users
        where user is None.
        """
        from src.routes.chat import stream_generator

        # Create mock stream with content
        chunks = create_mock_stream_chunks(["Hello", " world", "!"])

        # Call stream_generator with is_anonymous=True and user=None
        stream_output = []
        async for chunk in stream_generator(
            mock_stream_generator(chunks),
            user=None,  # Anonymous user
            api_key=None,
            model="gpt-3.5-turbo",
            trial={"is_trial": False, "is_expired": False},
            environment_tag="live",
            session_id=None,
            messages=[{"role": "user", "content": "Hello"}],
            rate_limit_mgr=None,
            provider="openrouter",
            tracker=None,
            is_anonymous=True,  # Key: Anonymous flag
        ):
            stream_output.append(chunk)

        # Verify enforce_plan_limits was NOT called
        mock_enforce_plan_limits.assert_not_called()

        # Verify we got content chunks and [DONE]
        assert len(stream_output) > 0
        assert any("[DONE]" in chunk for chunk in stream_output)

        # Verify no error in output
        for chunk in stream_output:
            if chunk.startswith("data: ") and chunk != "data: [DONE]\n\n":
                data = json.loads(chunk[6:].strip())
                assert "error" not in data, f"Unexpected error in stream: {data}"

    @pytest.mark.asyncio
    async def test_anonymous_stream_completes_successfully(
        self, mock_enforce_plan_limits, mock_process_stream_completion
    ):
        """Test that anonymous streaming completes without errors."""
        from src.routes.chat import stream_generator

        # Create mock stream with content
        chunks = create_mock_stream_chunks(["Test", " content"])

        stream_output = []
        error_found = False

        async for chunk in stream_generator(
            mock_stream_generator(chunks),
            user=None,
            api_key=None,
            model="gpt-3.5-turbo",
            trial={"is_trial": False, "is_expired": False},
            environment_tag="live",
            session_id=None,
            messages=[{"role": "user", "content": "Test"}],
            rate_limit_mgr=None,
            provider="openrouter",
            tracker=None,
            is_anonymous=True,
        ):
            stream_output.append(chunk)
            if '"error"' in chunk and '"type": "stream_error"' in chunk:
                error_found = True

        # Stream should complete without stream_error
        assert not error_found, "Stream should complete without stream_error"
        assert "data: [DONE]\n\n" in stream_output


class TestStreamGeneratorAuthenticated:
    """Tests for stream_generator with authenticated users."""

    @pytest.mark.asyncio
    async def test_authenticated_stream_calls_enforce_plan_limits(
        self, mock_enforce_plan_limits, mock_process_stream_completion
    ):
        """Test that authenticated streaming calls enforce_plan_limits."""
        from src.routes.chat import stream_generator

        # Create mock stream with content
        chunks = create_mock_stream_chunks(["Hello"])

        # Mock user object
        user = {"id": 123, "credits": 100.0, "environment_tag": "live"}

        stream_output = []
        async for chunk in stream_generator(
            mock_stream_generator(chunks),
            user=user,
            api_key="test-key",
            model="gpt-3.5-turbo",
            trial={"is_trial": False, "is_expired": False},
            environment_tag="live",
            session_id=None,
            messages=[{"role": "user", "content": "Hello"}],
            rate_limit_mgr=None,
            provider="openrouter",
            tracker=None,
            is_anonymous=False,
        ):
            stream_output.append(chunk)

        # Verify enforce_plan_limits WAS called with user ID
        mock_enforce_plan_limits.assert_called_once()
        call_args = mock_enforce_plan_limits.call_args
        assert call_args[0][0] == 123  # First arg should be user["id"]

    @pytest.mark.asyncio
    async def test_authenticated_stream_plan_limit_exceeded(self, mock_process_stream_completion):
        """Test that plan limit exceeded error is properly returned."""
        from src.routes.chat import stream_generator

        # Mock enforce_plan_limits to return not allowed
        with patch("src.routes.chat.enforce_plan_limits") as mock_enforce:
            mock_enforce.return_value = {"allowed": False, "reason": "Monthly limit exceeded"}

            chunks = create_mock_stream_chunks(["Hello"])
            user = {"id": 123, "credits": 100.0, "environment_tag": "live"}

            stream_output = []
            async for chunk in stream_generator(
                mock_stream_generator(chunks),
                user=user,
                api_key="test-key",
                model="gpt-3.5-turbo",
                trial={"is_trial": False, "is_expired": False},
                environment_tag="live",
                session_id=None,
                messages=[{"role": "user", "content": "Hello"}],
                rate_limit_mgr=None,
                provider="openrouter",
                tracker=None,
                is_anonymous=False,
            ):
                stream_output.append(chunk)

            # Verify error chunk is in output
            error_chunks = [c for c in stream_output if "plan_limit_exceeded" in c]
            assert len(error_chunks) == 1
            error_data = json.loads(error_chunks[0][6:].strip())
            assert error_data["error"]["type"] == "plan_limit_exceeded"
            assert "Monthly limit exceeded" in error_data["error"]["message"]
