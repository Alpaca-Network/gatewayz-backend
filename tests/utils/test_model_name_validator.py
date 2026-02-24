"""
Tests for model name validation and cleaning utilities.
"""

import pytest

from src.utils.model_name_validator import (
    clean_model_name,
    validate_and_clean_model_name,
    validate_model_name,
)


class TestValidateModelName:
    """Tests for validate_model_name function."""

    def test_valid_clean_name(self):
        """Test that clean model names are validated as valid."""
        is_valid, error = validate_model_name("Llama 3.3 70B")
        assert is_valid is True
        assert error is None

    def test_valid_with_numbers(self):
        """Test that names with numbers are valid."""
        is_valid, error = validate_model_name("GPT-4.5")
        assert is_valid is True
        assert error is None

    def test_valid_with_hyphens(self):
        """Test that names with hyphens are valid."""
        is_valid, error = validate_model_name("DeepSeek-V3")
        assert is_valid is True
        assert error is None

    def test_invalid_with_colon(self):
        """Test that names with colons are invalid."""
        is_valid, error = validate_model_name("Meta: Llama 3.3 70B")
        assert is_valid is False
        assert "colon" in error.lower()

    def test_invalid_with_parentheses_size(self):
        """Test that names with size in parentheses are invalid."""
        is_valid, error = validate_model_name("Mistral (7B) Instruct")
        assert is_valid is False
        assert "parentheses" in error.lower()

    def test_invalid_with_parentheses_type(self):
        """Test that names with type in parentheses are invalid."""
        is_valid, error = validate_model_name("Model Name (FP8)")
        assert is_valid is False
        assert "parentheses" in error.lower()

    def test_invalid_with_free_marker(self):
        """Test that names with (free) marker are invalid."""
        is_valid, error = validate_model_name("Qwen3 (free)")
        assert is_valid is False
        assert "parentheses" in error.lower()

    def test_invalid_empty_name(self):
        """Test that empty names are invalid."""
        is_valid, error = validate_model_name("")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_invalid_whitespace_only(self):
        """Test that whitespace-only names are invalid."""
        is_valid, error = validate_model_name("   ")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_invalid_too_long(self):
        """Test that names over 100 characters are invalid."""
        long_name = "A" * 101
        is_valid, error = validate_model_name(long_name)
        assert is_valid is False
        assert "too long" in error.lower()


class TestCleanModelName:
    """Tests for clean_model_name function."""

    def test_remove_company_prefix_with_colon(self):
        """Test that company prefix with colon is removed."""
        assert clean_model_name("Meta: Llama 3.3 70B") == "Llama 3.3 70B"
        assert clean_model_name("OpenAI: GPT-4") == "GPT-4"
        assert clean_model_name("DeepSeek: R1 0528") == "R1 0528"
        assert clean_model_name("Anthropic: Claude Opus 4") == "Claude Opus 4"

    def test_remove_parentheses_at_end(self):
        """Test that parenthetical info at end is removed."""
        assert (
            clean_model_name("Llama 4 Maverick Instruct (17Bx128E)") == "Llama 4 Maverick Instruct"
        )
        assert clean_model_name("Qwen3 30B A3B Instruct (Free)") == "Qwen3 30B A3B Instruct"
        assert clean_model_name("Grok 2 (December 2024)") == "Grok 2"
        assert clean_model_name("Model Name (FP8)") == "Model Name"
        assert clean_model_name("Model Name (7B)") == "Model Name"
        assert clean_model_name("Model Name (free)") == "Model Name"

    def test_remove_parentheses_in_middle(self):
        """Test that parenthetical info in middle is removed with proper spacing."""
        assert clean_model_name("Mistral (7B) Instruct v0.3") == "Mistral Instruct v0.3"
        assert clean_model_name("Qwen2.5-VL (72B) Instruct") == "Qwen2.5-VL Instruct"
        assert clean_model_name("Model (FP8) Name") == "Model Name"

    def test_remove_both_colon_and_parentheses(self):
        """Test that both colon and parentheses are removed."""
        assert (
            clean_model_name("Swiss AI: Apertus 70B Instruct 2509 (free)")
            == "Apertus 70B Instruct 2509"
        )
        # (INT4) at end is removed by end pattern
        assert (
            clean_model_name("Intel: Qwen3 Coder 480B A35B Instruct (INT4)")
            == "Qwen3 Coder 480B A35B Instruct"
        )
        assert clean_model_name("Company: Model (7B)") == "Model"

    def test_clean_already_clean_name(self):
        """Test that already clean names are unchanged."""
        assert clean_model_name("Llama 3.3 70B") == "Llama 3.3 70B"
        assert clean_model_name("GPT-4") == "GPT-4"
        assert clean_model_name("DeepSeek V3") == "DeepSeek V3"

    def test_normalize_whitespace(self):
        """Test that whitespace is normalized."""
        assert clean_model_name("Model   Name") == "Model Name"
        assert clean_model_name("  Model Name  ") == "Model Name"

    def test_truncate_long_name(self):
        """Test that names over 100 characters are truncated."""
        long_name = "A" * 105
        cleaned = clean_model_name(long_name)
        assert len(cleaned) == 100

    def test_empty_name(self):
        """Test that empty names return empty string."""
        assert clean_model_name("") == ""
        assert clean_model_name("   ") == ""


