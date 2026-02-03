"""
Code Task Classifier

Classifies code-related prompts into task categories and complexity levels
for intelligent model routing.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Load quality priors from JSON
_QUALITY_PRIORS_PATH = Path(__file__).parent / "code_quality_priors.json"
_quality_priors: dict[str, Any] | None = None


def _load_quality_priors() -> dict[str, Any]:
    """Load quality priors from JSON file with caching."""
    global _quality_priors
    if _quality_priors is None:
        try:
            with open(_QUALITY_PRIORS_PATH) as f:
                _quality_priors = json.load(f)
            logger.info(f"Loaded code quality priors v{_quality_priors.get('version', 'unknown')}")
        except Exception as e:
            logger.error(f"Failed to load code quality priors: {e}")
            _quality_priors = {"task_taxonomy": {}, "complexity_weights": {}}
    return _quality_priors


def get_task_taxonomy() -> dict[str, Any]:
    """Get task taxonomy from quality priors."""
    return _load_quality_priors().get("task_taxonomy", {})


def get_complexity_weights() -> dict[str, float]:
    """Get complexity weights from quality priors."""
    return _load_quality_priors().get("complexity_weights", {})


def get_quality_gates() -> dict[str, Any]:
    """Get quality gates (minimum tier requirements) from quality priors."""
    return _load_quality_priors().get("quality_gates", {})


class CodeTaskClassifier:
    """
    Classifies code-related prompts into categories and complexity levels.

    Uses keyword matching and heuristics to determine:
    - Task category (simple_code, debugging, architecture, etc.)
    - Complexity level (low, medium, high, very_high)
    - Recommended tier
    """

    def __init__(self):
        self.taxonomy = get_task_taxonomy()
        self.complexity_weights = get_complexity_weights()
        self.quality_gates = get_quality_gates()

        # Precompile regex patterns for efficiency
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile keyword patterns for fast matching."""
        self._keyword_patterns: dict[str, list[re.Pattern]] = {}
        for category, config in self.taxonomy.items():
            patterns = []
            for keyword in config.get("keywords", []):
                # Create pattern that matches keyword as word boundary
                pattern = re.compile(
                    r"\b" + re.escape(keyword.lower()) + r"\b",
                    re.IGNORECASE,
                )
                patterns.append(pattern)
            self._keyword_patterns[category] = patterns

    def classify(self, prompt: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Classify a code-related prompt.

        Args:
            prompt: The user's prompt/message
            context: Optional context (conversation history, file types, etc.)

        Returns:
            Classification result with:
            - category: Task category (e.g., "debugging", "architecture")
            - complexity: Complexity level (e.g., "medium", "high")
            - confidence: Classification confidence (0-1)
            - default_tier: Recommended model tier
            - min_tier: Minimum required tier (quality gate)
            - classification_time_ms: Time taken to classify
        """
        start_time = time.perf_counter()

        prompt_lower = prompt.lower()

        # Score each category based on keyword matches
        category_scores: dict[str, float] = {}
        for category, patterns in self._keyword_patterns.items():
            score = 0.0
            for pattern in patterns:
                matches = pattern.findall(prompt_lower)
                if matches:
                    # Weight by keyword specificity (longer keywords are more specific)
                    keyword_length = len(pattern.pattern)
                    score += len(matches) * (1 + keyword_length / 50)
            category_scores[category] = score

        # Apply context-based adjustments
        if context:
            category_scores = self._apply_context_adjustments(category_scores, context)

        # Find best matching category
        if any(score > 0 for score in category_scores.values()):
            best_category = max(category_scores, key=lambda k: category_scores[k])
            best_score = category_scores[best_category]
            # Normalize confidence (cap at 1.0)
            confidence = min(1.0, best_score / 5.0)
        else:
            # Default to code_generation if no keywords match
            best_category = "code_generation"
            confidence = 0.3

        # Get category configuration
        category_config = self.taxonomy.get(best_category, {})
        complexity = category_config.get("complexity", "medium")
        default_tier = category_config.get("default_tier", 3)
        min_tier = category_config.get("min_tier", 4)

        # Apply quality gates
        quality_gate = self.quality_gates.get(best_category, {})
        if quality_gate:
            min_tier = min(min_tier, quality_gate.get("min_tier", min_tier))

        classification_time_ms = (time.perf_counter() - start_time) * 1000

        result = {
            "category": best_category,
            "complexity": complexity,
            "confidence": round(confidence, 3),
            "default_tier": default_tier,
            "min_tier": min_tier,
            "classification_time_ms": round(classification_time_ms, 3),
            "category_scores": {k: round(v, 2) for k, v in category_scores.items() if v > 0},
        }

        logger.debug(f"Classified prompt as {best_category} ({complexity}) with confidence {confidence:.2f}")

        return result

    def _apply_context_adjustments(
        self,
        scores: dict[str, float],
        context: dict[str, Any],
    ) -> dict[str, float]:
        """
        Adjust category scores based on context.

        Context signals:
        - file_count: Number of files mentioned/involved
        - conversation_length: Length of conversation
        - has_error_trace: Whether error trace is present
        - file_types: Types of files involved
        """
        adjusted = scores.copy()

        # Multi-file context suggests higher complexity
        file_count = context.get("file_count", 1)
        if file_count > 3:
            adjusted["architecture"] = adjusted.get("architecture", 0) + 2.0
            adjusted["agentic"] = adjusted.get("agentic", 0) + 1.5

        # Error traces suggest debugging
        if context.get("has_error_trace"):
            adjusted["debugging"] = adjusted.get("debugging", 0) + 3.0

        # Long conversations suggest complex tasks
        conversation_length = context.get("conversation_length", 0)
        if conversation_length > 10:
            adjusted["refactoring"] = adjusted.get("refactoring", 0) + 1.0
            adjusted["architecture"] = adjusted.get("architecture", 0) + 1.0

        return adjusted

    def is_code_related(self, prompt: str) -> bool:
        """
        Determine if a prompt is code-related.

        Returns True if the prompt appears to be about programming/coding.
        """
        code_indicators = [
            # Programming keywords
            r"\b(function|class|method|variable|code|program|script)\b",
            r"\b(api|endpoint|database|server|client)\b",
            r"\b(bug|error|exception|crash|fix)\b",
            r"\b(refactor|optimize|implement|debug)\b",
            # Language-specific
            r"\b(python|javascript|typescript|java|rust|go|c\+\+)\b",
            r"\b(react|vue|angular|django|fastapi|express)\b",
            # Code patterns
            r"```",  # Code blocks
            r"\(\)|\[\]|\{\}",  # Function calls, arrays, objects
            r"def\s+\w+|function\s+\w+|class\s+\w+",  # Definitions
        ]

        prompt_lower = prompt.lower()
        for pattern in code_indicators:
            if re.search(pattern, prompt_lower, re.IGNORECASE):
                return True

        return False

    def extract_context_from_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Extract context signals from conversation messages.

        Args:
            messages: List of conversation messages

        Returns:
            Context dict with signals for classification
        """
        context: dict[str, Any] = {
            "conversation_length": len(messages),
            "file_count": 0,
            "has_error_trace": False,
            "file_types": set(),
        }

        # Patterns to detect files and errors
        file_pattern = re.compile(r"[\w/\\]+\.(py|js|ts|java|go|rs|cpp|c|h|jsx|tsx|vue|rb|php)")
        error_patterns = [
            r"Traceback",
            r"Error:",
            r"Exception:",
            r"at\s+\w+\.\w+\(",  # Stack trace
            r"TypeError|ValueError|RuntimeError|SyntaxError",
        ]

        all_content = ""
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                all_content += " " + content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        all_content += " " + part.get("text", "")

        # Count files mentioned
        files = file_pattern.findall(all_content)
        context["file_count"] = len(set(files))
        context["file_types"] = set(files)

        # Check for error traces
        for pattern in error_patterns:
            if re.search(pattern, all_content, re.IGNORECASE):
                context["has_error_trace"] = True
                break

        return context


# Module-level classifier instance (lazy initialization)
_classifier: CodeTaskClassifier | None = None


def get_classifier() -> CodeTaskClassifier:
    """Get the singleton classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = CodeTaskClassifier()
    return _classifier


def classify_code_task(
    prompt: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Convenience function to classify a code task.

    Args:
        prompt: The user's prompt
        context: Optional context

    Returns:
        Classification result
    """
    return get_classifier().classify(prompt, context)


def is_code_related(prompt: str) -> bool:
    """
    Convenience function to check if prompt is code-related.

    Args:
        prompt: The user's prompt

    Returns:
        True if code-related
    """
    return get_classifier().is_code_related(prompt)
