"""
Unit tests for the bug fix generator service.

Tests cover:
- API key validation
- Prompt sanitization and length validation
- Retry logic with mocked failures
- Error analysis with mocked Claude API
- Fix generation with mocked responses
- Request/response logging
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from datetime import datetime, timezone
import httpx

from src.services.bug_fix_generator import (
    BugFixGenerator,
    BugFix,
    MAX_PROMPT_LENGTH,
    MAX_ERROR_MESSAGE_LENGTH,
)
from src.services.error_monitor import ErrorPattern, ErrorCategory, ErrorSeverity


@pytest.fixture
def mock_anthropic_key(monkeypatch):
    """Mock ANTHROPIC_API_KEY in Config class."""
    from src.config import config
    monkeypatch.setattr(config.Config, "ANTHROPIC_API_KEY", "sk-ant-test-key-1234567890")
    monkeypatch.setattr(config.Config, "ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    return "sk-ant-test-key-1234567890"


@pytest.fixture
def error_pattern():
    """Create a sample error pattern for testing."""
    return ErrorPattern(
        error_type="HTTPException",
        message="Provider 'openrouter' returned an error: Circuit breaker is OPEN",
        category=ErrorCategory.PROVIDER_ERROR,
        severity=ErrorSeverity.HIGH,
        file="src/services/openrouter_client.py",
        line=123,
        function="make_request",
        stack_trace="Traceback (most recent call last):\n  File ...",
        timestamp=datetime.now(timezone.utc),
        count=5,
        fixable=True,
    )


class TestBugFixGeneratorInitialization:
    """Test bug fix generator initialization and configuration."""

    def test_init_without_api_key(self, monkeypatch):
        """Test that initialization fails without ANTHROPIC_API_KEY."""
        from src.config import config
        monkeypatch.setattr(config.Config, "ANTHROPIC_API_KEY", None)

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not configured"):
            BugFixGenerator()

    def test_init_with_api_key(self, mock_anthropic_key):
        """Test successful initialization with API key."""
        generator = BugFixGenerator()
        assert generator.anthropic_key == "sk-ant-test-key-1234567890"
        assert generator.anthropic_url == "https://api.anthropic.com/v1"
        assert generator.generated_fixes == {}
        assert generator.api_key_validated is False

    def test_init_with_invalid_key_format(self, monkeypatch, caplog):
        """Test warning when API key doesn't start with sk-ant-."""
        from src.config import config
        monkeypatch.setattr(config.Config, "ANTHROPIC_API_KEY", "invalid-key-format")

        with caplog.at_level("WARNING"):
            generator = BugFixGenerator()
            assert "does not start with 'sk-ant-'" in caplog.text

    def test_init_with_github_token(self, mock_anthropic_key, monkeypatch):
        """Test initialization with GitHub token."""
        from src.config import config
        monkeypatch.setattr(config.Config, "GITHUB_TOKEN", "ghp_test_token")

        generator = BugFixGenerator()
        assert generator.github_token == "ghp_test_token"

    def test_init_with_custom_model(self, mock_anthropic_key, monkeypatch):
        """Test initialization with custom ANTHROPIC_MODEL."""
        from src.config import config
        monkeypatch.setattr(config.Config, "ANTHROPIC_MODEL", "claude-opus-4-1-20250805")

        generator = BugFixGenerator()
        assert generator.anthropic_model == "claude-opus-4-1-20250805"


class TestAPIKeyValidation:
    """Test API key validation logic."""

    @pytest.mark.asyncio
    async def test_validate_api_key_success(self, mock_anthropic_key):
        """Test successful API key validation."""
        generator = BugFixGenerator()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [{"text": "test"}],
            "id": "msg_123",
            "model": "claude-3-5-sonnet-20241022",
        }

        with patch.object(httpx.AsyncClient, "post", return_value=mock_response):
            await generator.initialize()
            assert generator.api_key_validated is True

    @pytest.mark.asyncio
    async def test_validate_api_key_401_unauthorized(self, mock_anthropic_key):
        """Test validation fails with 401 Unauthorized."""
        generator = BugFixGenerator()

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "Invalid API key"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=Mock(),
            response=mock_response,
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_response):
            await generator.initialize()
            # Should log error but not crash
            assert generator.api_key_validated is False

    @pytest.mark.asyncio
    async def test_validate_api_key_400_bad_request(self, mock_anthropic_key):
        """Test validation fails with 400 Bad Request."""
        generator = BugFixGenerator()

        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request",
            request=Mock(),
            response=mock_response,
        )

        with patch.object(httpx.AsyncClient, "post", return_value=mock_response):
            await generator.initialize()
            assert generator.api_key_validated is False

    @pytest.mark.asyncio
    async def test_validate_api_key_timeout(self, mock_anthropic_key, caplog):
        """Test validation handles timeout gracefully."""
        generator = BugFixGenerator()

        with patch.object(
            httpx.AsyncClient,
            "post",
            side_effect=httpx.TimeoutException("Timeout"),
        ):
            with caplog.at_level("WARNING"):
                await generator.initialize()
                assert "timed out" in caplog.text
                # Timeout doesn't prevent initialization, but key is not validated
                # The validation process continues despite timeout


