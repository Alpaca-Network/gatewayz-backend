"""
Rule-Based Prompt Classifier for Prompt Router.

Fast, lightweight classification using keyword matching and heuristics.
No ML inference - target latency: < 1ms.

Based on the existing query_classifier.py pattern.
"""

import logging
import re
from typing import Any

from src.schemas.router import ClassificationResult, PromptCategory

logger = logging.getLogger(__name__)

# Pre-compiled patterns for fast matching (compiled once at module load)
CODE_PATTERN = re.compile(
    r"```|"
    r"\bdef\s+\w+\s*\(|"
    r"\bfunction\s+\w+\s*\(|"
    r"\bclass\s+\w+|"
    r"\bimport\s+\w+|"
    r"\bfrom\s+\w+\s+import\b|"
    r"\b(const|let|var)\s+\w+\s*=|"
    r"=>\s*\{|"
    r"\.(py|js|ts|tsx|jsx|java|cpp|go|rs|rb|php|swift|kt)\b",
    re.IGNORECASE,
)

CODE_KEYWORDS = frozenset(
    {
        "code",
        "function",
        "class",
        "method",
        "variable",
        "bug",
        "error",
        "exception",
        "debug",
        "compile",
        "syntax",
        "runtime",
        "algorithm",
        "implement",
        "refactor",
        "optimize",
        "python",
        "javascript",
        "typescript",
        "java",
        "rust",
        "golang",
        "sql",
        "html",
        "css",
        "react",
        "vue",
        "angular",
        "api",
        "endpoint",
        "database",
        "query",
        "script",
        "program",
        "module",
        "package",
        "library",
        "framework",
        "git",
        "commit",
        "merge",
        "branch",
    }
)

MATH_PATTERN = re.compile(
    r"\bcalculate\b|"
    r"\bcompute\b|"
    r"\bsolve\b|"
    r"\bequation\b|"
    r"\bintegral\b|"
    r"\bderivative\b|"
    r"\bmatrix\b|"
    r"\d+\s*[\+\-\*\/\^]\s*\d+|"
    r"\bsum\s+of\b|"
    r"\baverage\s+of\b",
    re.IGNORECASE,
)

MATH_KEYWORDS = frozenset(
    {
        "math",
        "calculate",
        "compute",
        "solve",
        "equation",
        "formula",
        "integral",
        "derivative",
        "matrix",
        "algebra",
        "geometry",
        "calculus",
        "statistics",
        "probability",
        "percentage",
        "fraction",
        "decimal",
        "multiply",
        "divide",
        "subtract",
        "add",
        "sum",
        "average",
        "mean",
        "median",
        "mode",
        "variance",
        "standard deviation",
        "logarithm",
    }
)

REASONING_PATTERN = re.compile(
    r"\bexplain\s+why\b|"
    r"\banalyze\b|"
    r"\bcompare\s+(and\s+)?contrast\b|"
    r"\bstep\s+by\s+step\b|"
    r"\bpros\s+and\s+cons\b|"
    r"\bevaluate\b|"
    r"\bassess\b|"
    r"\bcritique\b|"
    r"\bimplications\b|"
    r"\bconsequences\b",
    re.IGNORECASE,
)

REASONING_KEYWORDS = frozenset(
    {
        "analyze",
        "analyse",
        "explain",
        "why",
        "reason",
        "reasoning",
        "logic",
        "logical",
        "evaluate",
        "assess",
        "critique",
        "compare",
        "contrast",
        "implications",
        "consequences",
        "consider",
        "think",
        "argument",
        "evidence",
        "conclusion",
        "hypothesis",
        "theory",
        "step by step",
        "pros and cons",
        "advantages",
        "disadvantages",
    }
)

CREATIVE_KEYWORDS = frozenset(
    {
        "write",
        "story",
        "poem",
        "poetry",
        "creative",
        "fiction",
        "novel",
        "narrative",
        "character",
        "plot",
        "dialogue",
        "scene",
        "imagine",
        "fantasy",
        "tale",
        "script",
        "screenplay",
        "lyrics",
        "song",
        "compose",
        "brainstorm",
        "ideas",
        "generate",
        "create",
    }
)

