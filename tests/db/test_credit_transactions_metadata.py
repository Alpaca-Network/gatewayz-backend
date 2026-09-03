"""credit_transactions.description/metadata must never carry free-form content
(threat model G6) — description is bounded, metadata keys are allow-listed."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.credit_transactions import log_credit_transaction

# Fixture named `sb` per conftest convention — exempts this module from the
# database-availability autouse skip (see tests/db/test_usage_since.py).


@pytest.fixture
def sb():
    return None


def _mock_client_capturing_insert():
    client = MagicMock()
    captured = {}

    def _insert(data):
        captured["data"] = data
        result = MagicMock()
        result.execute.return_value = MagicMock(data=[{**data, "id": 1}])
        return result

    client.table.return_value.insert.side_effect = _insert
    return client, captured


@patch("src.db.credit_transactions.execute_with_retry")
@patch("src.db.credit_transactions.get_supabase_client")
def _log(mock_get_client, mock_retry, **kwargs):
    client, captured = _mock_client_capturing_insert()
    mock_get_client.return_value = client
    # execute_with_retry(do_insert, ...) -> call do_insert(client) directly
    mock_retry.side_effect = lambda fn, **_: fn(client)

    log_credit_transaction(
        user_id=1,
        amount=-0.01,
        transaction_type="api_usage",
        description=kwargs.get("description", "API usage"),
        balance_before=1.0,
        balance_after=0.99,
        metadata=kwargs.get("metadata"),
    )
    return captured["data"]


class TestMetadataAllowList:
    def test_known_keys_pass_through(self, sb):
        data = _log(
            metadata={"model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 5}
        )
        assert data["metadata"] == {
            "model": "gpt-4o",
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

    def test_unknown_key_is_dropped(self, sb, caplog):
        with caplog.at_level("WARNING"):
            data = _log(metadata={"model": "gpt-4o", "prompt": "tell me a secret"})
        assert data["metadata"] == {"model": "gpt-4o"}
        assert "prompt" not in data["metadata"]

    def test_error_message_key_is_specifically_dropped(self, sb):
        """chat_streaming.py's auto-refund path passes str(exception)[:200]
        under this key — it must never reach the ledger."""
        data = _log(
            metadata={
                "model": "gpt-4o",
                "error_type": "timeout_error",
                "error_message": "canary prompt fragment 424242",
            }
        )
        assert "error_message" not in data["metadata"]
        assert data["metadata"] == {"model": "gpt-4o", "error_type": "timeout_error"}

    def test_none_metadata_becomes_empty_dict(self, sb):
        data = _log(metadata=None)
        assert data["metadata"] == {}

    def test_dropped_key_logs_a_warning(self, sb, caplog):
        with caplog.at_level("WARNING"):
            _log(metadata={"prompt": "leak"})
        assert any("dropping non-allow-listed metadata keys" in r.message for r in caplog.records)

    @pytest.mark.parametrize(
        "key",
        [
            "from_tier",
            "to_tier",
            "subscription_id",
            "refund_reason",
            "admin_user_id",
            "duration_minutes",
            "stripe_session_id",
        ],
    )
    def test_every_known_legitimate_key_survives(self, sb, key):
        """Regression guard: every key a real caller uses today must stay
        allow-listed (see the comment block above _METADATA_ALLOWED_KEYS for
        the full provenance list)."""
        data = _log(metadata={key: "x"})
        assert key in data["metadata"]


class TestDescriptionTruncation:
    def test_description_under_limit_is_unchanged(self, sb):
        data = _log(description="API usage - gpt-4o")
        assert data["description"] == "API usage - gpt-4o"

    def test_description_over_limit_is_truncated_to_200_chars(self, sb):
        data = _log(description="x" * 500)
        assert len(data["description"]) == 200

    def test_none_description_becomes_empty_string(self, sb):
        data = _log(description=None)
        assert data["description"] == ""