class TestPromptSanitization:
    """Test prompt sanitization and length validation."""

    def test_sanitize_text_short_text(self, mock_anthropic_key):
        """Test sanitizing normal-length text."""
        generator = BugFixGenerator()
        text = "Short error message"
        result = generator._sanitize_text(text)
        assert result == text

    def test_sanitize_text_long_text(self, mock_anthropic_key):
        """Test sanitizing text exceeding max length."""
        generator = BugFixGenerator()
        long_text = "A" * (MAX_ERROR_MESSAGE_LENGTH + 1000)
        result = generator._sanitize_text(long_text)

        assert len(result) < len(long_text)
        assert "truncated from" in result
        assert result.startswith("A" * MAX_ERROR_MESSAGE_LENGTH)

    def test_sanitize_text_with_null_bytes(self, mock_anthropic_key):
        """Test removing null bytes from text."""
        generator = BugFixGenerator()
        text_with_nulls = "Error\x00message\x00here"
        result = generator._sanitize_text(text_with_nulls)

        assert "\x00" not in result
        assert result == "Errormessagehere"

    def test_sanitize_text_empty(self, mock_anthropic_key):
        """Test sanitizing empty text."""
        generator = BugFixGenerator()
        result = generator._sanitize_text("")
        assert result == ""

    def test_sanitize_text_none(self, mock_anthropic_key):
        """Test sanitizing None."""
        generator = BugFixGenerator()
        result = generator._sanitize_text(None)
        assert result == ""

    def test_prepare_prompt_normal_length(self, mock_anthropic_key):
        """Test preparing normal-length prompt."""
        generator = BugFixGenerator()
        prompt = "Analyze this error: ..."
        result = generator._prepare_prompt(prompt)
        assert result == prompt

    def test_prepare_prompt_exceeds_max(self, mock_anthropic_key, caplog):
        """Test preparing prompt that exceeds max length."""
        generator = BugFixGenerator()
        long_prompt = "A" * (MAX_PROMPT_LENGTH + 1000)

        with caplog.at_level("WARNING"):
            result = generator._prepare_prompt(long_prompt)
            assert "Prompt too long" in caplog.text
            assert len(result) <= MAX_PROMPT_LENGTH + 100  # Allow for truncation message


class TestErrorAnalysis:
    """Test error analysis with mocked Claude API."""

    @pytest.mark.asyncio
    async def test_analyze_error_success(self, mock_anthropic_key, error_pattern):
        """Test successful error analysis."""
        generator = BugFixGenerator()
        await generator.initialize()

        mock_response = {
            "content": [
                {
                    "text": "Root cause: Circuit breaker is preventing requests...",
                    "type": "text",
                }
            ],
            "id": "msg_123",
            "model": "claude-3-5-sonnet-20241022",
        }

        with patch.object(
            generator,
            "_make_claude_request",
            return_value=mock_response,
        ):
            analysis = await generator.analyze_error(error_pattern)
            assert "Root cause" in analysis
            assert "Circuit breaker" in analysis

    @pytest.mark.asyncio
    async def test_analyze_error_no_content(self, mock_anthropic_key, error_pattern):
        """Test error analysis with no content in response."""
        generator = BugFixGenerator()
        await generator.initialize()

        mock_response = {"id": "msg_123", "model": "claude-3-5-sonnet-20241022"}

        with patch.object(
            generator,
            "_make_claude_request",
            return_value=mock_response,
        ):
            analysis = await generator.analyze_error(error_pattern)
            assert "failed" in analysis.lower()

    @pytest.mark.asyncio
    async def test_analyze_error_400_bad_request(self, mock_anthropic_key, error_pattern):
        """Test error analysis with 400 error."""
        generator = BugFixGenerator()
        await generator.initialize()

        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Invalid request"

        with patch.object(
            generator,
            "_make_claude_request",
            side_effect=httpx.HTTPStatusError(
                "400 Bad Request",
                request=Mock(),
                response=mock_response,
            ),
        ):
            analysis = await generator.analyze_error(error_pattern)
            assert "400" in analysis
            assert "failed" in analysis.lower()

    @pytest.mark.asyncio
    async def test_analyze_error_sanitizes_long_messages(
        self, mock_anthropic_key
    ):
        """Test that error analysis sanitizes long error messages."""
        generator = BugFixGenerator()
        await generator.initialize()

        # Create error pattern with very long message
        long_error = ErrorPattern(
            error_type="HTTPException",
            message="Error: " + ("A" * 20000),  # Exceeds MAX_ERROR_MESSAGE_LENGTH
            category=ErrorCategory.PROVIDER_ERROR,
            severity=ErrorSeverity.HIGH,
            file="test.py",
            line=1,
            function="test",
            stack_trace="Traceback..." + ("B" * 20000),
            timestamp=datetime.now(timezone.utc),
            count=1,
        )

        mock_response = {
            "content": [{"text": "Analysis complete", "type": "text"}],
            "id": "msg_123",
        }

        with patch.object(
            generator,
            "_make_claude_request",
            return_value=mock_response,
        ) as mock_request:
            await generator.analyze_error(long_error)

            # Verify the prompt was sanitized
            call_args = mock_request.call_args
            prompt = call_args[0][0]  # First positional argument
            assert "truncated from" in prompt


