"""
Database Safety Utilities

Provides defensive wrappers and utilities for safe database operations.
Prevents common errors like IndexError, KeyError, and type mismatches.
"""

import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DatabaseResultError(Exception):
    """Raised when database result is invalid or unexpected."""

    pass


def safe_get_first(
    result: Any,
    error_message: str = "No data returned from database",
    validate_keys: list[str] | None = None,
) -> dict[str, Any]:
    """
    Safely get the first item from a Supabase query result.

    Args:
        result: Supabase query result object
        error_message: Custom error message if data is empty
        validate_keys: Optional list of keys that must exist in the result

    Returns:
        First item as dictionary

    Raises:
        DatabaseResultError: If result is empty or invalid
        KeyError: If validate_keys specified and key missing

    Example:
        >>> result = supabase.table("users").select("*").eq("id", user_id).execute()
        >>> user = safe_get_first(result, "User not found", validate_keys=["id", "email"])
    """
    if not hasattr(result, "data"):
        raise DatabaseResultError(f"Invalid result object: {error_message}")

    if not result.data or len(result.data) == 0:
        raise DatabaseResultError(error_message)

    first_item = result.data[0]

    if not isinstance(first_item, dict):
        raise DatabaseResultError(f"Expected dict from database, got {type(first_item).__name__}")

    # Validate required keys exist
    if validate_keys:
        missing_keys = [key for key in validate_keys if key not in first_item]
        if missing_keys:
            raise KeyError(
                f"Missing required keys in database result: {missing_keys}. "
                f"Available keys: {list(first_item.keys())}"
            )

    return first_item


def safe_get_value(  # noqa: UP047
    data: dict[str, Any],
    key: str,
    default: T = None,
    expected_type: type | None = None,
    allow_none: bool = True,
) -> T:
    """
    Safely get a value from a dictionary with type checking.

    Args:
        data: Dictionary to get value from
        key: Key to retrieve
        default: Default value if key missing or None
        expected_type: Expected type of the value (validates if provided)
        allow_none: Whether None is an acceptable value

    Returns:
        Value from dictionary or default

    Raises:
        TypeError: If value type doesn't match expected_type

    Example:
        >>> user_data = {"id": 123, "credits": "5.50", "name": None}
        >>> user_id = safe_get_value(user_data, "id", expected_type=int)
        >>> credits = safe_get_value(user_data, "credits", default=0.0, expected_type=float)
        >>> name = safe_get_value(user_data, "name", default="Unknown", allow_none=False)
    """
    value = data.get(key, default)

    # Handle None values
    if value is None:
        if not allow_none and default is not None:
            logger.warning(f"Key '{key}' is None but allow_none=False, using default: {default}")
            return default
        return value

    # Type validation
    if expected_type is not None and value is not None:
        # Try to convert if types don't match
        if not isinstance(value, expected_type):
            try:
                original_type = type(value).__name__
                value = expected_type(value)
                logger.debug(
                    f"Converted key '{key}' from {original_type} to {expected_type.__name__}"
                )
            except (ValueError, TypeError) as e:
                raise TypeError(
                    f"Key '{key}' has type {type(value).__name__}, expected {expected_type.__name__}: {e}"
                )

    return value


def safe_execute_query(  # noqa: UP047
    query_fn: Callable[[], Any],
    operation_name: str,
    fallback_value: T | None = None,
    raise_on_error: bool = True,
) -> T:
    """
    Safely execute a database query with error handling.

    Args:
        query_fn: Function that executes the query
        operation_name: Name of operation for logging
        fallback_value: Value to return if query fails and raise_on_error=False
        raise_on_error: Whether to raise exception or return fallback on error

    Returns:
        Query result or fallback_value

    Raises:
        DatabaseResultError: If query fails and raise_on_error=True

    Example:
        >>> def get_user():
        ...     return supabase.table("users").select("*").eq("id", 123).execute()
        >>> result = safe_execute_query(get_user, "get_user", fallback_value=[])
    """
    try:
        result = query_fn()
        return result
    except Exception as e:
        error_msg = f"{operation_name} failed: {str(e)}"
        logger.error(error_msg, exc_info=True)

        if raise_on_error:
            raise DatabaseResultError(error_msg) from e

        logger.warning(f"Returning fallback value for {operation_name}: {fallback_value}")
        return fallback_value


