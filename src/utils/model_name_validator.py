"""
Model name validation and cleaning utilities.

Ensures model names follow standardized format:
- Clean display name only (e.g., "Llama 3.3 70B")
- No company prefixes with colons (e.g., NOT "Meta: Llama 3.3 70B")
- No parentheses for type/size info (e.g., NOT "Mistral (7B) Instruct")
- Length <= 100 characters
- Not empty or null
"""
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def validate_model_name(name: str) -> Tuple[bool, Optional[str]]:
    """
    Validate model name follows standardized format.

    Args:
        name: Model name to validate

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
        - If valid: (True, None)
        - If invalid: (False, "error message")
    """
    if not name or not name.strip():
        return False, "Model name is empty or null"

    if len(name) > 100:
        return False, f"Model name too long ({len(name)} > 100 characters)"

    if ":" in name:
        return False, "Model name contains colon (:) - company prefix should be removed"

    # Check for parentheses that indicate type/size info
    # Allow parentheses that are part of legitimate model names (rare edge case)
    if "(" in name and ")" in name:
        # Common patterns that indicate malformed names
        malformed_patterns = [
            r"\([A-Z0-9]+\)",  # (FP8), (INT4), (AWQ), (BF16)
            r"\(\d+[BM]\)",  # (7B), (70B), (128M)
            r"\(\d+[BM]x\d+[EM]\)",  # (17Bx128E)
            r"\(Chat\)",  # (Chat)
            r"\(Instruct\)",  # (Instruct)
            r"\([Ff]ree\)",  # (free), (Free)
            r"\([Dd]ecember \d+\)",  # (December 2024)
        ]

        for pattern in malformed_patterns:
            if re.search(pattern, name):
                return (
                    False,
                    f"Model name contains parentheses with type/size info ({pattern})",
                )

    return True, None


def clean_model_name(name: str) -> str:
    """
    Clean a malformed model name to follow standardized format.

    Transformations:
    1. Remove company prefix before colon (e.g., "Meta: Llama" -> "Llama")
    2. Remove parenthetical type/size info (e.g., "Mistral (7B)" -> "Mistral")
    3. Remove "(free)" indicators
    4. Strip and normalize whitespace
    5. Truncate to 100 characters if needed

    Args:
        name: Model name to clean (may be malformed)

    Returns:
        Cleaned model name following standardized format
    """
    if not name:
        return ""

    cleaned = name.strip()

    # Remove company prefix before colon
    # Examples:
    # - "Meta: Llama 3.3 70B" -> "Llama 3.3 70B"
    # - "OpenAI: GPT-4" -> "GPT-4"
    # - "DeepSeek: R1 0528" -> "R1 0528"
    if ":" in cleaned:
        parts = cleaned.split(":", 1)
        if len(parts) == 2:
            # Keep the part after the colon
            cleaned = parts[1].strip()
            logger.debug(f"Removed company prefix: '{name}' -> '{cleaned}'")

    # Remove parenthetical info at the end
    # Examples:
    # - "Llama 4 Maverick Instruct (17Bx128E)" -> "Llama 4 Maverick Instruct"
    # - "Mistral (7B) Instruct" -> "Mistral Instruct"
    # - "Qwen3 30B A3B Instruct (Free)" -> "Qwen3 30B A3B Instruct"
    # - "Grok 2 (December 2024)" -> "Grok 2"

    # Common patterns to remove (at end)
    end_patterns = [
        r"\s*\([A-Z0-9]+\)$",  # (FP8), (INT4), (AWQ), (BF16) at end
        r"\s*\(\d+[BM]\)$",  # (7B), (70B), (128M) at end
        r"\s*\(\d+[BM]x\d+[EM]\)$",  # (17Bx128E) at end
        r"\s*\(Chat\)$",  # (Chat) at end
        r"\s*\(Instruct\)$",  # (Instruct) at end
        r"\s*\([Ff]ree\)$",  # (free), (Free) at end
        r"\s*\([Dd]ecember \d+\)$",  # (December 2024) at end
        r"\s*\(\d{4}\)$",  # (2024) at end
    ]

    # Patterns in middle of name - replace with single space
    middle_patterns = [
        r"\s*\(\d+[BM]\)\s+",  # (7B) in middle -> space
        r"\s*\([A-Z0-9]+\)\s+",  # (FP8), (INT4) in middle -> space
    ]

    original = cleaned

    # Remove end patterns (no replacement)
    for pattern in end_patterns:
        cleaned = re.sub(pattern, "", cleaned)

    # Remove middle patterns (replace with single space)
    for pattern in middle_patterns:
        cleaned = re.sub(pattern, " ", cleaned)

    if cleaned != original:
        logger.debug(f"Removed parenthetical info: '{original}' -> '{cleaned}'")

    # Normalize whitespace
    cleaned = " ".join(cleaned.split())

    # Truncate to 100 characters if needed
    if len(cleaned) > 100:
        cleaned = cleaned[:100].strip()
        logger.debug(f"Truncated model name to 100 characters: '{name}' -> '{cleaned}'")

    return cleaned


def validate_and_clean_model_name(name: str, auto_clean: bool = True) -> str:
    """
    Validate model name and optionally clean if malformed.

    Args:
        name: Model name to validate
        auto_clean: If True, automatically clean malformed names. If False, raise error.

    Returns:
        Cleaned and validated model name

    Raises:
        ValueError: If name is invalid and auto_clean=False
    """
    # First check if name is valid
    is_valid, error_msg = validate_model_name(name)

    if is_valid:
        return name

    # Name is invalid
    if not auto_clean:
        raise ValueError(f"Invalid model name: {error_msg}")

    # Auto-clean the name
    cleaned = clean_model_name(name)

    # Validate the cleaned name
    is_valid_after_clean, error_msg_after_clean = validate_model_name(cleaned)

    if not is_valid_after_clean:
        logger.error(
            f"Failed to clean model name '{name}': {error_msg_after_clean}. "
            f"Cleaned result: '{cleaned}'"
        )
        # Return the cleaned version anyway, even if still not perfect
        return cleaned

    logger.info(f"Cleaned malformed model name: '{name}' -> '{cleaned}'")
    return cleaned