class TestFixGeneration:
    """Test fix generation with mocked Claude API."""

    @pytest.mark.asyncio
    async def test_generate_fix_success(self, mock_anthropic_key, error_pattern):
        """Test successful fix generation."""
        generator = BugFixGenerator()
        await generator.initialize()

        # Mock the analysis step
        analysis_response = {
            "content": [{"text": "Root cause analysis...", "type": "text"}],
        }

        # Mock the fix generation step
        fix_response = {
            "content": [
                {
                    "text": """
                    {
                        "title": "Fix circuit breaker",
                        "description": "Add retry logic",
                        "explanation": "This fixes the issue by...",
                        "changes": [
                            {
                                "file": "src/services/openrouter_client.py",
                                "type": "modify",
                                "change_description": "Add retry logic",
                                "code": "def make_request():\\n    # Add retry logic"
                            }
                        ]
                    }
                    """,
                    "type": "text",
                }
            ],
        }

        with patch.object(
            generator,
            "_make_claude_request",
            side_effect=[analysis_response, fix_response],
        ):
            fix = await generator.generate_fix(error_pattern)

            assert fix is not None
            assert fix.error_message == error_pattern.message
            assert fix.error_category == error_pattern.category.value
            assert len(fix.files_affected) == 1
            assert "openrouter_client.py" in fix.files_affected[0]

    @pytest.mark.asyncio
    async def test_generate_fix_analysis_fails(self, mock_anthropic_key, error_pattern):
        """Test fix generation skips when analysis fails."""
        generator = BugFixGenerator()
        await generator.initialize()

        # Mock analysis failure
        with patch.object(
            generator,
            "analyze_error",
            return_value="Error analysis failed: 400 - Bad Request",
        ):
            fix = await generator.generate_fix(error_pattern)
            assert fix is None

    @pytest.mark.asyncio
    async def test_generate_fix_invalid_json(self, mock_anthropic_key, error_pattern):
        """Test fix generation handles invalid JSON response."""
        generator = BugFixGenerator()
        await generator.initialize()

        analysis_response = {
            "content": [{"text": "Analysis complete", "type": "text"}],
        }

        fix_response = {
            "content": [{"text": "This is not valid JSON", "type": "text"}],
        }

        with patch.object(
            generator,
            "_make_claude_request",
            side_effect=[analysis_response, fix_response],
        ):
            fix = await generator.generate_fix(error_pattern)
            assert fix is None

    @pytest.mark.asyncio
    async def test_generate_fix_no_changes(self, mock_anthropic_key, error_pattern):
        """Test fix generation with empty changes array."""
        generator = BugFixGenerator()
        await generator.initialize()

        analysis_response = {
            "content": [{"text": "Analysis complete", "type": "text"}],
        }

        fix_response = {
            "content": [
                {
                    "text": '{"title": "Fix", "description": "Desc", "changes": []}',
                    "type": "text",
                }
            ],
        }

        with patch.object(
            generator,
            "_make_claude_request",
            side_effect=[analysis_response, fix_response],
        ):
            fix = await generator.generate_fix(error_pattern)
            assert fix is not None
            assert len(fix.files_affected) == 0


