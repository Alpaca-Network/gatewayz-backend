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
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None, raising=False)
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


def test_get_client_uses_region_specific_keys(monkeypatch):
    """Ensure region-specific API keys override the default key."""

    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "default-key")
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", "china-key")
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", "intl-key")

    captured_keys: list[str] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured_keys.append(kwargs["api_key"])

    monkeypatch.setattr(acc, "OpenAI", FakeOpenAI)

    acc.get_alibaba_cloud_client(region_override="china")
    acc.get_alibaba_cloud_client(region_override="international")

    assert captured_keys == ["china-key", "intl-key"]


def test_region_attempt_order_uses_available_key_for_all_regions(monkeypatch):
    """When only one region-specific key is set, all regions should still be attempted.

    This enables failover when the user has misconfigured which key goes with which region.
    """

    _reset_region_state(monkeypatch)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", "intl-key", raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_REGION", "china", raising=False)
    monkeypatch.setattr(acc, "_inferred_region", None, raising=False)

    attempts = acc._region_attempt_order()

    # Both regions should be included because the intl-key will be used as fallback for china
    # This enables failover if user misconfigured which key goes with which region
    assert attempts == ["china", "international"], "should include all regions with fallback key"


def test_failover_with_misconfigured_region_key(monkeypatch):
    """Test that failover works when user sets international key but it only works for China.

    This is the scenario from the bug report: user sets ALIBABA_CLOUD_API_KEY_INTERNATIONAL
    with a key that actually only works for the China endpoint.
    """

    _reset_region_state(monkeypatch)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None, raising=False)
    # User mistakenly put their China key in the INTERNATIONAL variable
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", "china-key-in-wrong-var", raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_REGION", "international", raising=False)

    call_order: list[str] = []

    def fake_get_client(region_override=None):
        region = region_override or "international"
        call_order.append(region)

        failing = MagicMock()
        failing.models.list.side_effect = Exception("Error code: 401 - Incorrect API key provided")

        success_response = types.SimpleNamespace(data=[{"id": "qwen-max"}])
        succeeding = MagicMock()
        succeeding.models.list.return_value = success_response

        # The key only works for China, not international
        return failing if region == "international" else succeeding

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    response = acc.list_alibaba_models()

    assert response.data[0]["id"] == "qwen-max"
    assert call_order == ["international", "china"], "should failover from international to china"
    assert acc._inferred_region == "china"


def test_failover_works_with_explicit_region_env_var(monkeypatch):
    """Test that failover still works when ALIBABA_CLOUD_REGION env var is set explicitly.

    Previously, setting _explicit_region (from env var) would disable failover entirely,
    causing 'attempted 1 of 1 regions' errors when the configured region failed.
    """

    # Simulate the module-level env var capture for ALIBABA_CLOUD_REGION
    monkeypatch.setattr(acc, "_explicit_region", "international", raising=False)
    monkeypatch.setattr(acc, "_inferred_region", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None, raising=False)

    call_order: list[str] = []

    def fake_get_client(region_override=None):
        region = region_override or "international"
        call_order.append(region)

        failing = MagicMock()
        failing.models.list.side_effect = Exception(
            "Error code: 401 - {'error': {'message': 'Incorrect API key provided. '}}"
        )

        success_response = types.SimpleNamespace(data=[{"id": "qwen-max"}])
        succeeding = MagicMock()
        succeeding.models.list.return_value = success_response

        # Simulate the key only working for China endpoint
        return failing if region == "international" else succeeding

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    response = acc.list_alibaba_models()

    assert response.data[0]["id"] == "qwen-max"
    # Key assertion: should now attempt both regions even with _explicit_region set
    assert call_order == ["international", "china"], (
        "should failover to china even when _explicit_region is set"
    )
    assert acc._inferred_region == "china", "should remember successful region"


def test_inferred_region_is_cached_after_failover_with_explicit_region(monkeypatch):
    """Ensure the successful region is cached even when explicit region was set.

    After a successful failover, subsequent requests should go directly to the
    working region without re-attempting the failing region.
    """

    # Set explicit region to international, but key only works for china
    monkeypatch.setattr(acc, "_explicit_region", "international", raising=False)
    monkeypatch.setattr(acc, "_inferred_region", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key", raising=False)

    call_order: list[str] = []

    class FakeChat:
        def __init__(self):
            self.completions = MagicMock()
            self.completions.create.return_value = {"ok": True}

    def fake_get_client(region_override=None):
        region = region_override or "international"
        call_order.append(region)

        failing = MagicMock()
        failing.models.list.side_effect = Exception("Error code: 401")
        failing.chat = MagicMock()
        failing.chat.completions = MagicMock()
        failing.chat.completions.create.side_effect = Exception("Error code: 401")

        succeeding = MagicMock()
        succeeding.models.list.return_value = types.SimpleNamespace(data=[])
        succeeding.chat = FakeChat()

        return failing if region == "international" else succeeding

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    # First request: should failover from international to china
    acc.list_alibaba_models()
    assert call_order == ["international", "china"]
    assert acc._inferred_region == "china"

    # Second request: should go directly to china (the inferred region)
    call_order.clear()
    acc.make_alibaba_cloud_request_openai(messages=[], model="qwen-max")
    assert call_order == ["china"], "should use cached inferred region"
