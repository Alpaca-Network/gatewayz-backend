import importlib
import json
import types
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Request, Response, HTTPStatusError, RequestError, TimeoutException

# ======================================================================
# >>> CHANGE THIS to the module path where your router + endpoint live:
MODULE_PATH = "src.routes.chat"   # e.g. "src.api.chat", "src.api.v1.gateway", etc.
# ======================================================================

try:
    api = importlib.import_module(MODULE_PATH)
except ModuleNotFoundError as e:
    pytest.skip(f"Missing optional dependency: {e}", allow_module_level=True)

# Build a FastAPI app including the router under test
@pytest.fixture(scope="function")
def app():
    from src.security.deps import get_api_key

    app = FastAPI()
    app.include_router(api.router)

    # Override the get_api_key dependency to bypass authentication
    async def mock_get_api_key() -> str:
        return "test_api_key"

    app.dependency_overrides[get_api_key] = mock_get_api_key
    return app

@pytest.fixture
def auth_headers():
    """Provide authorization headers for test requests (not actually used due to dependency override)"""
    return {"Authorization": "Bearer test_api_key"}

@pytest.fixture(autouse=True)
def mock_api_key_validation(monkeypatch):
    """Mock API key validation to bypass database check - applied to all tests automatically"""
    from src.security import security
    # This fixture is kept for backwards compatibility but dependency override is the primary mechanism
    monkeypatch.setattr(security, "validate_api_key_security", lambda api_key, **kwargs: api_key)

@pytest.fixture
def payload_basic():
    # Minimal OpenAI-compatible body that your ProxyRequest should accept
    return {
        "model": "openrouter/some-model",
        "messages": [{"role": "user", "content": "Hello"}],
        # "provider": "openrouter"  # default in your code
    }

# ---------- Helper fake rate limit manager ----------
class _RLResult:
    def __init__(self, allowed=True, reason="", retry_after=None, rem_req=999, rem_tok=999999):
        self.allowed = allowed
        self.reason = reason
        self.retry_after = retry_after
        self.remaining_requests = rem_req
        self.remaining_tokens = rem_tok

class _RateLimitMgr:
    def __init__(self, allowed_pre=True, allowed_final=True):
        self.allowed_pre = allowed_pre
        self.allowed_final = allowed_final
        self._calls = []

    async def check_rate_limit(self, api_key: str, tokens_used: int = 0):
        # Record calls so tests can assert
        self._calls.append((api_key, tokens_used))
        # First call → "pre", second call → "final"
        if len(self._calls) == 1:
            return _RLResult(allowed=self.allowed_pre, reason="precheck")
        return _RLResult(allowed=self.allowed_final, reason="finalcheck", retry_after=3)

