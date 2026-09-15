"""The standing benchmark harness spends nothing unless a human says so.

This script is the one piece of the attribution work that costs real money
every time it runs, so the tests are weighted toward the ways a capped,
gated tool stops being either.

The three-state rule matters as much as the cap: a model that was never
probed must not be indistinguishable from one that answered instantly. A
reader who cannot tell those apart learns to trust a number that is not
there, which is the failure this whole month has been about.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Same convention as test_benchmark_config.py in this directory.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "benchmarks"))

import standing_harness as harness  # noqa: E402

pytestmark = pytest.mark.benchmark


MODELS = [
    {"id": "a/one", "pricing": {"prompt": 0.000001, "completion": 0.000002}},
    {"id": "a/two", "pricing": {"prompt": 0.000003, "completion": 0.000004}},
]


def test_the_estimate_is_computed_before_anything_is_sent():
    est = harness.estimate_cost(MODELS, samples=3)
    assert est > 0
    # 3 samples x (8 prompt x rate + 8 completion x rate), both models.
    expected = 3 * (8 * 0.000001 + 8 * 0.000002) + 3 * (8 * 0.000003 + 8 * 0.000004)
    assert est == pytest.approx(expected)


def test_a_model_with_no_pricing_contributes_nothing_rather_than_raising():
    # An unpriced model must not crash the estimate -- it is exactly the case
    # that would otherwise stop a scheduled run from ever starting.
    assert harness.estimate_cost([{"id": "x/y"}], samples=1) == 0.0


def test_budget_is_checked_against_the_estimate_not_the_spend(monkeypatch):
    # The refusal has to happen BEFORE the first request, or the cap is a
    # report rather than a limit.
    sent = []
    monkeypatch.setattr(harness, "probe", lambda *a, **k: sent.append(a) or (0.1, {}))
    monkeypatch.setattr(harness, "served_models", lambda _k: MODELS)
    monkeypatch.setattr(harness, "_key", lambda: "gw_test")
    monkeypatch.setattr("sys.argv", ["h", "--execute", "--budget-usd", "0.0000001"])
    assert harness.main() == 2
    assert sent == [], "a refused run must send nothing"


def test_without_execute_nothing_is_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(harness, "probe", lambda *a, **k: sent.append(a) or (0.1, {}))
    monkeypatch.setattr(harness, "served_models", lambda _k: MODELS)
    monkeypatch.setattr(harness, "_key", lambda: "gw_test")
    monkeypatch.setattr("sys.argv", ["h"])  # no --execute
    assert harness.main() == 0
    assert sent == [], "dry run must be a dry run"


def test_a_model_past_the_budget_is_not_attempted_not_zero(monkeypatch):
    # The distinction the whole output turns on.
    calls = {"n": 0}

    def _probe(model, key, timeout=60):
        calls["n"] += 1
        return 0.2, {"input_tokens": 8, "output_tokens": 8}

    monkeypatch.setattr(harness, "probe", _probe)
    # A budget that the first model's cost exhausts.
    results = harness.run(MODELS, "gw_test", samples=1, budget=0.00002)
    states = {r.model: r.state for r in results}
    assert states["a/one"] == "measured"
    assert states["a/two"] == "not-attempted"
    second = [r for r in results if r.model == "a/two"][0]
    assert second.samples == [], "an unattempted model must carry no numbers at all"
    assert second.cost_usd == 0.0
    assert "budget" in (second.error or "")


def test_a_failing_model_is_failed_not_missing(monkeypatch):
    def _probe(model, key, timeout=60):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(harness, "probe", _probe)
    results = harness.run(MODELS[:1], "gw_test", samples=1, budget=1.0)
    assert results[0].state == "failed"
    assert results[0].error
    assert results[0].samples == []


def test_stats_are_empty_when_nothing_was_measured():
    r = harness.ModelResult(model="x", state="not-attempted")
    assert r.stats() == {}, "no samples must yield no statistics, not zeros"
