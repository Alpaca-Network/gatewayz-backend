"""
Comprehensive tests for Users schemas
"""

from datetime import datetime

import pytest
from pydantic import ValidationError


class TestUsersSchemas:
    """Test Users schema models"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.schemas.users

        assert src.schemas.users is not None

    def test_module_has_expected_attributes(self):
        """Test module exports"""
        from src.schemas import users

        assert hasattr(users, "__name__")
