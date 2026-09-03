"""activity_log.metadata must never carry free-form content (threat model
G6) — only token-breakdown / auth-event keys are allow-listed."""

from unittest.mock import MagicMock, patch

import pytest

from src.db.activity import log_activity

# Fixture named `sb` per conftest convention — exempts this module from the
# database-availability autouse skip.


@pytest.fixture
def sb():
    return None


def _log(metadata):
    client = MagicMock()
    captured = {}

    def _insert(data):
        captured["data"] = data
        result = MagicMock()
        result.execute.return_value = MagicMock(data=[{**data, "id": 1}])
        return result

    client.table.return_value.insert.side_effect = _insert

    with patch("src.db.activity.get_supabase_client", return_value=client):
        log_activity(
            user_id=1,
            model="gpt-4o",
            provider="openai",
            tokens=100,
            cost=0.01,
            metadata=metadata,
        )
    return captured["data"]


def test_known_inference_keys_pass_through(sb):
    data = _log({"prompt_tokens": 60, "completion_tokens": 40, "endpoint": "/v1/chat/completions"})
    assert data["metadata"] == {
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "endpoint": "/v1/chat/completions",
    }


def test_known_auth_event_keys_pass_through(sb):
    """routes/auth.py logs login/registration events through the same writer
    with a different metadata shape — both must be supported."""
    data = _log({"action": "login", "auth_method": "privy", "is_new_user": False})
    assert data["metadata"] == {
        "action": "login",
        "auth_method": "privy",
        "is_new_user": False,
    }


def test_registration_event_initial_credits_key_passes_through(sb):
    data = _log(
        {
            "action": "register",
            "auth_method": "privy",
            "privy_user_id": "did:privy:canary",
            "is_new_user": True,
            "initial_credits": 5.0,
        }
    )
    assert data["metadata"]["initial_credits"] == 5.0
    assert data["metadata"]["privy_user_id"] == "did:privy:canary"


def test_unknown_key_is_dropped_with_warning(sb, caplog):
    with caplog.at_level("WARNING"):
        data = _log({"prompt_tokens": 10, "prompt": "leak this"})
    assert data["metadata"] == {"prompt_tokens": 10}
    assert any("dropping non-allow-listed metadata keys" in r.message for r in caplog.records)


def test_none_metadata_becomes_empty_dict(sb):
    data = _log(None)
    assert data["metadata"] == {}
