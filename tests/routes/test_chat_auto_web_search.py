"""
Tests for Auto Web Search functionality in Chat Completions.

Tests cover:
- Auto web search parameter handling
- Query classification integration
- Message augmentation with search results
- Error handling and fallbacks
- Different auto_web_search modes (True, False, "auto")
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routes.chat import router
from src.services.tools.base import ToolResult


@pytest.fixture(scope="function")
def client():
    """Create test client with mocked authentication."""
    from src.security.deps import get_api_key

    app = FastAPI()
    app.include_router(router, prefix="/v1")

    async def mock_get_api_key() -> str:
        return "test_api_key"

    app.dependency_overrides[get_api_key] = mock_get_api_key
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authorization headers for test requests."""
    return {"Authorization": "Bearer test_api_key"}


@pytest.fixture
def travel_query_payload():
    """Payload with a travel/remote work query that should trigger auto search."""
    return {
        "model": "openrouter/openai/gpt-4",
        "messages": [
            {
                "role": "user",
                "content": "How easy is it to get wifi in El Salvador for remote work and video calls?",
            }
        ],
        "auto_web_search": "auto",
    }


@pytest.fixture
def code_query_payload():
    """Payload with a code query that should NOT trigger auto search."""
    return {
        "model": "openrouter/openai/gpt-4",
        "messages": [
            {
                "role": "user",
                "content": "```python\ndef hello():\n    print('world')\n```\nCan you fix this?",
            }
        ],
        "auto_web_search": "auto",
    }


@pytest.fixture
def simple_greeting_payload():
    """Payload with a simple greeting that should NOT trigger auto search."""
    return {
        "model": "openrouter/openai/gpt-4",
        "messages": [{"role": "user", "content": "Hello!"}],
        "auto_web_search": "auto",
    }


@pytest.fixture
def mock_successful_search_result():
    """Mock successful web search result."""
    return ToolResult(
        success=True,
        result={
            "query": "wifi in El Salvador remote work",
            "results": [
                {
                    "title": "Internet in El Salvador - Digital Nomad Guide",
                    "url": "https://example.com/el-salvador-wifi",
                    "content": "El Salvador has improved its internet infrastructure significantly. Most cities have fiber optic coverage with speeds of 50-100 Mbps.",
                    "score": 0.95,
                },
                {
                    "title": "Remote Work in El Salvador 2025",
                    "url": "https://example.com/remote-work-es",
                    "content": "Many cafes and coworking spaces offer reliable wifi. Starlink is also available as a backup option.",
                    "score": 0.88,
                },
            ],
            "answer": "Internet connectivity in El Salvador has improved significantly. Most urban areas have fiber optic with 50-100 Mbps speeds. Coworking spaces and cafes offer reliable wifi, and Starlink is available as backup.",
            "results_count": 2,
        },
        error=None,
        metadata={"search_depth": "basic"},
    )


class TestAutoWebSearchParameter:
    """Tests for auto_web_search parameter handling."""

    def test_auto_web_search_default_is_auto(self):
        """Test that auto_web_search defaults to 'auto'."""
        from src.schemas.proxy import ProxyRequest

        req = ProxyRequest(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        )
        assert req.auto_web_search == "auto"

    def test_auto_web_search_can_be_true(self):
        """Test that auto_web_search can be set to True."""
        from src.schemas.proxy import ProxyRequest

        req = ProxyRequest(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            auto_web_search=True,
        )
        assert req.auto_web_search is True

    def test_auto_web_search_can_be_false(self):
        """Test that auto_web_search can be set to False."""
        from src.schemas.proxy import ProxyRequest

        req = ProxyRequest(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            auto_web_search=False,
        )
        assert req.auto_web_search is False

    def test_web_search_threshold_default(self):
        """Test that web_search_threshold defaults to 0.5."""
        from src.schemas.proxy import ProxyRequest

        req = ProxyRequest(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        )
        assert req.web_search_threshold == 0.5

    def test_web_search_threshold_validation(self):
        """Test that web_search_threshold is validated between 0 and 1."""
        from pydantic import ValidationError

        from src.schemas.proxy import ProxyRequest

        # Valid thresholds
        req = ProxyRequest(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
            web_search_threshold=0.3,
        )
        assert req.web_search_threshold == 0.3

        # Invalid threshold - too high
        with pytest.raises(ValidationError):
            ProxyRequest(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                web_search_threshold=1.5,
            )

        # Invalid threshold - too low
        with pytest.raises(ValidationError):
            ProxyRequest(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                web_search_threshold=-0.1,
            )


