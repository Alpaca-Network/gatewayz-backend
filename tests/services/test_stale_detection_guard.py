"""When the hourly sync is allowed to count models missing upstream."""

import pytest

from src.services.model_catalog_sync import _should_run_stale_detection


@pytest.mark.parametrize(
    "fetched, active, expected",
    [
        (5, 11, True),  # Groq 2026-10: most models retired, still must clean up
        (1, 3, True),
        (0, 11, False),  # nothing fetched: treat as an API failure
        (5, 0, False),  # nothing active to retire
        (30, 49, True),
        (20, 49, False),  # large provider, under half: likely a partial fetch
        (10, 20, True),
        (9, 20, False),
    ],
)
def test_should_run_stale_detection(fetched, active, expected):
    assert _should_run_stale_detection(fetched, active) is expected