class TestRetryLogic:
    """Test retry logic with exponential backoff."""

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, mock_anthropic_key):
        """Test that requests are retried on timeout."""
        generator = BugFixGenerator()
        await generator.initialize()

        # Create a mock that fails twice then succeeds
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.TimeoutException("Timeout")
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "content": [{"text": "Success", "type": "text"}]
            }
            return mock_response

        with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
            result = await generator._make_claude_request("test prompt")
            assert result["content"][0]["text"] == "Success"
            assert call_count == 3  # Failed twice, succeeded on third try

    @pytest.mark.asyncio
    async def test_retry_max_attempts(self, mock_anthropic_key):
        """Test that retry stops after max attempts."""
        generator = BugFixGenerator()
        await generator.initialize()

        with patch.object(
            httpx.AsyncClient,
            "post",
            side_effect=httpx.TimeoutException("Timeout"),
        ):
            with pytest.raises(httpx.TimeoutException):
                await generator._make_claude_request("test prompt")

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self, mock_anthropic_key):
        """Test that 400 errors are not retried."""
        generator = BugFixGenerator()
        await generator.initialize()

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = Mock()
            mock_response.status_code = 400
            mock_response.text = "Bad request"
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "400 Bad Request",
                request=Mock(),
                response=mock_response,
            )
            return mock_response

        with patch.object(httpx.AsyncClient, "post", side_effect=mock_post):
            with pytest.raises(httpx.HTTPStatusError):
                await generator._make_claude_request("test prompt")
            assert call_count == 1  # Should not retry


class TestRequestLogging:
    """Test request/response logging with correlation IDs."""

    @pytest.mark.asyncio
    async def test_request_logging_success(self, mock_anthropic_key, caplog):
        """Test that successful requests are logged."""
        generator = BugFixGenerator()
        await generator.initialize()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": [{"text": "Success", "type": "text"}]
        }

        with caplog.at_level("DEBUG"):
            with patch.object(httpx.AsyncClient, "post", return_value=mock_response):
                await generator._make_claude_request("test prompt", request_id="test123")

            # Check for correlation ID in logs
            assert any("test123" in record.message for record in caplog.records)
            assert any("Sending request to Claude API" in record.message for record in caplog.records)
            assert any("Successfully received response" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_request_logging_error(self, mock_anthropic_key, caplog):
        """Test that errors are logged with details."""
        generator = BugFixGenerator()
        await generator.initialize()

        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request error"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request",
            request=Mock(),
            response=mock_response,
        )

        with caplog.at_level("ERROR"):
            with patch.object(httpx.AsyncClient, "post", return_value=mock_response):
                with pytest.raises(httpx.HTTPStatusError):
                    await generator._make_claude_request("test prompt", request_id="err456")

            # Check for detailed error logging
            assert any("err456" in record.message for record in caplog.records)
            assert any("400" in record.message for record in caplog.records)


class TestBugFixDataClass:
    """Test BugFix data class functionality."""

    def test_bug_fix_to_dict(self):
        """Test BugFix.to_dict() serialization."""
        fix = BugFix(
            id="fix123",
            error_pattern_id="provider_error:Circuit breaker",
            error_message="Circuit breaker is OPEN",
            error_category="provider_error",
            analysis="Root cause analysis...",
            proposed_fix="Add retry logic",
            code_changes={"file.py": "code here"},
            files_affected=["file.py"],
            severity="high",
            generated_at=datetime(2026, 2, 11, 12, 0, 0, tzinfo=timezone.utc),
            pr_url="https://github.com/org/repo/pull/123",
            status="testing",
        )

        result = fix.to_dict()

        assert result["id"] == "fix123"
        assert result["error_category"] == "provider_error"
        assert result["severity"] == "high"
        assert result["pr_url"] == "https://github.com/org/repo/pull/123"
        assert result["status"] == "testing"
        assert isinstance(result["generated_at"], str)  # ISO format


class TestEdgeCases:
    """Test edge cases and error scenarios."""

    @pytest.mark.asyncio
    async def test_close_generator(self, mock_anthropic_key):
        """Test closing the generator properly."""
        generator = BugFixGenerator()
        await generator.initialize()

        assert generator.session is not None
        await generator.close()
        # Session should be closed (we can't easily test this without internal state)

    @pytest.mark.asyncio
    async def test_multiple_fixes_tracking(self, mock_anthropic_key, error_pattern):
        """Test that generated fixes are tracked."""
        generator = BugFixGenerator()
        await generator.initialize()

        analysis_response = {
            "content": [{"text": "Analysis", "type": "text"}],
        }

        fix_response = {
            "content": [
                {
                    "text": '{"title": "Fix", "description": "Desc", "changes": [{"file": "test.py", "code": "code"}]}',
                    "type": "text",
                }
            ],
        }

        with patch.object(
            generator,
            "_make_claude_request",
            side_effect=[analysis_response, fix_response],
        ):
            fix = await generator.generate_fix(error_pattern)

            assert fix is not None
            assert fix.id in generator.generated_fixes
            assert generator.generated_fixes[fix.id] == fix