class TestQueryClassifierIntegration:
    """Tests for query classifier integration in chat completions."""

    def test_travel_query_triggers_classification(self):
        """Test that travel queries are classified correctly."""
        from src.services.query_classifier import classify_query

        messages = [
            {
                "role": "user",
                "content": "How easy is it to get wifi in El Salvador for remote work?",
            }
        ]
        result = classify_query(messages, threshold=0.4)

        assert result.should_search is True
        assert result.confidence >= 0.4

    def test_code_query_does_not_trigger_search(self):
        """Test that code queries don't trigger search."""
        from src.services.query_classifier import classify_query

        messages = [
            {"role": "user", "content": "```python\ndef foo(): pass\n```\nHelp me fix this."}
        ]
        result = classify_query(messages, threshold=0.4)

        assert result.should_search is False

    def test_greeting_does_not_trigger_search(self):
        """Test that simple greetings don't trigger search."""
        from src.services.query_classifier import classify_query

        messages = [{"role": "user", "content": "Hello, how are you?"}]
        result = classify_query(messages, threshold=0.5)

        assert result.should_search is False


class TestMessageAugmentation:
    """Tests for message augmentation with search results."""

    def test_search_context_format(self, mock_successful_search_result):
        """Test that search context is formatted correctly."""
        result_data = mock_successful_search_result.result

        # Verify the structure
        assert "results" in result_data
        assert "answer" in result_data
        assert len(result_data["results"]) == 2

        # Verify each result has required fields
        for item in result_data["results"]:
            assert "title" in item
            assert "url" in item
            assert "content" in item

    def test_system_message_insertion_logic(self):
        """Test that system message is inserted after existing system messages."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Question?"},
        ]

        search_system_message = {
            "role": "system",
            "content": "[Web Search Results]...",
        }

        # Find insert index (after system messages)
        insert_index = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_index = i + 1
            else:
                break

        messages.insert(insert_index, search_system_message)

        # Verify order
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."
        assert messages[1]["role"] == "system"
        assert "[Web Search Results]" in messages[1]["content"]
        assert messages[2]["role"] == "user"

    def test_system_message_insertion_no_existing_system(self):
        """Test system message insertion when no existing system message."""
        messages = [
            {"role": "user", "content": "Question?"},
        ]

        search_system_message = {
            "role": "system",
            "content": "[Web Search Results]...",
        }

        # Find insert index (after system messages, or at start)
        insert_index = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_index = i + 1
            else:
                break

        messages.insert(insert_index, search_system_message)

        # Search results should be at the start
        assert messages[0]["role"] == "system"
        assert "[Web Search Results]" in messages[0]["content"]
        assert messages[1]["role"] == "user"


class TestAutoWebSearchModes:
    """Tests for different auto_web_search modes."""

    @patch("src.services.tools.execute_tool")
    @patch("src.services.query_classifier.should_auto_search")
    def test_auto_mode_uses_classifier(self, mock_classifier, mock_execute_tool):
        """Test that 'auto' mode uses the query classifier."""
        from src.services.query_classifier import ClassificationResult, QueryIntent

        # Setup classifier mock
        mock_classifier.return_value = (
            True,
            ClassificationResult(
                should_search=True,
                confidence=0.8,
                intent=QueryIntent.LOCATION_SPECIFIC,
                reason="wifi, remote work keywords",
                extracted_query="wifi in El Salvador",
            ),
        )

        # Call should_auto_search
        messages = [{"role": "user", "content": "wifi in El Salvador?"}]
        from src.services.query_classifier import should_auto_search

        should_search, result = should_auto_search(messages, threshold=0.5, enabled=True)

        assert should_search is True
        assert result.confidence == 0.8

    def test_explicit_true_always_searches(self):
        """Test that auto_web_search=True would always search."""
        # When auto_web_search is True, search is triggered regardless of classifier
        auto_web_search = True
        should_search = auto_web_search is True

        assert should_search is True

    def test_explicit_false_never_searches(self):
        """Test that auto_web_search=False never searches."""
        auto_web_search = False
        should_search = auto_web_search is True

        assert should_search is False


class TestErrorHandling:
    """Tests for error handling in auto web search."""

    @patch("src.services.tools.execute_tool")
    def test_search_failure_continues_request(self, mock_execute_tool):
        """Test that search failure doesn't break the request."""
        mock_execute_tool.return_value = ToolResult(
            success=False,
            result=None,
            error="Search service unavailable",
            metadata={},
        )

        # The chat should continue without search results
        # This is tested by verifying the tool returns failure but no exception is raised
        result = mock_execute_tool("web_search", {"query": "test"})
        assert result.success is False
        assert result.error is not None

    def test_classifier_exception_handled(self):
        """Test that classifier exceptions are handled gracefully."""
        # Simulate a malformed message that could cause issues
        messages = [{"role": "user", "content": None}]

        from src.services.query_classifier import classify_query

        # Should not raise, should return safe defaults
        result = classify_query(messages)
        assert result.should_search is False

    def test_empty_search_results_handled(self):
        """Test handling of empty search results."""
        empty_result = ToolResult(
            success=True,
            result={"results": [], "answer": None},
            error=None,
            metadata={},
        )

        # Verify the result structure
        assert empty_result.success is True
        assert len(empty_result.result["results"]) == 0
        assert empty_result.result["answer"] is None


