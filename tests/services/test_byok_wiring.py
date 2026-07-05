"""Tests for the BYOK inference/billing wiring.

Covers the context-variable key injection (the mechanism that lets the handler
substitute a customer's own provider key without threading api_key through every
provider client) and the routing-fee billing swap.
"""

from unittest.mock import MagicMock

import pytest

from src.services import byok


def test_context_is_provider_scoped():
    assert byok.get_byok_key_for("openrouter") is None
    token = byok.set_byok_context("openrouter", "sk-user-abc")
    try:
        assert byok.get_byok_key_for("openrouter") == "sk-user-abc"
        # A key bound for one provider must never be used for another.
        assert byok.get_byok_key_for("groq") is None
    finally:
        byok.reset_byok_context(token)
    assert byok.get_byok_key_for("openrouter") is None


def test_get_provider_api_key_prefers_byok_context(monkeypatch):
    from src.services import gateway_registry

    # Without context: normal resolution (platform key or None).
    baseline = gateway_registry.get_provider_api_key("openrouter")

    token = byok.set_byok_context("openrouter", "sk-user-xyz")
    try:
        assert gateway_registry.get_provider_api_key("openrouter") == "sk-user-xyz"
        # A different provider is unaffected by the openrouter binding.
        assert gateway_registry.get_provider_api_key("groq") != "sk-user-xyz"
    finally:
        byok.reset_byok_context(token)

    assert gateway_registry.get_provider_api_key("openrouter") == baseline


def test_openrouter_pooled_client_uses_byok_key(monkeypatch):
    import src.services.connection_pool as pool

    captured = {}

    def _fake_pooled(provider, base_url, api_key, default_headers=None, timeout=None):
        captured["api_key"] = api_key
        return MagicMock()

    monkeypatch.setattr(pool, "get_pooled_client", _fake_pooled)

    token = byok.set_byok_context("openrouter", "sk-user-999")
    try:
        pool.get_openrouter_pooled_client()
    finally:
        byok.reset_byok_context(token)

    assert captured["api_key"] == "sk-user-999"


def test_resolve_byok_key_none_without_user():
    assert byok.resolve_byok_key(None, "openrouter") is None


def test_resolve_byok_key_returns_decrypted(monkeypatch):
    import src.db.user_provider_keys as upk

    monkeypatch.setattr(upk, "get_decrypted_provider_key", lambda uid, slug: "sk-decrypted")
    assert byok.resolve_byok_key(42, "openrouter") == "sk-decrypted"


def test_resolve_byok_key_never_raises(monkeypatch):
    import src.db.user_provider_keys as upk

    def _boom(uid, slug):
        raise RuntimeError("db down")

    monkeypatch.setattr(upk, "get_decrypted_provider_key", _boom)
    assert byok.resolve_byok_key(42, "openrouter") is None  # swallowed → platform fallback


@pytest.mark.parametrize(
    "is_byok,rate,cost,expected",
    [
        (False, "0.05", 1.0, 1.0),   # not BYOK → full cost
        (True, "0.0", 1.0, 0.0),     # BYOK, 0% fee → free routing
        (True, "0.05", 1.0, 0.05),   # BYOK, 5% fee
        (True, "0.10", 2.0, 0.20),   # BYOK, 10% fee
    ],
)
def test_apply_byok_fee(monkeypatch, is_byok, rate, cost, expected):
    monkeypatch.setenv("BYOK_ROUTING_FEE_RATE", rate)
    from src.handlers.chat_handler import ChatInferenceHandler

    handler = ChatInferenceHandler("gw_test_key")
    handler.is_byok = is_byok
    assert handler._apply_byok_fee(cost) == expected


# ---------------------------------------------------------------------------
# Integration: drive the real _call_provider and prove the customer key is live
# DURING the provider call, is_byok is set on success, and the binding is cleared
# afterwards. A stub provider records what get_byok_key_for() sees at call time.
# ---------------------------------------------------------------------------
def _make_handler_with_user(monkeypatch, user_id=1):
    from src.handlers.chat_handler import ChatInferenceHandler

    handler = ChatInferenceHandler("gw_test_key")
    handler.is_anonymous = False
    handler.user = {"id": user_id}
    return handler


def _force_openrouter_async(monkeypatch):
    # Make _call_provider take the OpenRouter branch deterministically.
    import src.services.gateway_registry as gr

    monkeypatch.setattr(
        gr, "get_gateway_registry", lambda: {"openrouter": {"async_streaming": True}}
    )


def _install_recording_provider(monkeypatch):
    """Patch the OpenRouter request fn to record the BYOK key visible mid-call."""
    seen = {}

    def _stub(messages, model, **kwargs):
        from src.services.byok import get_byok_key_for

        seen["key"] = get_byok_key_for("openrouter")
        return MagicMock()

    monkeypatch.setattr("src.handlers.chat_handler.make_openrouter_request_openai", _stub)
    return seen


