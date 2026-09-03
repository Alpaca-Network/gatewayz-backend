"""Tests for src.services.gpu.model_classes (PR #2288 review fix round 1, C1)."""

from unittest.mock import patch

import pytest

from src.services.gpu.model_classes import is_known_model_id, known_model_class


@pytest.fixture
def sb():
    return None


def test_known_model_class_matches_allow_listed_ids(sb):
    assert known_model_class("llama-3.1-8b-instruct") == "small"
    assert known_model_class("qwen2.5-32b-instruct") == "medium"
    assert known_model_class("llama-3.1-70b-instruct") == "large"


def test_known_model_class_strips_community_prefix_and_lowercases(sb):
    assert known_model_class("community/Llama-3.1-8B-Instruct") == "small"


def test_known_model_class_returns_none_for_unknown_id(sb):
    """Regression for C1: an unknown id must NEVER fall back to a guessed
    class (the old regex-on-the-id behavior), even one that LOOKS like a
    large model by name -- that's exactly the exploit this allow-list
    closes."""
    assert known_model_class("community/definitely-a-70b-model") is None
    assert is_known_model_id("community/definitely-a-70b-model") is False


def test_overrides_add_a_new_model_id(sb):
    with patch("src.services.gpu.model_classes.Config") as mock_config:
        mock_config.COMMUNITY_MODEL_CLASS_OVERRIDES = '{"some-new-model": "medium"}'
        assert known_model_class("some-new-model") == "medium"


def test_overrides_can_reclassify_a_builtin_entry(sb):
    with patch("src.services.gpu.model_classes.Config") as mock_config:
        mock_config.COMMUNITY_MODEL_CLASS_OVERRIDES = '{"llama-3.1-8b-instruct": "medium"}'
        assert known_model_class("llama-3.1-8b-instruct") == "medium"


def test_malformed_overrides_are_ignored(sb):
    with patch("src.services.gpu.model_classes.Config") as mock_config:
        mock_config.COMMUNITY_MODEL_CLASS_OVERRIDES = "not json"
        # falls back to the builtin list rather than raising
        assert known_model_class("llama-3.1-8b-instruct") == "small"


def test_overrides_with_invalid_class_value_are_ignored_entirely(sb):
    with patch("src.services.gpu.model_classes.Config") as mock_config:
        mock_config.COMMUNITY_MODEL_CLASS_OVERRIDES = '{"x": "huge"}'
        assert known_model_class("x") is None
        # builtin list still intact
        assert known_model_class("llama-3.1-8b-instruct") == "small"


def test_overrides_unset_falls_back_to_builtin(sb):
    with patch("src.services.gpu.model_classes.Config") as mock_config:
        mock_config.COMMUNITY_MODEL_CLASS_OVERRIDES = None
        assert known_model_class("llama-3.1-8b-instruct") == "small"
