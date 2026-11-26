"""Sweep verdicts go to the live catalog row, not an inactive copy.

One catalog id can sit in several ``models`` rows (an inactive OpenRouter row,
an inactive Vercel row, the live OpenAI row). The lookup used to take the first
row returned, usually an inactive one, so passing models stayed "unknown".
"""

from unittest.mock import MagicMock, patch

from src.services.monitoring import model_health_sweep as sweep

ROWS = [
    {"id": 114, "model_name": "GPT-5", "provider_model_id": "openai/gpt-5", "is_active": False},
    {"id": 14670, "model_name": "GPT-5", "provider_model_id": "openai/gpt-5", "is_active": False},
    {"id": 17113, "model_name": "GPT-5", "provider_model_id": "openai/gpt-5", "is_active": True},
]


class _Query:
    """Minimal stand-in for the Supabase query builder used by the lookup."""

    def __init__(self):
        self.filters = {}

    def select(self, *_):
        return self

    def in_(self, column, values):
        self.filters["in"] = (column, set(values))
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def execute(self):
        column, values = self.filters["in"]
        rows = [r for r in ROWS if r.get(column) in values]
        if "is_active" in self.filters:
            rows = [r for r in rows if r["is_active"] == self.filters["is_active"]]
        return MagicMock(data=rows)


def test_verdict_maps_to_the_active_row():
    client = MagicMock()
    client.table.side_effect = lambda name: _Query()
    with patch("src.config.supabase_config.get_client_for_query", return_value=client):
        mapping = sweep._build_model_pk_map(["openai/gpt-5"])
    assert mapping == {"openai/gpt-5": 17113}


def test_id_with_only_inactive_rows_is_left_unmapped():
    client = MagicMock()
    client.table.side_effect = lambda name: _Query()
    original = list(ROWS)
    ROWS[:] = [r for r in ROWS if not r["is_active"]]
    try:
        with patch("src.config.supabase_config.get_client_for_query", return_value=client):
            assert sweep._build_model_pk_map(["openai/gpt-5"]) == {}
    finally:
        ROWS[:] = original
