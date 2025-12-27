"""
Logging utility functions for secure and sanitized logging.
"""


def mask_key(k: str) -> str:
    """
    Mask an API key for secure logging.

    Args:
        k: API key string to mask

    Returns:
        Masked key showing only last 4 characters
    """
    return f"...{k[-4:]}" if k and len(k) >= 4 else "****"


def sanitize_for_logging(value: str) -> str:
    """
    Sanitize a value for safe logging.

    Currently a passthrough, but can be extended to remove sensitive data.

    Args:
        value: String to sanitize

    Returns:
        Sanitized string safe for logging
    """
    return value
