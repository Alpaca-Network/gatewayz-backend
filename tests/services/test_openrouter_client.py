# tests/services/test_openrouter_client_unit.py
import importlib
import types

import pytest

MODULE_PATH = "src.services.openrouter_client"  # <- change if needed


@pytest.fixture
def mod():
    return importlib.import_module(MODULE_PATH)


class FakeCompletions:
    def __init__(self, parent):
        self.parent = parent
        self.calls = []
        self.return_value = object()  # sentinel

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Simulate OpenAI returning some object (the raw response)
        return self.return_value


class FakeChat:
    def __init__(self, parent):
        self.parent = parent
        self.completions = FakeCompletions(parent)


class FakeOpenAI:
    def __init__(self, base_url=None, api_key=None, default_headers=None):
        # record constructor args so tests can assert
        self.base_url = base_url
        self.api_key = api_key
        self.default_headers = default_headers or {}
        self.chat = FakeChat(self)


def test_get_openrouter_client_success(monkeypatch, mod):
    # Arrange config
    monkeypatch.setattr(mod.Config, "OPENROUTER_API_KEY", "sk-or-123")
    monkeypatch.setattr(mod.Config, "OPENROUTER_SITE_URL", "https://gatewayz.example")
    monkeypatch.setattr(mod.Config, "OPENROUTER_SITE_NAME", "Gatewayz")

    # Create a fake client with expected properties for OpenRouter
    fake_client = FakeOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-123",
        default_headers={"HTTP-Referer": "https://gatewayz.example", "X-Title": "Gatewayz"},
    )

    # Patch the connection pool function to return our fake client
    monkeypatch.setattr(mod, "get_openrouter_pooled_client", lambda: fake_client)

    # Act
    client = mod.get_openrouter_client()

    # Assert
    assert isinstance(client, FakeOpenAI)
    assert client is fake_client
    assert client.base_url == "https://openrouter.ai/api/v1"
    assert client.api_key == "sk-or-123"
    # headers
    hdrs = client.default_headers
    assert hdrs["HTTP-Referer"] == "https://gatewayz.example"
    assert hdrs["X-Title"] == "Gatewayz"


def test_get_openrouter_client_missing_key_raises(monkeypatch, mod):
    # Arrange: key not configured
    monkeypatch.setattr(mod.Config, "OPENROUTER_API_KEY", None)
    monkeypatch.setattr(mod.Config, "OPENROUTER_SITE_URL", "https://x")
    monkeypatch.setattr(mod.Config, "OPENROUTER_SITE_NAME", "X")

    pool_called = {"n": 0}

    def _pool_should_not_be_called():
        pool_called["n"] += 1
        return FakeOpenAI()

    # Patch the connection pool function since that's what the code uses
    monkeypatch.setattr(mod, "get_openrouter_pooled_client", _pool_should_not_be_called)

    # Act + Assert
    with pytest.raises(ValueError):
        mod.get_openrouter_client()
    # Pool function should never be called when API key is missing
    assert pool_called["n"] == 0


def test_make_openrouter_request_openai_forwards_args(monkeypatch, mod):
    # Arrange: stub client with completions
    fake = FakeOpenAI()
    monkeypatch.setattr(mod, "get_openrouter_client", lambda: fake)

    messages = [{"role": "user", "content": "Hello"}]
    model = "openrouter/some-model"
    kwargs = {"temperature": 0.2, "max_tokens": 128, "top_p": 0.9}

    # Act
    resp = mod.make_openrouter_request_openai(messages, model, **kwargs)

    # Assert: got the raw return value
    assert resp is fake.chat.completions.return_value
    # Exactly one call with the merged args
    assert len(fake.chat.completions.calls) == 1
    call = fake.chat.completions.calls[0]
    assert call["model"] == model
    assert call["messages"] == messages
    for k, v in kwargs.items():
        assert call[k] == v
    # Verify extra_body with provider settings is always included
    assert "extra_body" in call
    assert call["extra_body"]["provider"]["data_collection"] == "allow"


