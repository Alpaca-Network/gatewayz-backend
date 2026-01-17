"""
Query Classifier Service for Automatic Web Search Detection.

This module analyzes user queries to determine if they would benefit from
real-time web search. It uses a combination of:
1. Keyword/pattern matching for common web search indicators
2. Semantic heuristics for detecting time-sensitive queries
3. Question type classification

The classifier is designed to be:
- Fast: Uses lightweight heuristics, no ML inference required
- Conservative: Only triggers search when confident it will help
- Configurable: Thresholds and patterns can be adjusted
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class QueryIntent(Enum):
    """Classification of query intent for web search relevance."""

    FACTUAL_CURRENT = "factual_current"  # Current facts, recent events, live data
    FACTUAL_TIMELESS = "factual_timeless"  # Historical facts, definitions
    OPINION_SUBJECTIVE = "opinion_subjective"  # Personal advice, opinions
    CODE_TECHNICAL = "code_technical"  # Programming, technical questions
    CREATIVE = "creative"  # Writing, brainstorming
    CONVERSATIONAL = "conversational"  # Greetings, chitchat
    LOCATION_SPECIFIC = "location_specific"  # Place-specific information
    COMPARISON = "comparison"  # Comparing options, products, services


@dataclass
class ClassificationResult:
    """Result of query classification."""

    should_search: bool
    confidence: float  # 0.0 to 1.0
    intent: QueryIntent
    reason: str
    extracted_query: str | None = None  # Optimized query for search if different from original


# Keywords that strongly indicate need for current/real-time information
CURRENT_INFO_KEYWORDS = {
    # Time-sensitive
    "current", "latest", "recent", "today", "now", "this week", "this month",
    "this year", "2024", "2025", "2026", "right now", "at the moment",
    "these days", "nowadays", "currently", "presently", "as of",

    # News and events
    "news", "update", "updates", "announcement", "announced", "launched",
    "released", "happening", "trending", "breaking",

    # Prices and data
    "price", "prices", "cost", "costs", "rate", "rates", "stock", "stocks",
    "crypto", "bitcoin", "btc", "eth", "ethereum", "market",

    # Weather
    "weather", "forecast", "temperature",

    # Sports
    "score", "scores", "game", "match", "standings", "playoffs",

    # Status and availability
    "status", "available", "availability", "open", "closed", "hours",
    "schedule", "timetable",
}

# Keywords that indicate location/travel-specific queries (high search value)
LOCATION_KEYWORDS = {
    "wifi", "internet", "connectivity", "connection",
    "remote work", "digital nomad", "coworking", "co-working",
    "travel", "traveling", "visit", "visiting", "trip",
    "country", "city", "region", "area",
    "infrastructure", "facilities", "amenities",
    "visa", "requirements", "regulations",
    "cost of living", "expenses", "budget",
    "safety", "safe", "security",
    "healthcare", "medical", "hospital",
    "accommodation", "housing", "rent", "airbnb",
    "transport", "transportation", "getting around",
    "sim card", "mobile data", "cellular", "4g", "5g", "lte",
}

# Question patterns that suggest factual current info needed
FACTUAL_QUESTION_PATTERNS = [
    r"\bhow\s+(easy|hard|difficult|good|bad|reliable|fast|slow)\b.*\b(is|are|to)\b",
    r"\bwhat\s+(is|are)\s+the\s+(best|top|latest|current|average)\b",
    r"\bwhere\s+can\s+i\s+(find|get|buy|rent)\b",
    r"\bis\s+(it|there)\s+(possible|easy|common|safe)\b.*\b(to|in)\b",
    r"\bcan\s+(i|you|we)\s+(get|find|use|access)\b",
    r"\bdoes\s+.+\s+(have|offer|provide|support)\b",
    r"\bhow\s+much\s+(does|do|is|are)\b",
    r"\bwhat.*\b(like|cost|price|rate)\b.*\bin\b",
]

# Patterns that indicate code/technical questions (generally don't need search)
CODE_PATTERNS = [
    r"```",  # Code blocks
    r"\bdef\s+\w+\s*\(",  # Python function
    r"\bfunction\s+\w+\s*\(",  # JavaScript function
    r"\bclass\s+\w+",  # Class definition
    r"\bimport\s+\w+",  # Import statement
    r"\bfrom\s+\w+\s+import\b",  # From import
    r"\b(const|let|var)\s+\w+\s*=",  # JS variable
    r"<\w+[^>]*>",  # HTML/XML tags
    r"\{\s*\w+\s*:\s*",  # Object/dict literal
    r"=>\s*\{",  # Arrow function
    r"\.(js|py|ts|tsx|jsx|java|cpp|go|rs|rb)\b",  # File extensions
]

# Countries and regions that often come up in remote work/travel queries
TRAVEL_DESTINATIONS = {
    # Central America
    "el salvador", "costa rica", "panama", "guatemala", "belize", "nicaragua", "honduras",
    # South America
    "colombia", "mexico", "argentina", "brazil", "chile", "peru", "ecuador", "uruguay",
    # Southeast Asia
    "thailand", "bali", "indonesia", "vietnam", "philippines", "malaysia", "singapore",
    # Europe
    "portugal", "spain", "croatia", "greece", "estonia", "georgia", "turkey",
    # Other popular DN destinations
    "dubai", "morocco", "south africa", "japan", "korea", "taiwan",
}


def _normalize_text(text: str) -> str:
    """Normalize text for pattern matching."""
    return text.lower().strip()


def _contains_keywords(text: str, keywords: set[str]) -> tuple[bool, list[str]]:
    """Check if text contains any keywords from the set."""
    text_lower = _normalize_text(text)
    found = []
    for keyword in keywords:
        if keyword in text_lower:
            found.append(keyword)
    return len(found) > 0, found


def _matches_patterns(text: str, patterns: list[str]) -> tuple[bool, str | None]:
    """Check if text matches any regex patterns."""
    text_lower = _normalize_text(text)
    for pattern in patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True, pattern
    return False, None


def _is_code_query(text: str) -> bool:
    """Check if the query appears to be code-related."""
    matches, _ = _matches_patterns(text, CODE_PATTERNS)
    return matches


def _extract_user_query(messages: list[dict[str, Any]]) -> str | None:
    """Extract the most recent user message content."""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str):
                return content
            # Handle multimodal content (list of content parts)
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                return " ".join(text_parts)
    return None


def _calculate_search_score(
    query: str,
    has_current_keywords: bool,
    current_keywords_found: list[str],
    has_location_keywords: bool,
    location_keywords_found: list[str],
    has_destination: bool,
    matches_question_pattern: bool,
) -> tuple[float, str]:
    """Calculate a search relevance score and reason."""
    score = 0.0
    reasons = []

    # Current info keywords are strong signals
    if has_current_keywords:
        score += 0.4
        reasons.append(f"time-sensitive keywords: {', '.join(current_keywords_found[:3])}")

    # Location/travel keywords combined with destinations
    if has_location_keywords and has_destination:
        score += 0.5
        reasons.append(f"location-specific query about {', '.join(location_keywords_found[:2])}")
    elif has_location_keywords:
        score += 0.25
        reasons.append(f"location-related: {', '.join(location_keywords_found[:2])}")
    elif has_destination:
        score += 0.2
        reasons.append("mentions travel destination")

    # Question patterns suggest info-seeking
    if matches_question_pattern:
        score += 0.2
        reasons.append("factual question pattern detected")

    # Query length heuristic - very short queries less likely to need search
    word_count = len(query.split())
    if word_count < 3:
        score *= 0.5
    elif word_count > 10:
        score += 0.1

    # Cap at 1.0
    score = min(1.0, score)

    reason = "; ".join(reasons) if reasons else "general query"
    return score, reason


def classify_query(messages: list[dict[str, Any]], threshold: float = 0.5) -> ClassificationResult:
    """
    Classify a conversation to determine if web search would be beneficial.

    Args:
        messages: List of conversation messages (OpenAI format)
        threshold: Minimum confidence score to recommend search (0.0-1.0)

    Returns:
        ClassificationResult with search recommendation and details
    """
    query = _extract_user_query(messages)

    if not query:
        return ClassificationResult(
            should_search=False,
            confidence=0.0,
            intent=QueryIntent.CONVERSATIONAL,
            reason="No user message found",
        )

    query_lower = _normalize_text(query)

    # Check for code - these generally don't benefit from web search
    if _is_code_query(query):
        return ClassificationResult(
            should_search=False,
            confidence=0.9,
            intent=QueryIntent.CODE_TECHNICAL,
            reason="Query appears to be code-related",
        )

    # Check for current info keywords
    has_current, current_found = _contains_keywords(query, CURRENT_INFO_KEYWORDS)

    # Check for location/travel keywords
    has_location, location_found = _contains_keywords(query, LOCATION_KEYWORDS)

    # Check for travel destinations
    has_destination, _ = _contains_keywords(query, TRAVEL_DESTINATIONS)

    # Check question patterns
    matches_pattern, _ = _matches_patterns(query, FACTUAL_QUESTION_PATTERNS)

    # Calculate score
    score, reason = _calculate_search_score(
        query=query,
        has_current_keywords=has_current,
        current_keywords_found=current_found,
        has_location_keywords=has_location,
        location_keywords_found=location_found,
        has_destination=has_destination,
        matches_question_pattern=matches_pattern,
    )

    # Determine intent
    if has_location or has_destination:
        intent = QueryIntent.LOCATION_SPECIFIC
    elif has_current:
        intent = QueryIntent.FACTUAL_CURRENT
    elif matches_pattern:
        intent = QueryIntent.FACTUAL_TIMELESS
    else:
        intent = QueryIntent.CONVERSATIONAL

    should_search = score >= threshold

    logger.debug(
        f"Query classification: score={score:.2f}, threshold={threshold}, "
        f"should_search={should_search}, intent={intent.value}, reason={reason}"
    )

    return ClassificationResult(
        should_search=should_search,
        confidence=score,
        intent=intent,
        reason=reason,
        extracted_query=query if should_search else None,
    )


def should_auto_search(
    messages: list[dict[str, Any]],
    threshold: float = 0.5,
    enabled: bool = True,
) -> tuple[bool, ClassificationResult]:
    """
    Quick check if auto web search should be triggered.

    Args:
        messages: Conversation messages
        threshold: Confidence threshold for triggering search
        enabled: Master switch for auto search feature

    Returns:
        Tuple of (should_search, classification_result)
    """
    if not enabled:
        return False, ClassificationResult(
            should_search=False,
            confidence=0.0,
            intent=QueryIntent.CONVERSATIONAL,
            reason="Auto search disabled",
        )

    result = classify_query(messages, threshold)
    return result.should_search, result
