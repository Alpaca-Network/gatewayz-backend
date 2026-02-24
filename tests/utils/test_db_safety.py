"""
Tests for database safety utilities
"""

from unittest.mock import Mock

import pytest

from src.utils.db_safety import (
    DatabaseResultError,
    safe_execute_query,
    safe_float_convert,
    safe_get_first,
    safe_get_list,
    safe_get_value,
    safe_int_convert,
    safe_update_credits,
    validate_dict_structure,
)


class TestSafeGetFirst:
    """Tests for safe_get_first function."""

    def test_safe_get_first_success(self):
        """Test successful extraction of first item."""
        result = Mock()
        result.data = [{"id": 123, "name": "test"}]

        item = safe_get_first(result)
        assert item == {"id": 123, "name": "test"}

    def test_safe_get_first_with_key_validation(self):
        """Test extraction with key validation."""
        result = Mock()
        result.data = [{"id": 123, "name": "test", "email": "test@example.com"}]

        item = safe_get_first(result, validate_keys=["id", "name"])
        assert item["id"] == 123
        assert item["name"] == "test"

    def test_safe_get_first_empty_data(self):
        """Test error when data is empty."""
        result = Mock()
        result.data = []

        with pytest.raises(DatabaseResultError, match="No data returned"):
            safe_get_first(result)

    def test_safe_get_first_none_data(self):
        """Test error when data is None."""
        result = Mock()
        result.data = None

        with pytest.raises(DatabaseResultError):
            safe_get_first(result)

    def test_safe_get_first_missing_key(self):
        """Test error when required key is missing."""
        result = Mock()
        result.data = [{"id": 123}]

        with pytest.raises(KeyError, match="Missing required keys"):
            safe_get_first(result, validate_keys=["id", "name", "email"])

    def test_safe_get_first_invalid_result_object(self):
        """Test error when result object doesn't have data attribute."""
        result = {"some": "dict"}

        with pytest.raises(DatabaseResultError, match="Invalid result object"):
            safe_get_first(result)

    def test_safe_get_first_non_dict_item(self):
        """Test error when item is not a dictionary."""
        result = Mock()
        result.data = ["not a dict"]

        with pytest.raises(DatabaseResultError, match="Expected dict"):
            safe_get_first(result)

    def test_safe_get_first_custom_error_message(self):
        """Test custom error message."""
        result = Mock()
        result.data = []

        with pytest.raises(DatabaseResultError, match="Custom error"):
            safe_get_first(result, error_message="Custom error")


class TestSafeGetValue:
    """Tests for safe_get_value function."""

    def test_safe_get_value_existing_key(self):
        """Test getting existing key."""
        data = {"id": 123, "name": "test"}
        assert safe_get_value(data, "id") == 123
        assert safe_get_value(data, "name") == "test"

    def test_safe_get_value_missing_key_with_default(self):
        """Test getting missing key returns default."""
        data = {"id": 123}
        assert safe_get_value(data, "name", default="Unknown") == "Unknown"

    def test_safe_get_value_none_value_allowed(self):
        """Test None value when allowed."""
        data = {"name": None}
        assert safe_get_value(data, "name", allow_none=True) is None

    def test_safe_get_value_none_value_not_allowed(self):
        """Test None value uses default when not allowed."""
        data = {"name": None}
        assert safe_get_value(data, "name", default="Unknown", allow_none=False) == "Unknown"

    def test_safe_get_value_type_validation_success(self):
        """Test type validation with correct type."""
        data = {"count": 42}
        assert safe_get_value(data, "count", expected_type=int) == 42

    def test_safe_get_value_type_conversion(self):
        """Test automatic type conversion."""
        data = {"count": "42"}
        result = safe_get_value(data, "count", expected_type=int)
        assert result == 42
        assert isinstance(result, int)

    def test_safe_get_value_type_conversion_float(self):
        """Test float conversion."""
        data = {"price": "12.50"}
        result = safe_get_value(data, "price", expected_type=float)
        assert result == 12.5
        assert isinstance(result, float)

    def test_safe_get_value_type_conversion_failure(self):
        """Test type conversion failure raises TypeError."""
        data = {"count": "invalid"}
        with pytest.raises(TypeError, match="expected int"):
            safe_get_value(data, "count", expected_type=int)


class TestSafeExecuteQuery:
    """Tests for safe_execute_query function."""

    def test_safe_execute_query_success(self):
        """Test successful query execution."""

        def successful_query():
            return {"data": [1, 2, 3]}

        result = safe_execute_query(successful_query, "test query")
        assert result == {"data": [1, 2, 3]}

    def test_safe_execute_query_with_error_raise(self):
        """Test error with raise_on_error=True."""

        def failing_query():
            raise ValueError("Database error")

        with pytest.raises(DatabaseResultError, match="test query failed"):
            safe_execute_query(failing_query, "test query", raise_on_error=True)

    def test_safe_execute_query_with_error_fallback(self):
        """Test error with fallback value."""

        def failing_query():
            raise ValueError("Database error")

        result = safe_execute_query(
            failing_query, "test query", fallback_value=[], raise_on_error=False
        )
        assert result == []


