"""Tests for src.services.query_classifier (gatewayz-backend#2213 housekeeping).

The previous version of this file was deliberately deleted in baddad54 for
behavioral drift against the source (stale mocks/assertions), leaving
`query_classifier.py` and its `chat.py` call site (should_auto_search) with
zero coverage anywhere in the repo. Written fresh against the current source.
"""

from src.services.query_classifier import (
    QueryIntent,
    _contains_keywords,
    _extract_user_query,
    _is_code_query,
    _matches_patterns,
    _normalize_text,
    classify_query,
    should_auto_search,
)


def _user_message(content):
    return [{"role": "user", "content": content}]


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def test_normalize_text_lowercases_and_strips():
    assert _normalize_text("  Hello WORLD  ") == "hello world"


def test_contains_keywords_finds_present_keyword():
    found, matches = _contains_keywords("what's the latest news", {"latest", "news"})
    assert found is True
    assert set(matches) == {"latest", "news"}


def test_contains_keywords_no_match():
    found, matches = _contains_keywords("tell me a joke", {"latest", "news"})
    assert found is False
    assert matches == []


def test_matches_patterns_detects_factual_question():
    matched, pattern = _matches_patterns(
        "what is the best restaurant in town",
        [r"\bwhat\s+(is|are)\s+the\s+(best|top|latest|current|average)\b"],
    )
    assert matched is True
    assert pattern is not None


def test_is_code_query_detects_code_block():
    assert _is_code_query("```python\nprint('hi')\n```") is True


def test_is_code_query_detects_function_definition():
    assert _is_code_query("def foo(x):\n    return x") is True


def test_is_code_query_false_for_plain_text():
    assert _is_code_query("what is the weather today") is False


def test_extract_user_query_returns_most_recent_user_message():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]
    assert _extract_user_query(messages) == "second"


def test_extract_user_query_flattens_multipart_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ],
        }
    ]
    assert _extract_user_query(messages) == "hello world"


def test_extract_user_query_returns_none_when_no_user_message():
    assert _extract_user_query([{"role": "assistant", "content": "hi"}]) is None


# --------------------------------------------------------------------------- #
# classify_query
# --------------------------------------------------------------------------- #
def test_classify_query_no_user_message_is_conversational():
    result = classify_query([{"role": "assistant", "content": "hi"}])

    assert result.should_search is False
    assert result.confidence == 0.0
    assert result.intent == QueryIntent.CONVERSATIONAL


def test_classify_query_code_question_never_searches():
    result = classify_query(_user_message("```python\ndef foo(): pass\n```"))

    assert result.should_search is False
    assert result.intent == QueryIntent.CODE_TECHNICAL
    assert result.confidence == 0.9


def test_classify_query_current_info_keywords_trigger_search():
    result = classify_query(_user_message("what is the latest news on the election"))

    assert result.should_search is True
    assert result.intent == QueryIntent.FACTUAL_CURRENT
    assert result.extracted_query is not None


def test_classify_query_location_and_destination_triggers_search():
    result = classify_query(_user_message("how is the wifi and cost of living in bali"))

    assert result.should_search is True
    assert result.intent == QueryIntent.LOCATION_SPECIFIC


def test_classify_query_below_threshold_does_not_search():
    result = classify_query(_user_message("hello, how are you"), threshold=0.5)

    assert result.should_search is False
    assert result.extracted_query is None


def test_classify_query_short_query_score_is_dampened():
    # "news" alone is a current-info keyword but under the 3-word floor,
    # so the score is halved and stays under the default 0.5 threshold.
    result = classify_query(_user_message("news"))

    assert result.confidence < 0.4
    assert result.should_search is False


def test_classify_query_custom_threshold_is_respected():
    low_threshold = classify_query(_user_message("what's the latest price"), threshold=0.1)
    high_threshold = classify_query(_user_message("what's the latest price"), threshold=0.99)

    assert low_threshold.should_search is True
    assert high_threshold.should_search is False


# --------------------------------------------------------------------------- #
# should_auto_search
# --------------------------------------------------------------------------- #
def test_should_auto_search_disabled_short_circuits():
    should_search, result = should_auto_search(
        _user_message("what is the latest news"), enabled=False
    )

    assert should_search is False
    assert result.reason == "Auto search disabled"


def test_should_auto_search_enabled_delegates_to_classify_query():
    should_search, result = should_auto_search(_user_message("what is the latest news"))

    assert should_search is True
    assert result.intent == QueryIntent.FACTUAL_CURRENT
