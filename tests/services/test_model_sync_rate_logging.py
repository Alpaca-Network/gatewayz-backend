"""A cosmetic log line must not be able to fail a sync that succeeded.

`sync_provider_models` logged throughput as `count / metrics[...]` inline in
three f-strings — one per phase (fetch, transform, db_sync). The durations come
from `time.time() - start`, so a phase that finishes inside one clock tick
makes the divisor zero, the f-string raises ZeroDivisionError, the caller
catches it, and a **successful** sync is reported as `success: False`.

This is the mechanism behind `test_model_catalog_sync_delisting`, which was
written off as "a flaky delisting test" repeatedly — including twice by me in
one day. It is timing-dependent, not random, and it reproduces more readily
serially than under xdist, which is why it read as rare rather than as a bug.

A fast sync is the *good* case. It should not be the failing one.
"""

from __future__ import annotations

import pytest

from src.services.model_catalog_sync import _rate_per_sec


class TestRatePerSec:
    @pytest.mark.parametrize("seconds", [0, 0.0, -0.0, None])
    def test_an_unusable_duration_yields_a_string_not_an_exception(self, seconds):
        # The original failure: a zero divisor inside an f-string.
        assert _rate_per_sec(120, seconds) == "n/a"

    def test_a_negative_duration_does_not_produce_a_negative_rate(self):
        # Clock adjustments can make an elapsed time negative. "-4000 models/sec"
        # in a log is worse than "n/a" because it looks like a measurement.
        assert _rate_per_sec(120, -0.03) == "n/a"

    def test_a_real_duration_still_reports_a_rate(self):
        # Guarding must not silently turn every rate into "n/a" — that would
        # remove the signal instead of the crash.
        assert _rate_per_sec(120, 2.0) == "60"

    def test_zero_models_in_real_time_is_a_rate_not_an_error(self):
        assert _rate_per_sec(0, 1.5) == "0"


def test_the_log_lines_do_not_divide_inline():
    """Pins the fix at its source, not just the helper.

    The helper passing proves nothing if a future edit reintroduces
    `count / metrics['...']` directly in an f-string. This asserts the three
    call sites route through the guard.
    """
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "services" / "model_catalog_sync.py"
    text = src.read_text(encoding="utf-8")

    for divisor in ("fetch_duration", "transform_duration", "db_sync_duration"):
        assert f"/ metrics['{divisor}']" not in text, (
            f"metrics['{divisor}'] is divided by inline again. A sync fast enough to "
            "finish within one clock tick will raise ZeroDivisionError from a logging "
            "f-string and report a successful sync as success: False. "
            "Use _rate_per_sec()."
        )

    assert (
        text.count("_rate_per_sec(") >= 4
    ), "expected the helper's definition plus three call sites"