SUMMARIZATION_KEYWORDS = frozenset(
    {
        "summarize",
        "summarise",
        "summary",
        "brief",
        "condense",
        "shorten",
        "tldr",
        "tl;dr",
        "key points",
        "main points",
        "overview",
        "recap",
        "digest",
        "abstract",
        "synopsis",
    }
)

TRANSLATION_KEYWORDS = frozenset(
    {
        "translate",
        "translation",
        "spanish",
        "french",
        "german",
        "chinese",
        "japanese",
        "korean",
        "portuguese",
        "italian",
        "russian",
        "arabic",
        "hindi",
        "language",
        "convert to",
    }
)

DATA_ANALYSIS_KEYWORDS = frozenset(
    {
        "data",
        "dataset",
        "csv",
        "json",
        "excel",
        "spreadsheet",
        "table",
        "rows",
        "columns",
        "filter",
        "sort",
        "aggregate",
        "pivot",
        "chart",
        "graph",
        "visualization",
        "trend",
        "pattern",
        "correlation",
        "regression",
        "cluster",
        "segment",
    }
)

# Minimum word count for complex classification
MIN_WORDS_FOR_COMPLEX = 30


def classify_prompt(messages: list[dict[str, Any]]) -> ClassificationResult:
    """
    Classify a prompt using rule-based heuristics.

    Target latency: < 1ms

    Args:
        messages: Conversation messages (OpenAI format)

    Returns:
        ClassificationResult with category, confidence, and debug signals
    """
    # Extract text from messages
    text = _extract_text(messages)
    text_lower = text.lower()
    word_count = len(text.split())

    # Build signals dict for debugging
    signals = {
        "word_count": word_count,
        "message_count": len(messages),
        "has_question_mark": "?" in text,
    }

    # Check patterns (order matters - more specific first)

    # Code detection (highest priority for code-related queries)
    if CODE_PATTERN.search(text):
        signals["matched_pattern"] = "code_pattern"
        # Determine if it's generation or review
        if _contains_keywords(
            text_lower, {"review", "fix", "debug", "error", "bug", "issue", "problem"}
        ):
            return ClassificationResult(PromptCategory.CODE_REVIEW, 0.85, signals)
        return ClassificationResult(PromptCategory.CODE_GENERATION, 0.85, signals)

    code_keyword_count = _count_keywords(text_lower, CODE_KEYWORDS)
    if code_keyword_count >= 2:
        signals["code_keywords"] = code_keyword_count
        if _contains_keywords(text_lower, {"review", "fix", "debug", "error", "bug"}):
            return ClassificationResult(PromptCategory.CODE_REVIEW, 0.80, signals)
        return ClassificationResult(PromptCategory.CODE_GENERATION, 0.80, signals)

    # Math detection
    if MATH_PATTERN.search(text):
        signals["matched_pattern"] = "math_pattern"
        return ClassificationResult(PromptCategory.MATH_CALCULATION, 0.85, signals)

    math_keyword_count = _count_keywords(text_lower, MATH_KEYWORDS)
    if math_keyword_count >= 2:
        signals["math_keywords"] = math_keyword_count
        return ClassificationResult(PromptCategory.MATH_CALCULATION, 0.80, signals)

    # Translation detection
    translation_keyword_count = _count_keywords(text_lower, TRANSLATION_KEYWORDS)
    if translation_keyword_count >= 2 or "translate" in text_lower:
        signals["translation_keywords"] = translation_keyword_count
        return ClassificationResult(PromptCategory.TRANSLATION, 0.85, signals)

    # Summarization detection
    summarization_keyword_count = _count_keywords(text_lower, SUMMARIZATION_KEYWORDS)
    if summarization_keyword_count >= 1:
        signals["summarization_keywords"] = summarization_keyword_count
        return ClassificationResult(PromptCategory.SUMMARIZATION, 0.80, signals)

    # Data analysis detection
    data_keyword_count = _count_keywords(text_lower, DATA_ANALYSIS_KEYWORDS)
    if data_keyword_count >= 3:
        signals["data_keywords"] = data_keyword_count
        return ClassificationResult(PromptCategory.DATA_ANALYSIS, 0.75, signals)

    # Reasoning detection
    if REASONING_PATTERN.search(text):
        signals["matched_pattern"] = "reasoning_pattern"
        return ClassificationResult(PromptCategory.COMPLEX_REASONING, 0.80, signals)

    reasoning_keyword_count = _count_keywords(text_lower, REASONING_KEYWORDS)
    if reasoning_keyword_count >= 2:
        signals["reasoning_keywords"] = reasoning_keyword_count
        return ClassificationResult(PromptCategory.COMPLEX_REASONING, 0.75, signals)

    # Creative writing detection
    creative_keyword_count = _count_keywords(text_lower, CREATIVE_KEYWORDS)
    if creative_keyword_count >= 2:
        signals["creative_keywords"] = creative_keyword_count
        return ClassificationResult(PromptCategory.CREATIVE_WRITING, 0.75, signals)

    # Simple Q&A detection (short questions)
    if word_count < MIN_WORDS_FOR_COMPLEX and signals["has_question_mark"]:
        signals["classification_reason"] = "short_question"
        return ClassificationResult(PromptCategory.SIMPLE_QA, 0.70, signals)

    # Multi-turn conversation detection
    if len(messages) > 2:
        signals["classification_reason"] = "multi_turn"
        return ClassificationResult(PromptCategory.CONVERSATION, 0.65, signals)

    # Default: low confidence conversation/unknown
    if word_count > 100:
        # Long prompt without clear signals - might be complex
        signals["classification_reason"] = "long_unclear"
        return ClassificationResult(PromptCategory.UNKNOWN, 0.40, signals)

    signals["classification_reason"] = "default"
    return ClassificationResult(PromptCategory.CONVERSATION, 0.50, signals)


