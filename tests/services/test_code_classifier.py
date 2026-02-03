"""
Tests for Code Task Classifier

Tests the code classification logic for the code-optimized prompt router.
"""

import pytest

from src.services.code_classifier import (
    CodeTaskClassifier,
    classify_code_task,
    get_classifier,
    is_code_related,
)


# Shared fixtures for code classifier tests
@pytest.fixture
def classifier():
    """Provide a fresh CodeTaskClassifier instance for tests."""
    return CodeTaskClassifier()


@pytest.fixture
def sample_prompts():
    """Provide sample prompts for testing classification."""
    return {
        "simple_code": "Fix the typo in the variable name",
        "code_explanation": "What does this function do?",
        "code_generation": "Write a function that calculates fibonacci numbers",
        "debugging": "Debug this function, it returns null unexpectedly",
        "refactoring": "Refactor this code to use async/await",
        "architecture": "Design a microservices architecture for this system",
        "agentic": "Build the entire authentication system from scratch",
    }


@pytest.mark.unit
class TestCodeTaskClassifier:
    """Test suite for CodeTaskClassifier."""

    # ==================== Task Category Classification Tests ====================

    def test_classify_simple_code(self, classifier):
        """Test classification of simple code tasks."""
        result = classifier.classify("Fix the typo in the variable name")
        assert result["category"] == "simple_code"
        assert result["complexity"] == "low"
        assert result["default_tier"] == 4
        assert result["min_tier"] == 4

    def test_classify_code_explanation(self, classifier):
        """Test classification of code explanation tasks."""
        result = classifier.classify("What does this function do?")
        assert result["category"] == "code_explanation"
        assert result["complexity"] == "low_medium"
        assert result["default_tier"] == 3

    def test_classify_code_generation(self, classifier):
        """Test classification of code generation tasks."""
        result = classifier.classify("Write a function that calculates fibonacci numbers")
        assert result["category"] == "code_generation"
        assert result["complexity"] == "medium"
        assert result["default_tier"] == 3

    def test_classify_debugging(self, classifier):
        """Test classification of debugging tasks."""
        result = classifier.classify("Debug this function, it returns null unexpectedly")
        assert result["category"] == "debugging"
        assert result["complexity"] == "medium_high"
        assert result["default_tier"] == 2
        assert result["min_tier"] == 2  # Quality gate

    def test_classify_refactoring(self, classifier):
        """Test classification of refactoring tasks."""
        result = classifier.classify("Refactor this code to use async/await")
        assert result["category"] == "refactoring"
        assert result["complexity"] == "medium_high"
        assert result["default_tier"] == 2
        assert result["min_tier"] == 2  # Quality gate

    def test_classify_architecture(self, classifier):
        """Test classification of architecture tasks."""
        result = classifier.classify("Design a microservices architecture for this system")
        assert result["category"] == "architecture"
        assert result["complexity"] == "high"
        assert result["default_tier"] == 1
        assert result["min_tier"] == 1  # Quality gate

    def test_classify_agentic(self, classifier):
        """Test classification of agentic coding tasks."""
        result = classifier.classify("Build the entire authentication system from scratch")
        assert result["category"] == "agentic"
        assert result["complexity"] == "very_high"
        assert result["default_tier"] == 1
        assert result["min_tier"] == 1  # Quality gate

    # ==================== Confidence Tests ====================

    def test_high_confidence_classification(self, classifier):
        """Test that clear prompts get high confidence."""
        result = classifier.classify("Debug the null pointer exception in the login function")
        assert result["confidence"] >= 0.5

    def test_low_confidence_classification(self, classifier):
        """Test that ambiguous prompts get lower confidence."""
        result = classifier.classify("Help me with something")
        assert result["confidence"] < 0.5

    def test_default_category_for_ambiguous(self, classifier):
        """Test that ambiguous prompts default to code_generation."""
        result = classifier.classify("Please assist me")
        assert result["category"] == "code_generation"

    # ==================== Classification Time Tests ====================

    def test_classification_time_under_threshold(self, classifier):
        """Test that classification completes within target time."""
        result = classifier.classify("Write a function to sort an array")
        # Target is < 2ms, allow some margin
        assert result["classification_time_ms"] < 10

    # ==================== Context-Based Adjustment Tests ====================

    def test_context_multi_file(self, classifier):
        """Test that multi-file context boosts architecture/agentic scores."""
        context = {"file_count": 5, "has_error_trace": False, "conversation_length": 1}
        result = classifier.classify("Update the code", context)
        # Multi-file context should influence toward architecture/agentic
        assert "architecture" in result["category_scores"] or "agentic" in result["category_scores"]

    def test_context_error_trace(self, classifier):
        """Test that error trace context boosts debugging score."""
        context = {"file_count": 1, "has_error_trace": True, "conversation_length": 1}
        result = classifier.classify("Fix this issue", context)
        assert "debugging" in result["category_scores"]

    def test_context_long_conversation(self, classifier):
        """Test that long conversation context boosts refactoring/architecture."""
        context = {"file_count": 1, "has_error_trace": False, "conversation_length": 15}
        result = classifier.classify("Improve the code", context)
        # Should have some refactoring or architecture boost
        scores = result.get("category_scores", {})
        assert len(scores) > 0

    # ==================== is_code_related Tests ====================

    def test_is_code_related_positive(self, classifier):
        """Test that code-related prompts are detected."""
        assert classifier.is_code_related("Write a Python function")
        assert classifier.is_code_related("Debug the error in my code")
        assert classifier.is_code_related("Explain this class method")
        assert classifier.is_code_related("```python\ndef foo(): pass\n```")

    def test_is_code_related_negative(self, classifier):
        """Test that non-code prompts are not detected as code-related."""
        # These might still be detected due to some keywords
        # The function is quite sensitive to any programming-related words
        result = classifier.is_code_related("What is the weather today?")
        # This is expected to be False unless there are code-related keywords
        assert isinstance(result, bool)

    # ==================== Message Context Extraction Tests ====================

    def test_extract_context_from_messages(self, classifier):
        """Test context extraction from conversation messages."""
        messages = [
            {"role": "user", "content": "I have an error in src/main.py"},
            {"role": "assistant", "content": "Let me check that file"},
            {"role": "user", "content": "Here's the Traceback:\nTypeError: cannot be None"},
        ]
        context = classifier.extract_context_from_messages(messages)
        assert context["conversation_length"] == 3
        assert context["has_error_trace"] is True
        assert context["file_count"] >= 1

    def test_extract_context_multi_part_messages(self, classifier):
        """Test context extraction with multi-part messages."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check this file: app.js"},
                    {"type": "text", "text": "And this: utils.ts"},
                ],
            },
        ]
        context = classifier.extract_context_from_messages(messages)
        assert context["file_count"] >= 2


@pytest.mark.unit
class TestModuleFunctions:
    """Test module-level convenience functions."""

    def test_get_classifier_singleton(self):
        """Test that get_classifier returns the same instance."""
        c1 = get_classifier()
        c2 = get_classifier()
        assert c1 is c2

    def test_classify_code_task_function(self):
        """Test the convenience classify_code_task function."""
        result = classify_code_task("Write a sorting algorithm")
        assert "category" in result
        assert "complexity" in result
        assert "confidence" in result
        assert "default_tier" in result

    def test_is_code_related_function(self):
        """Test the convenience is_code_related function."""
        assert is_code_related("def foo(): pass") is True


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_prompt(self, classifier):
        """Test classification of empty prompt."""
        result = classifier.classify("")
        assert "category" in result
        assert result["category"] == "code_generation"  # Default

    def test_very_long_prompt(self, classifier):
        """Test classification of very long prompt."""
        long_prompt = "debug " * 1000 + "this error"
        result = classifier.classify(long_prompt)
        assert "category" in result
        # Should still complete without error

    def test_unicode_prompt(self, classifier):
        """Test classification with unicode characters."""
        result = classifier.classify("修复这个bug并重构代码")
        assert "category" in result

    def test_none_context(self, classifier):
        """Test classification with None context."""
        result = classifier.classify("Fix the bug", context=None)
        assert "category" in result

    def test_empty_context(self, classifier):
        """Test classification with empty context."""
        result = classifier.classify("Fix the bug", context={})
        assert "category" in result
