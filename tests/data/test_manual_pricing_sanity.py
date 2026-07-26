"""The manual pricing fallback must stay plausible and correctly scaled.

This file is hand-maintained and had gone six months stale, stopping at
claude-opus-4.5 and o3-mini. That was survivable only because the OpenRouter
price book took over intake — but this is the fallback for when the book is
unavailable, so a wrong entry here is a wrong bill.

Values are per-1M tokens; everything else in the system is per-token. That
factor of a million is the same one that shipped claude-opus-5 at 5E-12, so it
gets an explicit guard.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

_PRICING_PATH = Path(__file__).resolve().parents[2] / "src" / "data" / "manual_pricing.json"

# Sections that are model->pricing maps; the rest are metadata or nested shapes.
_SKIP = {"_metadata", "image_pricing"}


@pytest.fixture(scope="module")
def pricing():
    return json.loads(_PRICING_PATH.read_text())


def _entries(pricing):
    for gateway, models in pricing.items():
        if gateway in _SKIP or not isinstance(models, dict):
            continue
        for model_id, entry in models.items():
            if isinstance(entry, dict) and "prompt" in entry:
                yield gateway, model_id, entry


class TestPricingValues:
    def test_every_price_parses_as_a_number(self, pricing):
        bad = []
        for gateway, model_id, entry in _entries(pricing):
            for field in ("prompt", "completion"):
                try:
                    Decimal(str(entry.get(field, "0")))
                except Exception:
                    bad.append(f"{gateway}/{model_id}.{field}={entry.get(field)!r}")
        assert not bad, bad

    def test_no_entry_is_written_in_per_token_units(self, pricing):
        """A per-token value here is a 1e6 under-charge.

        Real per-1M prices run from ~0.01 (cheap open models) to ~100 (frontier).
        Anything non-zero below 0.0001 is almost certainly per-token by mistake.
        """
        suspect = []
        for gateway, model_id, entry in _entries(pricing):
            for field in ("prompt", "completion"):
                value = Decimal(str(entry.get(field, "0")))
                if value != 0 and value < Decimal("0.0001"):
                    suspect.append(f"{gateway}/{model_id}.{field}={value}")
        assert not suspect, f"per-token values in a per-1M file: {suspect}"

    def test_no_price_is_absurdly_high(self, pricing):
        """Catches the inverse error — a per-1M value multiplied again."""
        suspect = [
            f"{g}/{m}.{f}={entry[f]}"
            for g, m, entry in _entries(pricing)
            for f in ("prompt", "completion")
            if Decimal(str(entry.get(f, "0"))) > Decimal("1000")
        ]
        assert not suspect, f"implausibly expensive: {suspect}"

    def test_no_scientific_notation(self, pricing):
        """Plain decimals only — this file is edited by hand."""
        sci = [
            f"{g}/{m}.{f}={entry[f]}"
            for g, m, entry in _entries(pricing)
            for f in ("prompt", "completion")
            if "e" in str(entry.get(f, "")).lower()
        ]
        assert not sci, sci


class TestCoverage:
    def test_the_currently_served_flagships_are_present(self, pricing):
        """The fallback is worthless if it lacks the models we actually sell."""
        required = {
            "anthropic": ["anthropic/claude-opus-5", "anthropic/claude-sonnet-5"],
            "openai": ["openai/gpt-4o-mini"],
            "xai": ["grok-4"],
        }
        missing = [
            f"{gw}/{mid}"
            for gw, ids in required.items()
            for mid in ids
            if mid not in pricing.get(gw, {})
        ]
        assert not missing, f"fallback missing served models: {missing}"

    def test_metadata_records_when_it_was_refreshed(self, pricing):
        assert pricing["_metadata"]["last_updated"] >= "2026-07-26"
