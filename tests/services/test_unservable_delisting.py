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
