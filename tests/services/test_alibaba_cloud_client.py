import types
from unittest.mock import MagicMock

from src.config import Config
from src.services import alibaba_cloud_client as acc


def _reset_region_state(monkeypatch):
    monkeypatch.setattr(acc, "_explicit_region", None, raising=False)
    monkeypatch.setattr(acc, "_inferred_region", None, raising=False)


def test_list_alibaba_models_retries_with_china_region(monkeypatch):
    """Ensure we fall back to the China endpoint when the default fails with auth errors."""

    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")
    _reset_region_state(monkeypatch)

    call_order: list[str] = []

    def fake_get_client(region_override=None):
        region = region_override or "international"
        call_order.append(region)

        failing = MagicMock()
        failing.models.list.side_effect = Exception("Incorrect API key provided (401)")

        success_response = types.SimpleNamespace(data=[{"id": "qwen-max"}])
        succeeding = MagicMock()
        succeeding.models.list.return_value = success_response

        return failing if region == "international" else succeeding

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    response = acc.list_alibaba_models()

    assert response.data[0]["id"] == "qwen-max"
    assert call_order == ["international", "china"], "should retry in china region"
    assert acc._inferred_region == "china"


def test_chat_request_prefers_cached_region(monkeypatch):
    """Subsequent requests should re-use the inferred working region to avoid extra failures."""

    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")
    _reset_region_state(monkeypatch)
    monkeypatch.setattr(acc, "_inferred_region", "china", raising=False)

    captured_regions: list[str | None] = []

    class FakeChat:
        def __init__(self):
            self.completions = MagicMock()
            self.completions.create.return_value = {"ok": True}

    fake_client = MagicMock()
    fake_client.chat = FakeChat()

    def fake_get_client(region_override=None):
        captured_regions.append(region_override)
        return fake_client

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    result = acc.make_alibaba_cloud_request_openai(messages=[], model="qwen-max")

    assert result == {"ok": True}
    assert captured_regions == ["china"], "should use inferred region without retry"
    fake_client.chat.completions.create.assert_called_once()
