"""Integration test configuration.

All tests in this directory are marked as integration tests and excluded
from the default CI run. Run with: pytest -m integration
"""

import pytest


def pytest_collection_modifyitems(items):
    """Auto-mark all tests in tests/integration/ with the integration marker."""
    for item in items:
        if "/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