def safe_get_list(
    result: Any,
    error_message: str = "No data returned from database",
    min_items: int = 0,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """
    Safely get a list from a Supabase query result with validation.

    Args:
        result: Supabase query result object
        error_message: Custom error message if validation fails
        min_items: Minimum number of items required
        max_items: Maximum number of items allowed (optional)

    Returns:
        List of dictionaries

    Raises:
        DatabaseResultError: If validation fails

    Example:
        >>> result = supabase.table("users").select("*").execute()
        >>> users = safe_get_list(result, "No users found", min_items=1)
    """
    if not hasattr(result, "data"):
        raise DatabaseResultError(f"Invalid result object: {error_message}")

    if not isinstance(result.data, list):
        raise DatabaseResultError(f"Expected list from database, got {type(result.data).__name__}")

    if len(result.data) < min_items:
        raise DatabaseResultError(
            f"{error_message} (expected at least {min_items}, got {len(result.data)})"
        )

    if max_items is not None and len(result.data) > max_items:
        raise DatabaseResultError(
            f"{error_message} (expected at most {max_items}, got {len(result.data)})"
        )

    return result.data


def safe_update_credits(
    current_credits: Any,
    delta: float,
    min_credits: float = 0.0,
    operation_name: str = "credit update",
) -> float:
    """
    Safely update credit balance with validation.

    Args:
        current_credits: Current credit balance (any type, will be converted)
        delta: Amount to add (positive) or subtract (negative)
        min_credits: Minimum allowed credit balance
        operation_name: Name of operation for error messages

    Returns:
        New credit balance

    Raises:
        ValueError: If resulting balance would be below minimum

    Example:
        >>> new_balance = safe_update_credits(10.50, -2.00)
        >>> new_balance = safe_update_credits("15.75", -20.00)  # Raises ValueError
    """
    try:
        credits = float(current_credits) if current_credits is not None else 0.0
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid credit value in {operation_name}: {current_credits} ({e})")

    try:
        delta_float = float(delta)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid delta value in {operation_name}: {delta} ({e})")

    new_balance = credits + delta_float

    if new_balance < min_credits:
        # SECURITY: Log details server-side, keep ValueError generic to avoid
        # leaking exact credit amounts if this propagates to an HTTP response.
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "%s: Insufficient credits. current=%.6f, delta=%.6f, "
            "would_result_in=%.6f, minimum=%.6f",
            operation_name,
            credits,
            delta_float,
            new_balance,
            min_credits,
        )
        raise ValueError(f"{operation_name}: Insufficient credits. Please add credits to continue.")

    return round(new_balance, 6)  # Round to 6 decimal places to avoid floating point issues


def validate_dict_structure(
    data: Any,
    required_keys: list[str],
    context: str = "data validation",
) -> dict[str, Any]:
    """
    Validate that data is a dictionary with required keys.

    Args:
        data: Data to validate
        required_keys: List of keys that must be present
        context: Context for error messages

    Returns:
        Validated dictionary

    Raises:
        TypeError: If data is not a dictionary
        KeyError: If required keys are missing

    Example:
        >>> user_data = {"id": 123, "email": "test@example.com"}
        >>> validated = validate_dict_structure(user_data, ["id", "email"], "user data")
    """
    if not isinstance(data, dict):
        raise TypeError(f"{context}: Expected dict, got {type(data).__name__}")

    missing_keys = [key for key in required_keys if key not in data]
    if missing_keys:
        raise KeyError(
            f"{context}: Missing required keys: {missing_keys}. "
            f"Available keys: {list(data.keys())}"
        )

    return data


def safe_int_convert(
    value: Any,
    default: int = 0,
    context: str = "integer conversion",
) -> int:
    """
    Safely convert value to integer.

    Args:
        value: Value to convert
        default: Default value if conversion fails
        context: Context for logging

    Returns:
        Integer value or default

    Example:
        >>> count = safe_int_convert("123")  # Returns 123
        >>> count = safe_int_convert("invalid", default=0)  # Returns 0
    """
    if value is None:
        return default

    try:
        return int(value)
    except (ValueError, TypeError):
        logger.warning(f"{context}: Could not convert '{value}' to int, using default: {default}")
        return default


def safe_float_convert(
    value: Any,
    default: float = 0.0,
    context: str = "float conversion",
) -> float:
    """
    Safely convert value to float.

    Args:
        value: Value to convert
        default: Default value if conversion fails
        context: Context for logging

    Returns:
        Float value or default

    Example:
        >>> price = safe_float_convert("12.50")  # Returns 12.5
        >>> price = safe_float_convert("invalid", default=0.0)  # Returns 0.0
    """
    if value is None:
        return default

    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning(f"{context}: Could not convert '{value}' to float, using default: {default}")
        return default
