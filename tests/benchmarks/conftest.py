"""Pytest configuration for benchmark tests."""

import pytest


def pytest_configure(config):
    """Register the benchmark marker."""
    config.addinivalue_line(
        "markers",
        "benchmark: marks tests as benchmark tests (deselect with '-m \"not benchmark\"')",
    )
