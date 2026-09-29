"""Billing regressions: embeddings and paid /tools must be authenticated + metered,
and the anonymous chat limit must key on a spoof-safe IP and reserve pre-inference."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.db import users as users_module
from src.security.deps import get_api_key, get_optional_api_key
from src.services.tools.base import ToolResult

PAID = {"id": 7, "subscription_allowance": 0.0, "purchased_credits": 5.0}
BROKE = {"id": 8, "subscription_allowance": 0.0, "purchased_credits": 0.0}


def _client(router, **overrides):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_api_key] = lambda: "gw_test"
    for dep, fn in overrides.items():
        app.dependency_overrides[dep] = fn
    return TestClient(app)


# ---------------------------------------------------------------- embeddings
class TestEmbeddingsMetering:
    @pytest.fixture
    def env(self, monkeypatch):
        from src.routes import embeddings

        monkeypatch.setattr("src.config.Config.OPENAI_API_KEY", "sk", raising=False)
        upstream = MagicMock()
        upstream.raise_for_status = MagicMock()
        upstream.json.return_value = {
            "data": [{"embedding": [0.1], "index": 0}],
            "usage": {"prompt_tokens": 1000, "total_tokens": 1000},
        }
        http = MagicMock()
        http.post.return_value = upstream
        with patch.object(embeddings, "get_http_client", return_value=http):
            yield embeddings, http

    def _post(self, embeddings, **kw):
        return _client(embeddings.router).post(
            "/embeddings", json={"model": "text-embedding-3-small", "input": "hello", **kw}
        )

    def test_zero_balance_gets_402_and_no_upstream_call(self, env):
        embeddings, http = env
        with (
            patch.object(users_module, "get_user", return_value=BROKE),
            patch.object(users_module, "deduct_credits") as ded,
        ):
            r = self._post(embeddings)
        assert r.status_code == 402
        http.post.assert_not_called()
        ded.assert_not_called()

    def test_success_deducts_by_input_tokens_with_idempotent_ref(self, env):
        embeddings, http = env
        with (
            patch.object(users_module, "get_user", return_value=PAID),
            patch.object(users_module, "deduct_credits") as ded,
            patch.object(users_module, "record_usage") as rec,
        ):
            r = self._post(embeddings)
        assert r.status_code == 200
        ded.assert_called_once()
        args = ded.call_args.args
        assert args[0] == "gw_test"
        # 1000 tokens * $0.02/M
        assert args[1] == pytest.approx(0.00002)
        assert args[4]  # billing_ref idempotency key passed as request_id
        rec.assert_called_once()

    def test_unknown_model_price_falls_back_to_nonzero(self):
        from src.routes.embeddings import embedding_cost

        assert embedding_cost("together/some-new-embedder", 1_000_000) > 0

    def test_upstream_failure_is_not_charged(self, env):
        embeddings, http = env
        http.post.side_effect = RuntimeError("boom")
        with (
            patch.object(users_module, "get_user", return_value=PAID),
            patch.object(users_module, "deduct_credits") as ded,
        ):
            r = self._post(embeddings)
        assert r.status_code == 502
        ded.assert_not_called()


# --------------------------------------------------------------------- tools
class TestToolsMetering:
    def test_search_augment_requires_auth(self):
        from src.routes import tools

        app = FastAPI()
        app.include_router(tools.router)
        with patch.object(tools, "execute_tool", new_callable=AsyncMock) as ex:
            r = TestClient(app).post("/tools/search/augment", json={"query": "q"})
        assert r.status_code in (401, 403)
        ex.assert_not_called()

    def test_search_augment_zero_balance_402_no_search(self):
        from src.routes import tools

        with (
            patch.object(users_module, "get_user", return_value=BROKE),
            patch.object(tools, "execute_tool", new_callable=AsyncMock) as ex,
        ):
            r = _client(tools.router).post("/tools/search/augment", json={"query": "q"})
        assert r.status_code == 402
        ex.assert_not_called()

    def test_search_augment_charges_on_success(self):
        from src.routes import tools

        res = ToolResult(success=True, result={"results": [{"title": "t"}]}, metadata={})
        with (
            patch.object(users_module, "get_user", return_value=PAID),
            patch.object(users_module, "deduct_credits") as ded,
            patch.object(users_module, "record_usage"),
            patch.object(tools, "execute_tool", new_callable=AsyncMock, return_value=res),
        ):
            r = _client(tools.router).post("/tools/search/augment", json={"query": "q"})
        assert r.status_code == 200
        ded.assert_called_once()
        assert ded.call_args.args[1] > 0

    @pytest.mark.parametrize("name", ["web_search", "text_to_speech"])
    def test_execute_charges_paid_tools(self, name):
        from src.routes import tools

        res = ToolResult(success=True, result={}, metadata={})
        with (
            patch.object(users_module, "get_user", return_value=PAID),
            patch.object(users_module, "deduct_credits") as ded,
            patch.object(users_module, "record_usage"),
            patch.object(tools, "execute_tool", new_callable=AsyncMock, return_value=res),
        ):
            r = _client(tools.router).post("/tools/execute", json={"name": name, "parameters": {}})
        assert r.status_code == 200
        ded.assert_called_once()

    def test_execute_zero_balance_402(self):
        from src.routes import tools

        with (
            patch.object(users_module, "get_user", return_value=BROKE),
            patch.object(tools, "execute_tool", new_callable=AsyncMock) as ex,
        ):
            r = _client(tools.router).post(
                "/tools/execute", json={"name": "web_search", "parameters": {}}
            )
        assert r.status_code == 402
        ex.assert_not_called()

    def test_failed_tool_is_not_charged(self):
        from src.routes import tools

        res = ToolResult(success=False, error="x", metadata={})
        with (
            patch.object(users_module, "get_user", return_value=PAID),
            patch.object(users_module, "deduct_credits") as ded,
            patch.object(tools, "execute_tool", new_callable=AsyncMock, return_value=res),
        ):
            _client(tools.router).post(
                "/tools/execute", json={"name": "web_search", "parameters": {}}
            )
        ded.assert_not_called()


# ---------------------------------------------------------- anonymous limiter
class TestAnonymousReservation:
    def test_reserve_increments_atomically_and_denies_over_limit(self, monkeypatch):
        from src.services import anonymous_rate_limiter as arl

        monkeypatch.setattr(arl, "_get_redis_client", lambda: None)
        arl._anonymous_usage_cache.clear()
        limit = arl.ANONYMOUS_DAILY_LIMIT
        results = [arl.reserve_anonymous_request("9.9.9.9") for _ in range(limit + 1)]
        assert all(r["allowed"] for r in results[:limit])
        assert results[-1]["allowed"] is False
        assert results[-1]["remaining"] == 0

    def test_chat_ip_ignores_client_controlled_leftmost_xff(self):
        from src.routes.chat import resolve_anonymous_client_ip

        req = MagicMock()
        req.headers = {"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 3.3.3.3"}
        req.client.host = "10.0.0.1"
        assert resolve_anonymous_client_ip(req) == "3.3.3.3"

    def test_post_processing_no_longer_double_counts(self):
        import inspect

        from src.handlers import post_processing

        assert "record_anonymous_request" not in inspect.getsource(post_processing)
