"""Test to verify Claude workflow fixing."""
import pytest


def test_simple_math():
    """Simple test that will fail."""
    assert 2 + 2 == 5, "This test is intentionally failing for workflow verification"


def test_string_concat():
    """Another simple test."""
    result = "hello" + " " + "world"
    assert result == "hello world"
