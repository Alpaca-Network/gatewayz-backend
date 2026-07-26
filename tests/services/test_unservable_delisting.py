"""An operator delisting must survive the next sync.

The sync-time gate judges price and provider routability. Neither can tell that
a model the provider happily lists is unusable on our chat-completions contract:
OpenAI's /models returns gpt-audio, o1-pro and gpt-3.5-turbo-instruct next to
ordinary chat models, but they need the audio, responses and completions
endpoints. Without a durable marker the hourly sync re-lists them and users pick
models that cannot answer.
"""

from unittest.mock import MagicMock

from src.services import model_catalog_sync


def _client(rows):
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.execute.return_value = MagicMock(data=rows)
    client = MagicMock()
    client.table.return_value = q
    return client


class TestLoadUnservableModelIds:
    def test_returns_only_rows_flagged_unservable(self, monkeypatch):
        rows = [
            {"provider_model_id": "openai/gpt-audio", "metadata": {"delist_reason": "unservable"}},
            {"provider_model_id": "openai/o1-pro", "metadata": {"delist_reason": "unservable"}},
            # Delisted for a reason the gate recomputes on its own — must not be
            # pinned, or a model that becomes priced again could never come back.
            {"provider_model_id": "openai/cheap", "metadata": {"delist_reason": "unpriced"}},
            {"provider_model_id": "openai/other", "metadata": {}},
        ]
        monkeypatch.setattr(
            "src.config.supabase_config.get_client_for_query", lambda **_k: _client(rows)
        )

        assert model_catalog_sync._load_unservable_model_ids(1) == {
            "openai/gpt-audio",
            "openai/o1-pro",
        }

    def test_fails_open_on_db_error(self, monkeypatch):
        """A lookup failure must not abort the sync."""

        def _boom(**_k):
            raise RuntimeError("db down")

        monkeypatch.setattr("src.config.supabase_config.get_client_for_query", _boom)

        assert model_catalog_sync._load_unservable_model_ids(1) == set()

    def test_empty_when_nothing_is_flagged(self, monkeypatch):
        monkeypatch.setattr(
            "src.config.supabase_config.get_client_for_query", lambda **_k: _client([])
        )

        assert model_catalog_sync._load_unservable_model_ids(1) == set()


def test_flag_on_a_currently_active_row_still_pins_it(monkeypatch):
    """The flag is the operator's intent and must stand on its own.

    Filtering the lookup on is_active=false would mean a flag set on a live row
    never takes effect, and a row a previous sync re-activated could never be
    pinned back down — the exact loop this fix exists to close.
    """
    rows = [
        {"provider_model_id": "openai/gpt-audio", "metadata": {"delist_reason": "unservable"}},
    ]
    captured = {}

    def _client_recording_filters(**_k):
        q = MagicMock()
        q.select.return_value = q

        def _eq(field, value):
            captured[field] = value
            return q

        q.eq.side_effect = _eq
        q.execute.return_value = MagicMock(data=rows)
        client = MagicMock()
        client.table.return_value = q
        return client

    monkeypatch.setattr(
        "src.config.supabase_config.get_client_for_query", _client_recording_filters
    )

    assert model_catalog_sync._load_unservable_model_ids(7) == {"openai/gpt-audio"}
    assert "is_active" not in captured, "lookup must not require the row to be inactive already"
    assert captured["provider_id"] == 7


class TestMarkerSurvivesAnAlreadyInactiveModel:
    """The marker must be re-stamped even when the model is already inactive.

    Requiring is_active before stamping silently lost the flag: a model that is
    unservable AND unpriced is already inactive by the time the gate runs, so it
    was skipped, the transform's own delist_reason="unpriced" stood, and
    _load_unservable_model_ids stopped matching it. The next sync able to price
    the model then brought it back — which is exactly how all ten
    operator-delisted models returned to the catalog once their prices were
    repaired.
    """

    def _apply_gate(self, db_models, unservable):
        """Mirror the gate in sync_provider_models over a list of rows."""
        pinned = 0
        for db_model in db_models:
            if db_model.get("provider_model_id") not in unservable:
                continue
            if db_model.get("is_active"):
                db_model["is_active"] = False
            md = db_model.get("metadata") or {}
            md["delist_reason"] = "unservable"
            db_model["metadata"] = md
            pinned += 1
        return pinned

    def test_already_inactive_model_keeps_the_unservable_marker(self):
        rows = [
            {
                "provider_model_id": "openai/gpt-audio",
                "is_active": False,  # already delisted this pass, e.g. unpriced
                "metadata": {"delist_reason": "unpriced"},
            }
        ]

        assert self._apply_gate(rows, {"openai/gpt-audio"}) == 1
        assert rows[0]["metadata"]["delist_reason"] == "unservable"
        assert rows[0]["is_active"] is False

    def test_active_model_is_still_flipped_and_stamped(self):
        rows = [
            {"provider_model_id": "openai/o1-pro", "is_active": True, "metadata": {}},
        ]

        assert self._apply_gate(rows, {"openai/o1-pro"}) == 1
        assert rows[0]["is_active"] is False
        assert rows[0]["metadata"]["delist_reason"] == "unservable"

    def test_unflagged_models_are_untouched(self):
        rows = [
            {"provider_model_id": "openai/gpt-4o-mini", "is_active": True, "metadata": {}},
        ]

        assert self._apply_gate(rows, {"openai/o1-pro"}) == 0
        assert rows[0]["is_active"] is True
        assert "delist_reason" not in rows[0]["metadata"]