def _extract_text(messages: list[dict[str, Any]]) -> str:
    """
    Extract text from messages, focusing on most recent user message.
    Fast - no deep recursion.
    """
    # Get the last user message (most relevant for classification)
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # Handle multimodal content - extract text parts
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text", "")
                        if text:
                            text_parts.append(text)
                    elif isinstance(part, str):
                        text_parts.append(part)
                return " ".join(text_parts)

    # Fallback: concatenate all messages
    all_text = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            all_text.append(content)
    return " ".join(all_text)


def _contains_keywords(text: str, keywords: set[str]) -> bool:
    """Check if text contains any keywords."""
    for keyword in keywords:
        if keyword in text:
            return True
    return False


def _count_keywords(text: str, keywords: frozenset[str]) -> int:
    """Count how many keywords are present in text."""
    count = 0
    for keyword in keywords:
        if keyword in text:
            count += 1
    return count


def get_category_description(category: PromptCategory) -> str:
    """Get human-readable description for a category."""
    descriptions = {
        PromptCategory.SIMPLE_QA: "Short factual question",
        PromptCategory.COMPLEX_REASONING: "Multi-step reasoning or analysis",
        PromptCategory.CODE_GENERATION: "Code writing or programming",
        PromptCategory.CODE_REVIEW: "Code review, debugging, or fixing",
        PromptCategory.CREATIVE_WRITING: "Creative content generation",
        PromptCategory.SUMMARIZATION: "Text summarization",
        PromptCategory.TRANSLATION: "Language translation",
        PromptCategory.MATH_CALCULATION: "Mathematical calculation",
        PromptCategory.DATA_ANALYSIS: "Data analysis or processing",
        PromptCategory.CONVERSATION: "General conversation",
        PromptCategory.TOOL_USE: "Function/tool calling",
        PromptCategory.UNKNOWN: "Unclear classification",
    }
    return descriptions.get(category, "Unknown")