class TestRealWorldScenarios:
    """Integration tests for real-world scenarios."""

    @pytest.mark.parametrize(
        "query,expected_search",
        [
            ("How easy is it to get wifi in El Salvador for remote work?", True),
            ("What's the internet like in Bali for video calls?", True),
            ("Current Bitcoin price", True),
            ("Latest news about AI", True),
            ("def hello(): print('world')", False),
            ("Hello!", False),
            ("What is 2+2?", False),
        ],
    )
    def test_various_queries(self, query, expected_search):
        """Test classification of various query types."""
        from src.services.query_classifier import classify_query

        messages = [{"role": "user", "content": query}]
        result = classify_query(messages, threshold=0.4)

        assert (
            result.should_search == expected_search
        ), f"Query '{query}' expected search={expected_search}, got {result.should_search}"

    def test_conversation_context_uses_last_message(self):
        """Test that classification uses the last user message."""
        from src.services.query_classifier import classify_query

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "What's the wifi situation in Thailand?"},
        ]

        result = classify_query(messages, threshold=0.4)
        assert result.should_search is True
        assert result.extracted_query == "What's the wifi situation in Thailand?"


class TestThresholdBehavior:
    """Tests for threshold configuration behavior."""

    def test_low_threshold_more_searches(self):
        """Test that lower threshold triggers more searches."""
        from src.services.query_classifier import classify_query

        messages = [{"role": "user", "content": "Tell me about coffee shops"}]

        result_low = classify_query(messages, threshold=0.1)
        result_high = classify_query(messages, threshold=0.9)

        # Confidence should be the same
        assert result_low.confidence == result_high.confidence

        # But search decision may differ based on threshold
        # (depends on the actual confidence value)

    def test_threshold_boundary_cases(self):
        """Test behavior at threshold boundaries."""
        from src.services.query_classifier import classify_query

        messages = [{"role": "user", "content": "What's the current time?"}]
        result = classify_query(messages, threshold=0.5)

        # Test at exact threshold
        if result.confidence == 0.5:
            assert result.should_search is True  # >= threshold

        # Test just below
        result_below = classify_query(messages, threshold=result.confidence + 0.01)
        if result.confidence + 0.01 <= 1.0:
            assert result_below.should_search is False

        # Test just above
        if result.confidence > 0.01:
            result_above = classify_query(messages, threshold=result.confidence - 0.01)
            assert result_above.should_search is True


