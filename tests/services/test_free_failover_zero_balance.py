"""$0-balance / anonymous callers on a free model must never fail over to a paid provider."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.services.provider_failover import restrict_chain_for_zero_balance_free_model as restrict

FREE = "meta-llama/llama-3.2-3b-instruct:free"
CHAIN = ["openrouter", "together", "fireworks"]


@pytest.fixture(autouse=True)
def _known_free():
    with patch(
        "src.services.cache.model_capabilities_cache.is_free_model", side_effect=lambda m: True
    ):
        yield


def test_zero_balance_user_restricted_to_free_provider():
    user = {"subscription_allowance": 0, "purchased_credits": 0}
    assert restrict(FREE, list(CHAIN), user=user, is_anonymous=False) == ["openrouter"]


def test_anonymous_restricted_to_free_provider():
    assert restrict(FREE, list(CHAIN), user=None, is_anonymous=True) == ["openrouter"]


def test_funded_user_keeps_full_chain():
    user = {"subscription_allowance": 0, "purchased_credits": 5.0}
    assert restrict(FREE, list(CHAIN), user=user, is_anonymous=False) == CHAIN


def test_allowance_only_user_keeps_full_chain():
    user = {"subscription_allowance": 3.0, "purchased_credits": 0}
    assert restrict(FREE, list(CHAIN), user=user, is_anonymous=False) == CHAIN


def test_no_free_provider_in_chain_raises_clear_error():
    user = {"subscription_allowance": 0, "purchased_credits": 0}
    with pytest.raises(HTTPException) as ei:
        restrict(FREE, ["together", "fireworks"], user=user, is_anonymous=False)
    assert ei.value.status_code == 503
    assert ei.value.detail["error"]["code"] == "free_model_unavailable"


def test_no_free_provider_returns_empty_when_not_raising():
    assert restrict(FREE, ["together"], user=None, is_anonymous=True, raise_if_empty=False) == []


def test_paid_model_is_untouched_for_zero_balance():
    with patch("src.services.cache.model_capabilities_cache.is_free_model", return_value=False):
        assert restrict("openai/gpt-4o", list(CHAIN), user=None, is_anonymous=True) == CHAIN
    assert restrict("openai/gpt-4o", list(CHAIN), user=None, is_anonymous=True) == CHAIN


def test_unknown_free_suffix_is_not_treated_as_free():
    with patch("src.services.cache.model_capabilities_cache.is_free_model", return_value=False):
        assert restrict(FREE, list(CHAIN), user=None, is_anonymous=True) == CHAIN
