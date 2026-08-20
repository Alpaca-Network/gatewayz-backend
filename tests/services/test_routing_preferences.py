"""Tests for src.services.routing_preferences."""

from unittest.mock import patch

from src.services.routing_preferences import (
    DEFAULT_INDUSTRY,
    DEFAULT_MODE,
    get_routing_preferences_for_key,
)


def test_valid_mode_and_industry_round_trip():
    user = {"settings": {"routing_mode": "quality", "routing_industry": "legal"}}
    with patch("src.services.routing_preferences.get_user", return_value=user):
        mode, industry = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == "quality"
    assert industry == "legal"


def test_invalid_mode_falls_back_to_default():
    user = {"settings": {"routing_mode": "not_a_real_mode", "routing_industry": "legal"}}
    with patch("src.services.routing_preferences.get_user", return_value=user):
        mode, industry = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == DEFAULT_MODE
    assert industry == "legal"


def test_invalid_industry_falls_back_to_default():
    user = {"settings": {"routing_mode": "price", "routing_industry": "not_a_real_industry"}}
    with patch("src.services.routing_preferences.get_user", return_value=user):
        mode, industry = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == "price"
    assert industry == DEFAULT_INDUSTRY


def test_balanced_is_not_a_valid_user_selectable_mode():
    """"balanced" is intentionally internal-only (model_selector's fallback);
    a stray "balanced" value in settings must fall back to the default,
    never pass through as if it were user-selected."""
    user = {"settings": {"routing_mode": "balanced"}}
    with patch("src.services.routing_preferences.get_user", return_value=user):
        mode, _ = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == DEFAULT_MODE


def test_missing_user_falls_back_to_defaults():
    with patch("src.services.routing_preferences.get_user", return_value=None):
        mode, industry = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == DEFAULT_MODE
    assert industry == DEFAULT_INDUSTRY


def test_missing_settings_key_falls_back_to_defaults():
    user = {"id": 1}  # no "settings" key at all
    with patch("src.services.routing_preferences.get_user", return_value=user):
        mode, industry = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == DEFAULT_MODE
    assert industry == DEFAULT_INDUSTRY


def test_settings_value_of_none_falls_back_to_defaults():
    user = {"settings": None}
    with patch("src.services.routing_preferences.get_user", return_value=user):
        mode, industry = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == DEFAULT_MODE
    assert industry == DEFAULT_INDUSTRY


def test_lookup_exception_falls_back_to_defaults_without_raising():
    with patch("src.services.routing_preferences.get_user", side_effect=RuntimeError("db down")):
        mode, industry = get_routing_preferences_for_key("gw_live_abc123")

    assert mode == DEFAULT_MODE
    assert industry == DEFAULT_INDUSTRY


def test_no_api_key_falls_back_to_defaults_without_calling_get_user():
    with patch("src.services.routing_preferences.get_user") as mock_get_user:
        mode, industry = get_routing_preferences_for_key(None)

    assert mode == DEFAULT_MODE
    assert industry == DEFAULT_INDUSTRY
    mock_get_user.assert_not_called()