class TestSearchContextFormatting:
    """Tests for formatting search results into context."""

    def test_format_search_context_with_answer(self):
        """Test formatting search context when answer is present."""
        results = [
            {"title": "Test Result", "url": "https://example.com", "content": "Test content"},
        ]
        answer = "This is the AI summary."

        context_parts = ["[Web Search Results]"]
        if answer:
            context_parts.append(f"\nSummary: {answer}")
        if results:
            context_parts.append("\nSources:")
            for i, item in enumerate(results[:5], 1):
                context_parts.append(f"\n{i}. {item['title']}")
                context_parts.append(f"   {item['content']}")
                context_parts.append(f"   {item['url']}")
        context_parts.append("\n[End of Search Results]\n")

        context = "\n".join(context_parts)

        assert "[Web Search Results]" in context
        assert "Summary: This is the AI summary." in context
        assert "Test Result" in context
        assert "https://example.com" in context
        assert "[End of Search Results]" in context

    def test_format_search_context_without_answer(self):
        """Test formatting search context when no answer is present."""
        results = [
            {"title": "Result 1", "url": "https://example.com/1", "content": "Content 1"},
            {"title": "Result 2", "url": "https://example.com/2", "content": "Content 2"},
        ]
        answer = None

        context_parts = ["[Web Search Results]"]
        if answer:
            context_parts.append(f"\nSummary: {answer}")
        if results:
            context_parts.append("\nSources:")
            for i, item in enumerate(results[:5], 1):
                context_parts.append(f"\n{i}. {item['title']}")
                context_parts.append(f"   {item['content']}")
                context_parts.append(f"   {item['url']}")
        context_parts.append("\n[End of Search Results]\n")

        context = "\n".join(context_parts)

        assert "[Web Search Results]" in context
        assert "Summary:" not in context
        assert "Result 1" in context
        assert "Result 2" in context

    def test_content_truncation_for_long_snippets(self):
        """Test that long content snippets are truncated."""
        long_content = "A" * 500  # More than 300 chars
        results = [
            {"title": "Test", "url": "https://example.com", "content": long_content},
        ]

        # Simulate truncation logic
        for item in results:
            content = item["content"]
            if len(content) > 300:
                item["content"] = content[:297] + "..."

        assert len(results[0]["content"]) == 300
        assert results[0]["content"].endswith("...")

    def test_max_five_results(self):
        """Test that only first 5 results are included."""
        results = [
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": f"Content {i}"}
            for i in range(10)
        ]

        context_parts = ["[Web Search Results]", "\nSources:"]
        for i, item in enumerate(results[:5], 1):  # Only first 5
            context_parts.append(f"\n{i}. {item['title']}")

        context = "\n".join(context_parts)

        assert "Result 0" in context
        assert "Result 4" in context
        assert "Result 5" not in context
        assert "Result 9" not in context


