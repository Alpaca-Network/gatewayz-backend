"""Tests for A6, the ad-creative agent.

The load-bearing behaviour is the refusal. Generating creative for an unproven
channel is how a $300 test line becomes a $3,000 spend without anyone deciding
to spend it — the ads exist, so they get run.
"""

import json
from unittest.mock import Mock

import pytest

from agents.base import UsageLedger, UsageRecord
from agents.producer import (
    CAC_TARGET_USD,
    MIN_CONVERSIONS_FOR_SIGNAL,
    ChannelResult,
    Producer,
)


def _client(text=None):
    client = Mock()
    payload = text or json.dumps(
        {
            "variants": [
                {
                    "hypothesis": "cost",
                    "headline": "Short headline",
                    "body": "b",
                    "creative_brief": "c",
                    "cta": "Try it",
                }
            ]
        }
    )
    client.complete.return_value = (
        payload,
        UsageRecord("", "m", 10, 5, 0, 0.001, 1),
    )
    return client


def _ledger(tmp_path):
    return UsageLedger(path=tmp_path / "ledger.jsonl")


class TestChannelResult:
    def test_cac_is_spend_over_conversions(self):
        assert ChannelResult("reddit", 300.0, 15).cac_usd == 20.0

    def test_no_conversions_yields_no_cac(self):
        assert ChannelResult("reddit", 300.0, 0).cac_usd is None

    def test_below_signal_threshold_is_not_actionable(self):
        result = ChannelResult("reddit", 100.0, MIN_CONVERSIONS_FOR_SIGNAL - 1)
        assert result.has_signal is False
        assert result.earned_budget is False

    def test_good_cac_with_enough_conversions_earns_budget(self):
        assert ChannelResult("reddit", 200.0, 20).earned_budget is True

    def test_cac_above_target_does_not_earn_budget(self):
        result = ChannelResult("reddit", 1000.0, 20)  # $50 CAC
        assert result.cac_usd > CAC_TARGET_USD
        assert result.earned_budget is False

    def test_cheap_cac_from_two_conversions_is_still_not_signal(self):
        """A $5 CAC off 2 conversions is noise, not a result."""
        result = ChannelResult("reddit", 10.0, 2)
        assert result.cac_usd < CAC_TARGET_USD
        assert result.earned_budget is False


class TestGating:
    def test_no_results_is_an_error_not_an_empty_run(self, tmp_path):
        agent = Producer(client=_client(), ledger=_ledger(tmp_path), results=[])
        run = agent.run()
        assert run.errors
        assert run.outputs == []

    def test_unproven_channel_produces_no_creative(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("reddit", 1000.0, 20)],  # $50 CAC
        )
        run = agent.run()
        assert run.outputs == []
        agent.client.complete.assert_not_called()

    def test_refusal_explains_itself(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("reddit", 1000.0, 20)],
        )
        run = agent.run()
        assert any("exceeds the" in n for n in run.notes)
        assert any("intended outcome" in n for n in run.notes)

    def test_thin_data_is_called_out_as_thin_not_as_failure(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("reddit", 50.0, 3)],
        )
        run = agent.run()
        assert any("do not scale" in n for n in run.notes)
        assert run.errors == []

    def test_proven_channel_generates_creative(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("reddit", 200.0, 20)],
        )
        run = agent.run()
        assert len(run.outputs) == 1
        assert run.outputs[0]["channel"] == "reddit"

    def test_only_proven_channels_are_generated_for(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[
                ChannelResult("reddit", 200.0, 20),  # good
                ChannelResult("x", 2000.0, 20),  # bad CAC
            ],
        )
        run = agent.run()
        channels = {o["channel"] for o in run.outputs}
        assert channels == {"reddit"}


class TestCreativeGeneration:
    def test_unknown_platform_is_an_error_not_a_guess(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("tiktok", 200.0, 20)],
        )
        run = agent.run()
        assert run.errors
        assert any("platform spec" in e for e in run.errors)

    def test_output_is_marked_for_review(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("reddit", 200.0, 20)],
        )
        run = agent.run()
        assert run.outputs[0]["status"] == "draft-review-before-upload"

    def test_overlong_headline_is_flagged(self, tmp_path):
        """Truncated copy on a paid impression is money spent on half a sentence."""
        long_headline = "x" * 60
        payload = json.dumps(
            {"variants": [{"hypothesis": "h", "headline": long_headline, "body": "b"}]}
        )
        agent = Producer(
            client=_client(payload),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("google_search", 200.0, 20)],  # 30-char limit
        )
        run = agent.run()
        assert any("exceed" in n for n in run.notes)

    def test_measured_cac_is_recorded_with_the_creative(self, tmp_path):
        agent = Producer(
            client=_client(),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("reddit", 200.0, 20)],
        )
        run = agent.run()
        assert run.outputs[0]["measured_cac_usd"] == 10.0

    def test_unparseable_response_is_recorded(self, tmp_path):
        agent = Producer(
            client=_client("not json"),
            ledger=_ledger(tmp_path),
            results=[ChannelResult("reddit", 200.0, 20)],
        )
        run = agent.run()
        assert run.errors
