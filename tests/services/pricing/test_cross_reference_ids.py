"""Anthropic and OpenRouter spell the same model differently.

Anthropic ships "claude-opus-4-6" and dated snapshots like
"claude-haiku-4-5-20251001". OpenRouter lists "claude-opus-4.6" and
"claude-haiku-4.5". normalize_model_name cannot bridge them — it maps "." to "p"
so "4.6" becomes "4p6" while "4-6" stays "4-6" — and it also drives provider
dispatch, so widening it there risks mis-routing an inference call.

Four Anthropic models sat unpriced, and therefore unlistable, purely because of
this. The price was in the index the whole time under a dotted name.
"""

import pytest

from src.services.pricing.pricing_lookup import (
    _cross_reference_candidates,
    _get_cross_reference_pricing,
)


class TestCandidateForms:
    def test_a_dated_snapshot_yields_the_undated_dotted_form(self):
        candidates = _cross_reference_candidates(
            "anthropic/claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001"
        )

        assert "claude-haiku-4.5" in candidates
        assert "anthropic/claude-haiku-4.5" in candidates
        # The literal id is still tried first.
        assert candidates[0] == "anthropic/claude-haiku-4-5-20251001"

    def test_a_hyphenated_version_yields_the_dotted_form(self):
        candidates = _cross_reference_candidates("anthropic/claude-opus-4-6", "claude-opus-4-6")

        assert "claude-opus-4.6" in candidates

    @pytest.mark.parametrize("model_id", ["openai/gpt-4", "anthropic/claude-3-opus"])
    def test_hyphens_not_between_digits_are_left_alone(self, model_id):
        """gpt-4 must not become gpt.4, and claude-3-opus must stay itself."""
        candidates = _cross_reference_candidates(model_id, model_id.split("/")[-1])

        assert not any("." in c for c in candidates)

    def test_a_digit_run_is_converted(self):
        """claude-3-5-sonnet is OpenRouter's claude-3.5-sonnet."""
        candidates = _cross_reference_candidates("anthropic/claude-3-5-sonnet", "claude-3-5-sonnet")

        assert "claude-3.5-sonnet" in candidates

    def test_no_duplicates(self):
        candidates = _cross_reference_candidates("openai/gpt-4", "gpt-4")

        assert len(candidates) == len(set(candidates))


class TestLookupAgainstADottedIndex:
    @pytest.fixture
    def index(self):
        # Shaped like the real one: OpenRouter's dotted, undated ids.
        return {
            "anthropic/claude-opus-4.6": {"prompt": "0.000005", "completion": "0.000025"},
            "claude-opus-4.6": {"prompt": "0.000005", "completion": "0.000025"},
            "anthropic/claude-haiku-4.5": {"prompt": "0.000001", "completion": "0.000005"},
            "claude-haiku-4.5": {"prompt": "0.000001", "completion": "0.000005"},
        }

    def test_hyphenated_id_finds_the_dotted_price(self, index):
        pricing = _get_cross_reference_pricing("anthropic/claude-opus-4-6", index, "anthropic")

        assert pricing is not None
        assert float(pricing["prompt"]) == 5e-6

    def test_dated_snapshot_finds_the_undated_price(self, index):
        pricing = _get_cross_reference_pricing(
            "anthropic/claude-haiku-4-5-20251001", index, "anthropic"
        )

        assert pricing is not None
        assert float(pricing["prompt"]) == 1e-6

    def test_an_unknown_model_still_returns_nothing(self, index):
        """The widened matching must not invent a price for something absent."""
        assert (
            _get_cross_reference_pricing("anthropic/claude-nonexistent-9", index, "anthropic")
            is None
        )
