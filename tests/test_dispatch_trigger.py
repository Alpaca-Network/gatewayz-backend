"""Test to verify the claude-on-failure dispatch trigger."""


def test_simple_failure():
    """This test intentionally fails to trigger claude-on-failure workflow."""
    assert 1 + 1 == 3, "Intentional test failure to trigger claude-on-failure"