def test_call_provider_binds_customer_key_during_call(monkeypatch):
    from src.config import Config
    import src.db.user_provider_keys as upk

    monkeypatch.setattr(Config, "BYOK_ENABLED", True)
    monkeypatch.setattr(upk, "get_decrypted_provider_key", lambda uid, slug: "sk-byok-live")
    _force_openrouter_async(monkeypatch)
    seen = _install_recording_provider(monkeypatch)

    handler = _make_handler_with_user(monkeypatch)
    handler._call_provider("openrouter", "openai/gpt-4o", [{"role": "user", "content": "hi"}])

    # The customer's key was bound while the provider client ran...
    assert seen["key"] == "sk-byok-live"
    # ...is_byok recorded on success...
    assert handler.is_byok is True
    # ...and the binding is cleared once the call returns (no leak).
    assert byok.get_byok_key_for("openrouter") is None


def test_call_provider_no_key_is_not_byok(monkeypatch):
    from src.config import Config
    import src.db.user_provider_keys as upk

    monkeypatch.setattr(Config, "BYOK_ENABLED", True)
    monkeypatch.setattr(upk, "get_decrypted_provider_key", lambda uid, slug: None)  # user has none
    _force_openrouter_async(monkeypatch)
    seen = _install_recording_provider(monkeypatch)

    handler = _make_handler_with_user(monkeypatch)
    handler._call_provider("openrouter", "openai/gpt-4o", [{"role": "user", "content": "hi"}])

    assert seen["key"] is None  # nothing bound → platform key resolution
    assert handler.is_byok is False


def test_call_provider_flag_off_ignores_stored_key(monkeypatch):
    from src.config import Config
    import src.db.user_provider_keys as upk

    monkeypatch.setattr(Config, "BYOK_ENABLED", False)  # master switch off
    # Even though the user HAS a key, it must not be used.
    monkeypatch.setattr(upk, "get_decrypted_provider_key", lambda uid, slug: "sk-should-not-use")
    _force_openrouter_async(monkeypatch)
    seen = _install_recording_provider(monkeypatch)

    handler = _make_handler_with_user(monkeypatch)
    handler._call_provider("openrouter", "openai/gpt-4o", [{"role": "user", "content": "hi"}])

    assert seen["key"] is None
    assert handler.is_byok is False


def test_call_provider_failure_leaves_is_byok_false_and_unbinds(monkeypatch):
    from fastapi import HTTPException

    from src.config import Config
    import src.db.user_provider_keys as upk

    monkeypatch.setattr(Config, "BYOK_ENABLED", True)
    monkeypatch.setattr(upk, "get_decrypted_provider_key", lambda uid, slug: "sk-byok-live")
    _force_openrouter_async(monkeypatch)

    def _boom(messages, model, **kwargs):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr("src.handlers.chat_handler.make_openrouter_request_openai", _boom)

    handler = _make_handler_with_user(monkeypatch)
    with pytest.raises(HTTPException):
        handler._call_provider("openrouter", "openai/gpt-4o", [{"role": "user", "content": "hi"}])

    # A failed BYOK attempt must not mark the request BYOK (else a failover to the
    # platform key would be under-billed), and the binding must be released.
    assert handler.is_byok is False
    assert byok.get_byok_key_for("openrouter") is None


@pytest.mark.asyncio
async def test_call_provider_stream_binds_at_creation_not_across_yields(monkeypatch):
    """Streaming: the key must be live when the stream is CREATED, but cleared
    before any chunk is yielded — a ContextVar.set() left active across an async-
    generator yield would leak into the caller's context."""
    from src.config import Config
    import src.db.user_provider_keys as upk

    monkeypatch.setattr(Config, "BYOK_ENABLED", True)
    monkeypatch.setattr(upk, "get_decrypted_provider_key", lambda uid, slug: "sk-byok-stream")
    _force_openrouter_async(monkeypatch)

    seen = {}

    async def _fake_stream_create(messages, model, **kwargs):
        seen["key_at_creation"] = byok.get_byok_key_for("openrouter")

        async def _gen():
            for c in ["a", "b"]:
                yield c

        return _gen()

    monkeypatch.setattr(
        "src.handlers.chat_handler.make_openrouter_request_openai_stream_async",
        _fake_stream_create,
    )

    handler = _make_handler_with_user(monkeypatch)
    keys_during_yield = []
    async for _chunk in handler._call_provider_stream(
        "openrouter", "openai/gpt-4o", [{"role": "user", "content": "hi"}]
    ):
        keys_during_yield.append(byok.get_byok_key_for("openrouter"))

    assert seen["key_at_creation"] == "sk-byok-stream"  # key live at stream creation
    assert handler.is_byok is True
    assert keys_during_yield == [None, None]  # NOT bound while yielding → no leak
    assert byok.get_byok_key_for("openrouter") is None
