"""
Tests for the Query Classifier Service.

Tests cover:
- Query classification for different types of queries
- Keyword detection (current info, location, travel destinations)
- Pattern matching (factual questions, code queries)
- Confidence scoring
- Edge cases and boundary conditions
"""

import pytest

from src.services.query_classifier import (
    CODE_PATTERNS,
    CURRENT_INFO_KEYWORDS,
    FACTUAL_QUESTION_PATTERNS,
    LOCATION_KEYWORDS,
    TRAVEL_DESTINATIONS,
    ClassificationResult,
    QueryIntent,
    _contains_keywords,
    _extract_user_query,
    _is_code_query,
    _matches_patterns,
    _normalize_text,
    classify_query,
    should_auto_search,
)


class TestNormalizeText:
    """Tests for text normalization."""

    def test_lowercase(self):
        """Test that text is lowercased."""
        assert _normalize_text("HELLO WORLD") == "hello world"

    def test_strip_whitespace(self):
        """Test that whitespace is stripped."""
        assert _normalize_text("  hello  ") == "hello"

    def test_combined(self):
        """Test combined normalization."""
        assert _normalize_text("  HELLO World  ") == "hello world"


class TestContainsKeywords:
    """Tests for keyword detection."""

    def test_single_keyword_found(self):
        """Test detection of single keyword."""
        found, keywords = _contains_keywords("What is the current price?", {"current", "latest"})
        assert found is True
        assert "current" in keywords

    def test_multiple_keywords_found(self):
        """Test detection of multiple keywords."""
        found, keywords = _contains_keywords(
            "What is the latest news about current events?", {"latest", "current", "news"}
        )
        assert found is True
        assert len(keywords) == 3

    def test_no_keywords_found(self):
        """Test when no keywords are found."""
        found, keywords = _contains_keywords("Hello, how are you?", {"current", "latest"})
        assert found is False
        assert len(keywords) == 0

    def test_case_insensitive(self):
        """Test case-insensitive matching."""
        found, keywords = _contains_keywords("CURRENT NEWS", {"current", "news"})
        assert found is True


class TestMatchesPatterns:
    """Tests for pattern matching."""

    def test_factual_question_pattern(self):
        """Test factual question pattern detection."""
        matches, pattern = _matches_patterns(
            "How easy is it to get wifi in Costa Rica?", FACTUAL_QUESTION_PATTERNS
        )
        assert matches is True

    def test_what_is_the_best_pattern(self):
        """Test 'what is the best' pattern."""
        matches, pattern = _matches_patterns(
            "What is the best coworking space in Lisbon?", FACTUAL_QUESTION_PATTERNS
        )
        assert matches is True

    def test_code_pattern_function(self):
        """Test code pattern detection for functions."""
        matches, pattern = _matches_patterns("def hello_world():", CODE_PATTERNS)
        assert matches is True

    def test_code_pattern_import(self):
        """Test code pattern detection for imports."""
        matches, pattern = _matches_patterns("import numpy as np", CODE_PATTERNS)
        assert matches is True

    def test_no_pattern_match(self):
        """Test when no patterns match."""
        matches, pattern = _matches_patterns("Hello, nice to meet you!", FACTUAL_QUESTION_PATTERNS)
        assert matches is False


class TestIsCodeQuery:
    """Tests for code query detection."""

    def test_code_block(self):
        """Test detection of code block."""
        assert _is_code_query("Here's some code:\n```python\nprint('hello')\n```") is True

    def test_python_function(self):
        """Test detection of Python function."""
        assert _is_code_query("def calculate_sum(a, b):") is True

    def test_javascript_function(self):
        """Test detection of JavaScript function."""
        assert _is_code_query("function handleClick(event) {") is True

    def test_import_statement(self):
        """Test detection of import statement."""
        assert _is_code_query("from fastapi import APIRouter") is True

    def test_not_code(self):
        """Test non-code query."""
        assert _is_code_query("How easy is it to get wifi in El Salvador?") is False


class TestExtractUserQuery:
    """Tests for extracting user query from messages."""

    def test_single_user_message(self):
        """Test extraction from single user message."""
        messages = [{"role": "user", "content": "Hello, how are you?"}]
        assert _extract_user_query(messages) == "Hello, how are you?"

    def test_multiple_messages(self):
        """Test extraction from conversation with multiple messages."""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ]
        assert _extract_user_query(messages) == "Second question"

    def test_multimodal_content(self):
        """Test extraction from multimodal content."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}},
                ],
            }
        ]
        assert _extract_user_query(messages) == "What is in this image?"

    def test_no_user_message(self):
        """Test when there's no user message."""
        messages = [{"role": "system", "content": "You are a helpful assistant."}]
        assert _extract_user_query(messages) is None

    def test_empty_messages(self):
        """Test with empty messages list."""
        assert _extract_user_query([]) is None