class TestSafeGetList:
    """Tests for safe_get_list function."""

    def test_safe_get_list_success(self):
        """Test successful list extraction."""
        result = Mock()
        result.data = [{"id": 1}, {"id": 2}, {"id": 3}]

        items = safe_get_list(result)
        assert len(items) == 3
        assert items[0]["id"] == 1

    def test_safe_get_list_min_items(self):
        """Test minimum items validation."""
        result = Mock()
        result.data = [{"id": 1}]

        with pytest.raises(DatabaseResultError, match="expected at least 3"):
            safe_get_list(result, min_items=3)

    def test_safe_get_list_max_items(self):
        """Test maximum items validation."""
        result = Mock()
        result.data = [{"id": i} for i in range(10)]

        with pytest.raises(DatabaseResultError, match="expected at most 5"):
            safe_get_list(result, max_items=5)

    def test_safe_get_list_empty_allowed(self):
        """Test empty list allowed with min_items=0."""
        result = Mock()
        result.data = []

        items = safe_get_list(result, min_items=0)
        assert items == []


class TestSafeUpdateCredits:
    """Tests for safe_update_credits function."""

    def test_safe_update_credits_add(self):
        """Test adding credits."""
        new_balance = safe_update_credits(10.0, 5.0)
        assert new_balance == 15.0

    def test_safe_update_credits_subtract(self):
        """Test subtracting credits."""
        new_balance = safe_update_credits(10.0, -3.0)
        assert new_balance == 7.0

    def test_safe_update_credits_string_conversion(self):
        """Test automatic string to float conversion."""
        new_balance = safe_update_credits("10.50", "2.50")
        assert new_balance == 13.0

    def test_safe_update_credits_insufficient_balance(self):
        """Test error when balance would go negative."""
        with pytest.raises(ValueError, match="Insufficient credits"):
            safe_update_credits(5.0, -10.0)

    def test_safe_update_credits_custom_minimum(self):
        """Test custom minimum balance."""
        new_balance = safe_update_credits(10.0, -8.0, min_credits=-5.0)
        assert new_balance == 2.0

    def test_safe_update_credits_invalid_current(self):
        """Test error with invalid current credits."""
        with pytest.raises(ValueError, match="Invalid credit value"):
            safe_update_credits("invalid", 5.0)

    def test_safe_update_credits_invalid_delta(self):
        """Test error with invalid delta."""
        with pytest.raises(ValueError, match="Invalid delta value"):
            safe_update_credits(10.0, "invalid")

    def test_safe_update_credits_floating_point_precision(self):
        """Test floating point precision handling."""
        new_balance = safe_update_credits(0.1, 0.2)
        # Should handle floating point precision issues
        assert abs(new_balance - 0.3) < 1e-6

    def test_safe_update_credits_none_current(self):
        """Test None current credits defaults to 0."""
        new_balance = safe_update_credits(None, 5.0)
        assert new_balance == 5.0


class TestValidateDictStructure:
    """Tests for validate_dict_structure function."""

    def test_validate_dict_structure_success(self):
        """Test successful validation."""
        data = {"id": 123, "name": "test", "email": "test@example.com"}
        validated = validate_dict_structure(data, ["id", "name"])
        assert validated == data

    def test_validate_dict_structure_all_keys(self):
        """Test validation with all required keys."""
        data = {"id": 123, "name": "test"}
        validated = validate_dict_structure(data, ["id", "name"])
        assert validated["id"] == 123

    def test_validate_dict_structure_missing_keys(self):
        """Test error with missing keys."""
        data = {"id": 123}
        with pytest.raises(KeyError, match="Missing required keys"):
            validate_dict_structure(data, ["id", "name", "email"])

    def test_validate_dict_structure_not_dict(self):
        """Test error when data is not a dict."""
        data = ["not", "a", "dict"]
        with pytest.raises(TypeError, match="Expected dict"):
            validate_dict_structure(data, ["id"])

    def test_validate_dict_structure_none_data(self):
        """Test error when data is None."""
        with pytest.raises(TypeError, match="Expected dict"):
            validate_dict_structure(None, ["id"])


class TestSafeIntConvert:
    """Tests for safe_int_convert function."""

    def test_safe_int_convert_string(self):
        """Test converting string to int."""
        assert safe_int_convert("123") == 123

    def test_safe_int_convert_float(self):
        """Test converting float to int."""
        assert safe_int_convert(123.7) == 123

    def test_safe_int_convert_none(self):
        """Test None returns default."""
        assert safe_int_convert(None, default=0) == 0

    def test_safe_int_convert_invalid(self):
        """Test invalid value returns default."""
        assert safe_int_convert("invalid", default=0) == 0

    def test_safe_int_convert_custom_default(self):
        """Test custom default value."""
        assert safe_int_convert("invalid", default=999) == 999


class TestSafeFloatConvert:
    """Tests for safe_float_convert function."""

    def test_safe_float_convert_string(self):
        """Test converting string to float."""
        assert safe_float_convert("12.5") == 12.5

    def test_safe_float_convert_int(self):
        """Test converting int to float."""
        assert safe_float_convert(10) == 10.0

    def test_safe_float_convert_none(self):
        """Test None returns default."""
        assert safe_float_convert(None, default=0.0) == 0.0

    def test_safe_float_convert_invalid(self):
        """Test invalid value returns default."""
        assert safe_float_convert("invalid", default=0.0) == 0.0

    def test_safe_float_convert_custom_default(self):
        """Test custom default value."""
        assert safe_float_convert("invalid", default=99.9) == 99.9