class TestValidateAndCleanModelName:
    """Tests for validate_and_clean_model_name function."""

    def test_valid_name_unchanged(self):
        """Test that valid names are returned unchanged."""
        name = "Llama 3.3 70B"
        result = validate_and_clean_model_name(name)
        assert result == name

    def test_malformed_name_auto_cleaned(self):
        """Test that malformed names are automatically cleaned."""
        result = validate_and_clean_model_name("Meta: Llama 3.3 70B")
        assert result == "Llama 3.3 70B"

    def test_malformed_name_with_parentheses_auto_cleaned(self):
        """Test that names with parentheses are automatically cleaned."""
        result = validate_and_clean_model_name("Mistral (7B) Instruct")
        assert result == "Mistral Instruct"

    def test_malformed_name_no_auto_clean_raises_error(self):
        """Test that malformed names raise error when auto_clean=False."""
        with pytest.raises(ValueError, match="Invalid model name"):
            validate_and_clean_model_name("Meta: Llama 3.3 70B", auto_clean=False)

    def test_valid_name_no_auto_clean(self):
        """Test that valid names work with auto_clean=False."""
        name = "Llama 3.3 70B"
        result = validate_and_clean_model_name(name, auto_clean=False)
        assert result == name


class TestRealWorldExamples:
    """Tests using real-world examples from the database audit."""

    @pytest.mark.parametrize(
        "malformed,expected",
        [
            # AiMo examples
            ("Alibaba: Qwen2.5 7B Instruct", "Qwen2.5 7B Instruct"),
            ("Anthropic: Claude Opus 4", "Claude Opus 4"),
            ("DeepSeek: R1 0528", "R1 0528"),
            ("Meta: Llama 3.3 70B Instruct", "Llama 3.3 70B Instruct"),
            ("OpenAI: GPT-4.1 Nano", "GPT-4.1 Nano"),
            ("Qwen: Qwen3 235B A22B Thinking 2507", "Qwen3 235B A22B Thinking 2507"),
            # Together examples
            ("Llama 4 Maverick Instruct (17Bx128E)", "Llama 4 Maverick Instruct"),
            ("Mistral (7B) Instruct v0.3", "Mistral Instruct v0.3"),
            ("Qwen2.5-VL (72B) Instruct", "Qwen2.5-VL Instruct"),
            # Sybil examples
            ("Qwen: Qwen3 Embedding 8B", "Qwen3 Embedding 8B"),
            ("ZAI: GLM-4.5", "GLM-4.5"),
            # Novita example
            ("OpenAI: GPT OSS 20B", "GPT OSS 20B"),
            # xAI example
            ("Grok 2 (December 2024)", "Grok 2"),
            # Edge cases
            ("Swiss AI: Apertus 70B Instruct 2509 (free)", "Apertus 70B Instruct 2509"),
            ("Qwen: Qwen3 Next 80B A3B Instruct (free)", "Qwen3 Next 80B A3B Instruct"),
        ],
    )
    def test_clean_real_world_examples(self, malformed, expected):
        """Test cleaning of real-world malformed model names."""
        assert clean_model_name(malformed) == expected

    @pytest.mark.parametrize(
        "name",
        [
            "Llama 3.3 70B",
            "GPT-4",
            "DeepSeek V3",
            "Gemini 2.0 Flash",
            "Claude 3.5 Sonnet",
            "Mistral Large",
            "Qwen3 Coder",
            "R1 0528",
        ],
    )
    def test_validate_clean_names(self, name):
        """Test that clean names from production are validated as valid."""
        is_valid, error = validate_model_name(name)
        assert is_valid is True
        assert error is None