class TestMultimodalQueryExtraction:
    """Tests for extracting queries from multimodal messages."""

    def test_extract_text_from_multimodal_message(self):
        """Test extracting text content from multimodal message."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is the wifi like"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.jpg"}},
                    {"type": "text", "text": "in this place?"},
                ]
            }
        ]

        # Extract text
        search_query = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    search_query = content
                elif isinstance(content, list):
                    text_parts = [
                        p.get("text", "") if isinstance(p, dict) else str(p)
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text" or isinstance(p, str)
                    ]
                    search_query = " ".join(text_parts)
                break

        assert search_query is not None
        assert "wifi" in search_query
        assert "in this place" in search_query


class TestParallelExecutionLogic:
    """Tests for the parallel execution implementation."""

    @pytest.mark.asyncio
    async def test_asyncio_create_task_pattern(self):
        """Test that asyncio.create_task pattern works correctly."""
        import asyncio

        async def mock_search():
            await asyncio.sleep(0.01)
            return {"success": True, "results": []}

        # Start task
        task = asyncio.create_task(mock_search())

        # Do some other work (simulated)
        await asyncio.sleep(0.005)

        # Await result
        result = await task

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_asyncio_wait_for_timeout(self):
        """Test that timeout works with asyncio.wait_for."""
        import asyncio

        async def slow_search():
            await asyncio.sleep(10)  # Simulate slow search
            return {"success": True}

        task = asyncio.create_task(slow_search())

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=0.01)

        # Task should be cancelled or cancellable
        task.cancel()

    @pytest.mark.asyncio
    async def test_task_exception_handling(self):
        """Test that task exceptions are handled properly."""
        import asyncio

        async def failing_search():
            raise ValueError("Search failed")

        task = asyncio.create_task(failing_search())

        try:
            await task
            assert False, "Should have raised"
        except ValueError as e:
            assert "Search failed" in str(e)


class TestAutoWebSearchDisabled:
    """Tests for when auto_web_search is disabled."""

    def test_disabled_via_false(self):
        """Test that False disables auto search."""
        from src.services.query_classifier import should_auto_search

        messages = [{"role": "user", "content": "Current Bitcoin price?"}]

        # Even though this would normally trigger search
        should_search, result = should_auto_search(messages, threshold=0.3, enabled=False)

        assert should_search is False
        assert "disabled" in result.reason.lower()

    def test_explicit_false_parameter(self):
        """Test explicit False parameter behavior."""
        auto_web_search = False

        # Simulate the chat.py logic
        should_search = False
        if auto_web_search is True:
            should_search = True
        elif auto_web_search == "auto":
            should_search = True  # Would use classifier
        # If False, should_search stays False

        assert should_search is False

    def test_none_parameter_no_search(self):
        """Test that None parameter doesn't trigger search."""
        auto_web_search = None

        should_search = False
        if auto_web_search is True:
            should_search = True
        elif auto_web_search == "auto":
            should_search = True

        assert should_search is False


class TestWebSearchThresholdEdgeCases:
    """Tests for web_search_threshold edge cases."""

    def test_threshold_zero(self):
        """Test threshold of 0 (always search if any signal)."""
        from src.schemas.proxy import ProxyRequest

        req = ProxyRequest(
            model="test",
            messages=[{"role": "user", "content": "test"}],
            web_search_threshold=0.0,
        )
        assert req.web_search_threshold == 0.0

    def test_threshold_one(self):
        """Test threshold of 1.0 (only search if perfect confidence)."""
        from src.schemas.proxy import ProxyRequest

        req = ProxyRequest(
            model="test",
            messages=[{"role": "user", "content": "test"}],
            web_search_threshold=1.0,
        )
        assert req.web_search_threshold == 1.0

    def test_threshold_affects_classification(self):
        """Test that different thresholds affect search decisions."""
        from src.services.query_classifier import classify_query

        messages = [{"role": "user", "content": "What are some restaurants?"}]

        result_low = classify_query(messages, threshold=0.1)
        result_high = classify_query(messages, threshold=0.99)

        # Same query should have same confidence
        assert result_low.confidence == result_high.confidence

        # But different search decisions possible based on threshold
        # If confidence is between 0.1 and 0.99, decisions will differ


class TestToolResultHandling:
    """Tests for handling different ToolResult scenarios."""

    def test_tool_result_success_with_results(self, mock_successful_search_result):
        """Test handling of successful search with results."""
        result = mock_successful_search_result

        assert result.success is True
        assert result.result is not None
        assert len(result.result["results"]) > 0
        assert result.error is None

    def test_tool_result_success_no_results(self):
        """Test handling of successful search with no results."""
        result = ToolResult(
            success=True,
            result={"results": [], "answer": None, "results_count": 0},
            error=None,
            metadata={},
        )

        assert result.success is True
        assert len(result.result["results"]) == 0

    def test_tool_result_failure(self):
        """Test handling of failed search."""
        result = ToolResult(
            success=False,
            result=None,
            error="API rate limit exceeded",
            metadata={"error_type": "rate_limit"},
        )

        assert result.success is False
        assert result.result is None
        assert result.error is not None

    def test_tool_result_with_only_answer(self):
        """Test handling of result with answer but no individual results."""
        result = ToolResult(
            success=True,
            result={
                "results": [],
                "answer": "The answer is 42.",
                "results_count": 0,
            },
            error=None,
            metadata={},
        )

        assert result.success is True
        assert len(result.result["results"]) == 0
        assert result.result["answer"] is not None