class TestClassifyQuery:
    """Tests for query classification."""

    def test_wifi_travel_query_el_salvador(self):
        """Test classification of wifi/travel query for El Salvador."""
        messages = [
            {
                "role": "user",
                "content": "How easy is it to get wifi in El Salvador for remote work and video calls?",
            }
        ]
        result = classify_query(messages)

        assert result.should_search is True
        assert result.confidence >= 0.5
        assert result.intent == QueryIntent.LOCATION_SPECIFIC
        assert "wifi" in result.reason.lower() or "location" in result.reason.lower()

    def test_wifi_travel_query_thailand(self):
        """Test classification of wifi/travel query for Thailand."""
        messages = [
            {
                "role": "user",
                "content": "Is the internet connection reliable in Bali for video conferencing?",
            }
        ]
        result = classify_query(messages)

        assert result.should_search is True
        assert result.confidence >= 0.5
        assert result.intent == QueryIntent.LOCATION_SPECIFIC

    def test_cost_of_living_query(self):
        """Test classification of cost of living query."""
        messages = [
            {"role": "user", "content": "What is the cost of living in Lisbon for digital nomads?"}
        ]
        result = classify_query(messages)

        assert result.should_search is True
        assert result.confidence >= 0.5

    def test_current_events_query(self):
        """Test classification of current events query."""
        messages = [
            {"role": "user", "content": "What's the latest news about AI regulation in 2025?"}
        ]
        result = classify_query(messages)

        assert result.should_search is True
        assert result.confidence >= 0.4
        assert result.intent == QueryIntent.FACTUAL_CURRENT

    def test_price_query(self):
        """Test classification of price query."""
        messages = [{"role": "user", "content": "What is the current Bitcoin price in USD?"}]
        result = classify_query(messages)

        assert result.should_search is True
        assert result.confidence >= 0.5

    def test_weather_query(self):
        """Test classification of weather query."""
        messages = [{"role": "user", "content": "What's the weather forecast for Tokyo this week?"}]
        result = classify_query(messages)

        assert result.should_search is True

    def test_code_query_no_search(self):
        """Test that code queries don't trigger search."""
        messages = [
            {
                "role": "user",
                "content": "Can you help me with this code?\n```python\ndef hello():\n    print('world')\n```",
            }
        ]
        result = classify_query(messages)

        assert result.should_search is False
        assert result.intent == QueryIntent.CODE_TECHNICAL

    def test_import_statement_no_search(self):
        """Test that import statements don't trigger search."""
        messages = [
            {"role": "user", "content": "I'm getting an error with: from fastapi import APIRouter"}
        ]
        result = classify_query(messages)

        assert result.should_search is False
        assert result.intent == QueryIntent.CODE_TECHNICAL

    def test_simple_greeting_no_search(self):
        """Test that simple greetings don't trigger search."""
        messages = [{"role": "user", "content": "Hi there!"}]
        result = classify_query(messages, threshold=0.5)

        assert result.should_search is False
        assert result.confidence < 0.5

    def test_general_knowledge_low_confidence(self):
        """Test general knowledge questions have lower confidence."""
        messages = [{"role": "user", "content": "What is the capital of France?"}]
        result = classify_query(messages, threshold=0.5)

        # General knowledge doesn't need real-time search
        # Confidence should be lower as this is timeless information
        assert result.confidence < 0.7

    def test_empty_messages(self):
        """Test handling of empty messages."""
        result = classify_query([])

        assert result.should_search is False
        assert result.confidence == 0.0
        assert result.intent == QueryIntent.CONVERSATIONAL

    def test_threshold_adjustment(self):
        """Test that threshold affects search decision."""
        messages = [{"role": "user", "content": "What are good coworking spaces?"}]

        # With low threshold, might search
        result_low = classify_query(messages, threshold=0.2)

        # With high threshold, likely won't search
        result_high = classify_query(messages, threshold=0.9)

        assert result_low.confidence == result_high.confidence
        # The confidence stays the same, but should_search changes based on threshold
        if result_low.confidence >= 0.2:
            assert result_low.should_search is True
        if result_high.confidence < 0.9:
            assert result_high.should_search is False


