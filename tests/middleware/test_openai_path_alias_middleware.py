"""Root /chat/completions, /completions and /embeddings reach the /v1 handlers."""

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from src.middleware.openai_path_alias_middleware import (
    ALIASED_PATHS,
    OpenAIPathAliasMiddleware,
)


class _SeenPath:
    """Records the path an outer middleware observes, after the rewrite."""

    def __init__(self, app, seen):
        self.app = app
        self.seen = seen

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.seen.append(scope["path"])
        await self.app(scope, receive, send)


def _make_app(seen):
    app = FastAPI()
    v1 = APIRouter(prefix="/v1")

    @v1.post("/chat/completions")
    async def chat(request: Request):
        return {"route": "chat", "path": request.url.path}

    @v1.post("/completions")
    async def completions(request: Request):
        return {"route": "completions", "path": request.url.path}

    @v1.post("/embeddings")
    async def embeddings(request: Request):
        return {"route": "embeddings", "path": request.url.path}

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.include_router(v1)
    # Same order as main.py: the alias is added last, so it is outermost and
    # every other middleware (here: _SeenPath) sees the canonical /v1 path.
    app.add_middleware(_SeenPath, seen=seen)
    app.add_middleware(OpenAIPathAliasMiddleware)
    return app


def test_root_aliases_reach_v1_handlers():
    seen: list[str] = []
    client = TestClient(_make_app(seen))
    for path, route in [
        ("/chat/completions", "chat"),
        ("/completions", "completions"),
        ("/embeddings", "embeddings"),
    ]:
        response = client.post(path, json={})
        assert response.status_code == 200, path
        assert response.json() == {"route": route, "path": "/v1" + path}
    assert seen == ["/v1/chat/completions", "/v1/completions", "/v1/embeddings"]


def test_canonical_v1_paths_unchanged():
    seen: list[str] = []
    client = TestClient(_make_app(seen))
    response = client.post("/v1/chat/completions", json={})
    assert response.json()["path"] == "/v1/chat/completions"
    assert seen == ["/v1/chat/completions"]


def test_other_paths_not_rewritten():
    seen: list[str] = []
    client = TestClient(_make_app(seen))
    assert client.get("/health").status_code == 200
    # Only exact matches are aliased; a nested path is left alone (404 here).
    assert client.post("/chat/completions/extra", json={}).status_code == 404
    assert seen == ["/health", "/chat/completions/extra"]


def test_models_is_not_aliased():
    # /models is mounted at the root directly in main.py with the /v1 handler.
    assert "/models" not in ALIASED_PATHS