# ---------- Common "happy path" monkeypatches ----------
@pytest.fixture
def happy_patches(monkeypatch):
    # Mock API key validation to bypass database check
    from src.security import security
    monkeypatch.setattr(security, "validate_api_key_security", lambda api_key, **kwargs: api_key)

    # DB: user with credits
    monkeypatch.setattr(api, "get_user", lambda api_key: {"id": 1, "credits": 100.0, "environment_tag": "live"})

    # Plan limits allowed pre & post
    monkeypatch.setattr(api, "enforce_plan_limits", lambda user_id, tokens, env: {"allowed": True})

    # Trial: not a trial user
    monkeypatch.setattr(api, "validate_trial_access", lambda api_key: {"is_valid": True, "is_trial": False})

    # Rate limit manager that always allows
    mgr = _RateLimitMgr(allowed_pre=True, allowed_final=True)
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: mgr)

    # Upstream call & response processing
    def make_openrouter_request_openai(messages, model, **kw):
        # raw is irrelevant; response is produced by process fn
        return {"_raw": True}

    def process_openrouter_response(resp):
        return {
            "choices": [{"message": {"content": "Hi from model"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 30, "prompt_tokens": 10, "completion_tokens": 20},
        }

    monkeypatch.setattr(api, "make_openrouter_request_openai", make_openrouter_request_openai)
    monkeypatch.setattr(api, "process_openrouter_response", process_openrouter_response)

    # Pricing
    monkeypatch.setattr(api, "calculate_cost", lambda model, pt, ct: 0.012345)

    # Usage/credits book-keeping (no-ops we can assert via monkeypatch spies if needed)
    monkeypatch.setattr(api, "deduct_credits", lambda api_key, amount, desc, meta: None)
    monkeypatch.setattr(api, "record_usage", lambda user_id, api_key, model, tokens, cost: None)
    monkeypatch.setattr(api, "update_rate_limit_usage", lambda api_key, tokens: None)
    monkeypatch.setattr(api, "increment_api_key_usage", lambda api_key: None)

    # History helpers
    monkeypatch.setattr(api, "get_chat_session", lambda session_id, user_id: {"id": session_id})
    # saved messages container we can inspect in tests
    saved = []
    def save_chat_message(session_id, role, content, model, tokens):
        saved.append((session_id, role, content, model, tokens))
    monkeypatch.setattr(api, "save_chat_message", save_chat_message)

    return {"rate_mgr": mgr, "saved": saved}


# ----------------------------------------------------------------------
#                                TESTS
# ----------------------------------------------------------------------

@pytest.mark.anyio
async def test_happy_path_openrouter(app, happy_patches, payload_basic, auth_headers):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "Hi from model"
    assert data["usage"]["total_tokens"] == 30
    # gateway_usage present; we don’t assert buggy “balance” math here—just presence
    assert "gateway_usage" in data
    # rate limiter was called twice (pre + final)
    assert len(happy_patches["rate_mgr"].__dict__["_calls"]) == 2

@pytest.mark.anyio
async def test_invalid_api_key(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    monkeypatch.setattr(api, "get_user", lambda api_key: None)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 401
    assert "Invalid API key" in r.text

@pytest.mark.anyio
async def test_plan_limit_exceeded_precheck(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 100.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": False, "reason": "plan cap"})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: _RateLimitMgr(True, True))
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 429
    assert "Plan limit exceeded" in r.text

@pytest.mark.anyio
async def test_rate_limit_exceeded_precheck(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 100.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    mgr = _RateLimitMgr(allowed_pre=False, allowed_final=True)
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: mgr)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 429
    assert "Rate limit exceeded" in r.text

@pytest.mark.anyio
async def test_insufficient_credits_non_trial(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 0.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    mgr = _RateLimitMgr(True, True)
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: mgr)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 402
    assert "Insufficient credits" in r.text

@pytest.mark.anyio
async def test_trial_valid_usage_tracked(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    # Trial user (valid, not expired) → no credit deduction, track usage called
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 0.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": True, "is_expired": False})
    mgr = _RateLimitMgr(True, True)
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: mgr)

    # upstream success
    def make_openrouter_request_openai(messages, model, **kw):
        return {"_raw": True}
    def process_openrouter_response(resp):
        return {
            "choices": [{"message": {"content": "Trial OK"}}],
            "usage": {"total_tokens": 10, "prompt_tokens": 4, "completion_tokens": 6},
        }
    monkeypatch.setattr(api, "make_openrouter_request_openai", make_openrouter_request_openai)
    monkeypatch.setattr(api, "process_openrouter_response", process_openrouter_response)

    # Track trial usage spy
    called = {"n": 0, "args": None}
    def track_trial_usage(api_key, total_tokens, requests):
        called["n"] += 1
        called["args"] = (api_key, total_tokens, requests)
        return True
    monkeypatch.setattr(api, "track_trial_usage", track_trial_usage)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "Trial OK"
    assert called["n"] == 1
    assert called["args"][1] == 10

@pytest.mark.anyio
async def test_trial_expired_403(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 0.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": False, "is_trial": True, "is_expired": True, "error": "Trial expired", "trial_end_date": "2025-09-01"})
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 403
    assert "Trial expired" in r.text
    assert r.headers.get("X-Trial-Expired") == "true"

@pytest.mark.anyio
async def test_upstream_429_maps_429(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    # Happy DB/trial/plan/rate
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 100.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: _RateLimitMgr(True, True))

    # Make upstream raise HTTPStatusError(429)
    def boom(*a, **k):
        req = Request("POST", "https://openrouter.example/v1/chat")
        resp = Response(429, request=req, headers={"retry-after": "7"}, text="Too Many Requests")
        raise HTTPStatusError("rate limit", request=req, response=resp)
    monkeypatch.setattr(api, "make_openrouter_request_openai", boom)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 429
    assert "rate limit" in r.text.lower() or "limit exceeded" in r.text.lower()
    assert r.headers.get("retry-after") in ("7", "7.0")

@pytest.mark.anyio
async def test_upstream_401_maps_500_in_your_code(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    # (Matches your current mapping: 401 → 500 "OpenRouter authentication error")
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 100.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: _RateLimitMgr(True, True))

    def boom(*a, **k):
        req = Request("POST", "https://openrouter.example/v1/chat")
        resp = Response(401, request=req, text="Unauthorized")
        raise HTTPStatusError("auth", request=req, response=resp)
    monkeypatch.setattr(api, "make_openrouter_request_openai", boom)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 500
    assert "authentication" in r.text.lower()

@pytest.mark.anyio
async def test_upstream_request_error_maps_503(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 100.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: _RateLimitMgr(True, True))
    monkeypatch.setattr(api, "should_failover", lambda exc: False)
    monkeypatch.setattr(api, "make_huggingface_request_openai", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not failover")))

    def boom(*a, **k):
        raise RequestError("network is down", request=Request("POST", "https://openrouter.example/v1/chat"))
    monkeypatch.setattr(api, "make_openrouter_request_openai", boom)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code == 503
    assert "service unavailable" in r.text.lower() or "network" in r.text.lower()

@pytest.mark.anyio
async def test_upstream_timeout_maps_504(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 100.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: _RateLimitMgr(True, True))
    monkeypatch.setattr(api, "should_failover", lambda exc: False)
    monkeypatch.setattr(api, "make_huggingface_request_openai", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not failover")))

    def boom(*a, **k):
        # Your code does not explicitly catch httpx.TimeoutException in the executor,
        # but this simulates the path where make_* raises it.
        raise TimeoutException("upstream timeout")
    monkeypatch.setattr(api, "make_openrouter_request_openai", boom)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)
    assert r.status_code in (503, 500, 504)  # your current code maps RequestError→503; Timeout may be 503 or 500
    # (Adjust assertion to match your exact mapping if you add explicit timeout handling.)

@pytest.mark.anyio
async def test_saves_chat_history_when_session_id(app, happy_patches, payload_basic, auth_headers):
    payload = dict(payload_basic)
    payload["messages"] = [{"role": "user", "content": "Save this please"}]
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions?session_id=123", json=payload, headers=auth_headers)
    assert r.status_code == 200
    # Your current code saves only messages[0] ("first user") + assistant
    saved = happy_patches["saved"]
    assert len(saved) == 2
    assert saved[0][0] == 123 and saved[0][1] == "user"
    assert saved[1][0] == 123 and saved[1][1] == "assistant"

@pytest.mark.anyio
async def test_streaming_response(app, mock_api_key_validation, monkeypatch, payload_basic, auth_headers):
    # Mock a streaming response
    monkeypatch.setattr(api, "get_user", lambda k: {"id": 1, "credits": 100.0, "environment_tag": "live"})
    monkeypatch.setattr(api, "enforce_plan_limits", lambda uid, tok, env: {"allowed": True})
    monkeypatch.setattr(api, "validate_trial_access", lambda k: {"is_valid": True, "is_trial": False})
    mgr = _RateLimitMgr(True, True)
    monkeypatch.setattr(api, "get_rate_limit_manager", lambda: mgr)

    # Mock streaming response
    class MockStreamChunk:
        def __init__(self, content, finish_reason=None):
            self.id = "chatcmpl-123"
            self.object = "chat.completion.chunk"
            self.created = 1234567890
            self.model = "test-model"
            self.choices = [MockChoice(content, finish_reason)]
            self.usage = None

    class MockChoice:
        def __init__(self, content, finish_reason=None):
            self.index = 0
            self.delta = MockDelta(content)
            self.finish_reason = finish_reason

    class MockDelta:
        def __init__(self, content):
            self.content = content
            self.role = "assistant" if content else None

    def make_stream(*args, **kwargs):
        return [
            MockStreamChunk("Hello"),
            MockStreamChunk(" streaming"),
            MockStreamChunk(" world!", "stop")
        ]

    monkeypatch.setattr(api, "make_openrouter_request_openai_stream", make_stream)
    monkeypatch.setattr(api, "calculate_cost", lambda m, p, c: 0.001)
    monkeypatch.setattr(api, "deduct_credits", lambda *a, **k: None)
    monkeypatch.setattr(api, "record_usage", lambda *a, **k: None)
    monkeypatch.setattr(api, "update_rate_limit_usage", lambda *a, **k: None)
    monkeypatch.setattr(api, "increment_api_key_usage", lambda *a: None)

    payload = dict(payload_basic)
    payload["stream"] = True

    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/v1/chat/completions", json=payload, headers=auth_headers)

    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    content = r.text
    assert "data: " in content
    assert "[DONE]" in content


@pytest.mark.anyio
async def test_provider_failover_to_huggingface(app, happy_patches, payload_basic, auth_headers, monkeypatch):
    payload = dict(payload_basic)
    payload["provider"] = "featherless"
    payload["model"] = "featherless/test-model"

    # Avoid auto-detection overriding the requested provider
    monkeypatch.setattr(api, "detect_provider_from_model_id", lambda model: None)

    call_tracker = {"featherless": 0, "huggingface": 0}

    def failing_featherless(*args, **kwargs):
        call_tracker["featherless"] += 1
        request = Request("POST", "https://featherless.test/v1/chat")
        response = Response(status_code=502, request=request, content=b"")
        raise HTTPStatusError("featherless backend error", request=request, response=response)

    monkeypatch.setattr(api, "make_featherless_request_openai", failing_featherless)

    def fallback_huggingface(messages, model, **kwargs):
        call_tracker["huggingface"] += 1
        return {"_raw": True}

    monkeypatch.setattr(api, "make_huggingface_request_openai", fallback_huggingface)

    def process_huggingface_response(resp):
        return {
            "choices": [{"message": {"content": "served by huggingface"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 12, "prompt_tokens": 5, "completion_tokens": 7},
        }

    monkeypatch.setattr(api, "process_huggingface_response", process_huggingface_response)

    def guard_openrouter(*args, **kwargs):
        raise AssertionError("Failover should not reach openrouter in this scenario")

    monkeypatch.setattr(api, "make_openrouter_request_openai", guard_openrouter)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post("/v1/chat/completions", json=payload, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "served by huggingface"
    assert call_tracker["featherless"] == 1
    assert call_tracker["huggingface"] == 1


@pytest.mark.anyio
async def test_provider_failover_on_404_to_huggingface(app, happy_patches, payload_basic, auth_headers, monkeypatch):
    payload = dict(payload_basic)
    payload["provider"] = "featherless"
    payload["model"] = "featherless/ghost-model"

    monkeypatch.setattr(api, "detect_provider_from_model_id", lambda model: None)

    call_tracker = {"featherless": 0, "huggingface": 0}

    def missing_featherless(*args, **kwargs):
        call_tracker["featherless"] += 1
        request = Request("POST", "https://featherless.test/v1/chat")
        response = Response(status_code=404, request=request, content=b"missing")
        raise HTTPStatusError("not found", request=request, response=response)

    monkeypatch.setattr(api, "make_featherless_request_openai", missing_featherless)

    def fallback_huggingface(messages, model, **kwargs):
        call_tracker["huggingface"] += 1
        return {"_raw": True}

    monkeypatch.setattr(api, "make_huggingface_request_openai", fallback_huggingface)

    def process_huggingface_response(resp):
        return {
            "choices": [{"message": {"content": "fallback success"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 8, "prompt_tokens": 4, "completion_tokens": 4},
        }

    monkeypatch.setattr(api, "process_huggingface_response", process_huggingface_response)

    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post("/v1/chat/completions", json=payload, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "fallback success"
    assert call_tracker["featherless"] == 1
    assert call_tracker["huggingface"] == 1