def test_process_openrouter_response_happy(monkeypatch, mod):
    # Build a dummy OpenAI-like response object
    class _Msg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class _Choice:
        def __init__(self, index, role, content, finish_reason="stop"):
            self.index = index
            self.message = _Msg(role, content)
            self.finish_reason = finish_reason

    class _Usage:
        def __init__(self, p, c, t):
            self.prompt_tokens = p
            self.completion_tokens = c
            self.total_tokens = t

    class _Resp:
        id = "cmpl-123"
        object = "chat.completion"
        created = 1720000000
        model = "openrouter/some-model"
        choices = [
            _Choice(0, "assistant", "Hello world", "stop"),
            _Choice(1, "assistant", "Another", "length"),
        ]
        usage = _Usage(10, 20, 30)

    out = mod.process_openrouter_response(_Resp)

    assert out["id"] == "cmpl-123"
    assert out["object"] == "chat.completion"
    assert out["created"] == 1720000000
    assert out["model"] == "openrouter/some-model"
    assert len(out["choices"]) == 2
    assert out["choices"][0]["index"] == 0
    assert out["choices"][0]["message"]["role"] == "assistant"
    assert out["choices"][0]["message"]["content"] == "Hello world"
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"]["prompt_tokens"] == 10
    assert out["usage"]["completion_tokens"] == 20
    assert out["usage"]["total_tokens"] == 30


def test_process_openrouter_response_no_usage(monkeypatch, mod):
    # Response with usage = None should produce {}
    class _Msg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class _Choice:
        def __init__(self, idx):
            self.index = idx
            self.message = _Msg("assistant", "ok")
            self.finish_reason = "stop"

    class _Resp:
        id = "cmpl-xyz"
        object = "chat.completion"
        created = 1720000101
        model = "openrouter/some-model"
        choices = [_Choice(0)]
        usage = None

    out = mod.process_openrouter_response(_Resp)
    assert out["usage"] == {}


# Tests for _merge_extra_body helper function
class TestMergeExtraBody:
    """Tests for the _merge_extra_body helper that ensures data_collection: allow is set."""

    def test_merge_empty_kwargs(self, mod):
        """Empty kwargs should get extra_body with provider settings."""
        result = mod._merge_extra_body({})
        assert result == {"extra_body": {"provider": {"data_collection": "allow"}}}

    def test_merge_preserves_other_kwargs(self, mod):
        """Other kwargs like temperature should be preserved."""
        result = mod._merge_extra_body({"temperature": 0.7, "max_tokens": 100})
        assert result["temperature"] == 0.7
        assert result["max_tokens"] == 100
        assert result["extra_body"]["provider"]["data_collection"] == "allow"

    def test_merge_preserves_existing_extra_body(self, mod):
        """Existing extra_body values should be preserved."""
        result = mod._merge_extra_body({"extra_body": {"other_field": "value"}})
        assert result["extra_body"]["other_field"] == "value"
        assert result["extra_body"]["provider"]["data_collection"] == "allow"

    def test_merge_preserves_existing_provider_settings(self, mod):
        """Existing provider settings should be preserved and merged."""
        result = mod._merge_extra_body(
            {"extra_body": {"provider": {"order": ["anthropic", "openai"]}}}
        )
        assert result["extra_body"]["provider"]["order"] == ["anthropic", "openai"]
        assert result["extra_body"]["provider"]["data_collection"] == "allow"

    def test_user_can_override_data_collection(self, mod):
        """User-provided data_collection should override the default."""
        result = mod._merge_extra_body({"extra_body": {"provider": {"data_collection": "deny"}}})
        # User's explicit setting takes precedence
        assert result["extra_body"]["provider"]["data_collection"] == "deny"