class TestShouldAutoSearch:
    """Tests for the should_auto_search function."""

    def test_enabled_returns_result(self):
        """Test that enabled=True processes the query."""
        messages = [{"role": "user", "content": "What is the current price of gold?"}]
        should_search, result = should_auto_search(messages, threshold=0.3, enabled=True)

        assert isinstance(should_search, bool)
        assert isinstance(result, ClassificationResult)

    def test_disabled_returns_false(self):
        """Test that enabled=False always returns False."""
        messages = [{"role": "user", "content": "What is the current price of gold?"}]
        should_search, result = should_auto_search(messages, threshold=0.1, enabled=False)

        assert should_search is False
        assert result.should_search is False
        assert "disabled" in result.reason.lower()

    def test_remote_work_query(self):
        """Test remote work related query triggers search."""
        messages = [
            {
                "role": "user",
                "content": "Which countries are best for remote work with good internet?",
            }
        ]
        should_search, result = should_auto_search(messages, threshold=0.4, enabled=True)

        assert should_search is True
        assert result.confidence >= 0.4


class TestRealWorldQueries:
    """Integration tests with real-world query examples."""

    @pytest.mark.parametrize(
        "query,should_trigger",
        [
            # Should trigger search
            ("How easy is it to get wifi in El Salvador for remote work and video calls?", True),
            ("What's the internet speed like in Medellin Colombia?", True),
            ("Are there good coworking spaces in Lisbon?", True),
            ("What's the current visa situation for Americans in Thailand?", True),
            ("How much does an Airbnb cost in Bali?", True),
            ("What's the weather in Tokyo right now?", True),
            ("Latest news about OpenAI", True),
            ("Current stock price of NVIDIA", True),
            ("Best restaurants in Mexico City 2025", True),
            ("Is it safe to travel to Colombia right now?", True),
            # Should NOT trigger search
            ("Hello!", False),
            ("Thanks for your help", False),
            ("Can you write a Python function to sort a list?", False),
            ("```python\ndef foo(): pass\n```", False),
            ("What is 2 + 2?", False),
            ("Explain how recursion works", False),
        ],
    )
    def test_query_classification(self, query, should_trigger):
        """Test classification of various real-world queries."""
        messages = [{"role": "user", "content": query}]
        result = classify_query(messages, threshold=0.4)

        if should_trigger:
            assert result.should_search is True, f"Expected search for: {query}"
        else:
            assert result.should_search is False, f"Unexpected search for: {query}"


class TestKeywordSets:
    """Tests to verify keyword sets are properly defined."""

    def test_current_info_keywords_not_empty(self):
        """Test that current info keywords set is not empty."""
        assert len(CURRENT_INFO_KEYWORDS) > 0

    def test_location_keywords_not_empty(self):
        """Test that location keywords set is not empty."""
        assert len(LOCATION_KEYWORDS) > 0

    def test_travel_destinations_not_empty(self):
        """Test that travel destinations set is not empty."""
        assert len(TRAVEL_DESTINATIONS) > 0

    def test_all_keywords_lowercase(self):
        """Test that all keywords are lowercase."""
        for keyword in CURRENT_INFO_KEYWORDS:
            assert keyword == keyword.lower(), f"Keyword not lowercase: {keyword}"

        for keyword in LOCATION_KEYWORDS:
            assert keyword == keyword.lower(), f"Keyword not lowercase: {keyword}"

        for keyword in TRAVEL_DESTINATIONS:
            assert keyword == keyword.lower(), f"Keyword not lowercase: {keyword}"


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_very_long_query(self):
        """Test handling of very long queries."""
        long_query = "How easy is it to get wifi " * 100
        messages = [{"role": "user", "content": long_query}]

        # Should not crash
        result = classify_query(messages)
        assert isinstance(result, ClassificationResult)

    def test_unicode_characters(self):
        """Test handling of unicode characters."""
        messages = [{"role": "user", "content": "¿Cómo es el internet en México? 🌐"}]

        # Should not crash
        result = classify_query(messages)
        assert isinstance(result, ClassificationResult)

    def test_mixed_content_messages(self):
        """Test handling of mixed content in conversation."""
        messages = [
            {"role": "system", "content": "You are a helpful travel assistant."},
            {"role": "user", "content": "I want to work remotely."},
            {"role": "assistant", "content": "I can help with that!"},
            {"role": "user", "content": "What's the wifi situation in Costa Rica?"},
        ]

        result = classify_query(messages)
        assert result.should_search is True
        assert result.extracted_query == "What's the wifi situation in Costa Rica?"

    def test_none_content(self):
        """Test handling of None content."""
        messages = [{"role": "user", "content": None}]

        # Should not crash, should return no search
        result = classify_query(messages)
        assert result.should_search is False

    def test_empty_string_content(self):
        """Test handling of empty string content."""
        messages = [{"role": "user", "content": ""}]

        result = classify_query(messages)
        assert result.should_search is False