# Tests for _normalize_message_roles helper function
class TestNormalizeMessageRoles:
    """Tests for the _normalize_message_roles helper that transforms developer role to system."""

    def test_normalize_empty_messages(self, mod):
        """Empty messages list should return empty list."""
        result = mod._normalize_message_roles([])
        assert result == []

    def test_normalize_preserves_standard_roles(self, mod):
        """Standard roles (user, assistant, system, tool) should be preserved."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "system", "content": "You are helpful"},
            {"role": "tool", "content": "Tool result", "tool_call_id": "123"},
        ]
        result = mod._normalize_message_roles(messages)
        assert result == messages

    def test_normalize_transforms_developer_to_system(self, mod):
        """Developer role should be transformed to system role."""
        messages = [
            {"role": "developer", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        result = mod._normalize_message_roles(messages)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a helpful assistant."
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Hello"

    def test_normalize_preserves_other_message_fields(self, mod):
        """Other message fields should be preserved when transforming developer role."""
        messages = [
            {"role": "developer", "content": "Instructions", "name": "developer_name"},
        ]
        result = mod._normalize_message_roles(messages)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Instructions"
        assert result[0]["name"] == "developer_name"

    def test_normalize_multiple_developer_messages(self, mod):
        """Multiple developer messages should all be transformed."""
        messages = [
            {"role": "developer", "content": "First instruction"},
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
            {"role": "developer", "content": "Second instruction"},
        ]
        result = mod._normalize_message_roles(messages)
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "system"

    def test_normalize_handles_non_dict_messages(self, mod):
        """Non-dict messages should be passed through unchanged."""
        messages = [
            {"role": "user", "content": "Hello"},
            None,  # Edge case: None in messages list
            {"role": "developer", "content": "Instructions"},
        ]
        result = mod._normalize_message_roles(messages)
        assert result[0]["role"] == "user"
        assert result[1] is None
        assert result[2]["role"] == "system"


def test_make_openrouter_request_normalizes_developer_role(monkeypatch, mod):
    """Verify that make_openrouter_request_openai normalizes developer role to system."""
    fake = FakeOpenAI()
    monkeypatch.setattr(mod, "get_openrouter_client", lambda: fake)

    messages = [
        {"role": "developer", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    model = "openrouter/some-model"

    mod.make_openrouter_request_openai(messages, model)

    # Check that the messages passed to the API have normalized roles
    assert len(fake.chat.completions.calls) == 1
    call = fake.chat.completions.calls[0]
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "You are helpful."
    assert call["messages"][1]["role"] == "user"


def test_make_openrouter_request_stream_normalizes_developer_role(monkeypatch, mod):
    """Verify that make_openrouter_request_openai_stream normalizes developer role to system."""
    fake = FakeOpenAI()
    monkeypatch.setattr(mod, "get_openrouter_client", lambda: fake)

    messages = [
        {"role": "developer", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    model = "openrouter/some-model"

    mod.make_openrouter_request_openai_stream(messages, model)

    # Check that the messages passed to the API have normalized roles
    assert len(fake.chat.completions.calls) == 1
    call = fake.chat.completions.calls[0]
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "You are helpful."
    assert call["messages"][1]["role"] == "user"


# Async fake client for testing async streaming function
class FakeAsyncCompletions:
    def __init__(self, parent):
        self.parent = parent
        self.calls = []
        self.return_value = object()  # sentinel

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.return_value


class FakeAsyncChat:
    def __init__(self, parent):
        self.parent = parent
        self.completions = FakeAsyncCompletions(parent)


class FakeAsyncOpenAI:
    def __init__(self, base_url=None, api_key=None, default_headers=None):
        self.base_url = base_url
        self.api_key = api_key
        self.default_headers = default_headers or {}
        self.chat = FakeAsyncChat(self)


@pytest.mark.asyncio
async def test_make_openrouter_request_stream_async_normalizes_developer_role(monkeypatch, mod):
    """Verify that make_openrouter_request_openai_stream_async normalizes developer role to system."""
    fake = FakeAsyncOpenAI()
    monkeypatch.setattr(mod, "get_openrouter_async_client", lambda: fake)

    messages = [
        {"role": "developer", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    model = "openrouter/some-model"

    await mod.make_openrouter_request_openai_stream_async(messages, model)

    # Check that the messages passed to the API have normalized roles
    assert len(fake.chat.completions.calls) == 1
    call = fake.chat.completions.calls[0]
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "You are helpful."
    assert call["messages"][1]["role"] == "user"
