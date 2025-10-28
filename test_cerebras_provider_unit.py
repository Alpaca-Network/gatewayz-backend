import sys
import types

import pytest

try:  # pragma: no cover - exercised during local testing environments
    import supabase  # type: ignore
    # Some environments ship with a namespace package but no client helpers.
    getattr(supabase, "create_client")
    getattr(supabase, "Client")
except (ImportError, AttributeError):  # pragma: no cover - falls back only when SDK missing
    supabase_stub = types.ModuleType("supabase")

    class _StubSupabaseClient:
        def table(self, *args, **kwargs):
            raise RuntimeError("Supabase client is not available in unit tests")

    def _stub_create_client(*args, **kwargs):
        return _StubSupabaseClient()

    supabase_stub.Client = _StubSupabaseClient
    supabase_stub.create_client = _stub_create_client
    sys.modules["supabase"] = supabase_stub

from src.cache import _cerebras_models_cache
from src.config import Config
from src.services.portkey_providers import fetch_models_from_cerebras


class _DummyModel:
    def __init__(self, model_id, description=None, context_length=None, owned_by="cerebras"):
        self._model_id = model_id
        self._description = description
        self._context_length = context_length
        self._owned_by = owned_by

    def model_dump(self):
        return {
            "id": self._model_id,
            "description": self._description,
            "context_length": self._context_length,
            "owned_by": self._owned_by,
        }


class _DummyResponse:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return {"object": "list", "data": self._data}

    @property
    def data(self):
        return self._data


class _DummyCerebrasClient:
    def __init__(self, api_key):
        self._api_key = api_key
        self.models = types.SimpleNamespace(list=self._list)

    def _list(self):
        data = [
            _DummyModel("llama3.1-8b", description="Llama 3.1 8B"),
            _DummyModel("gpt-oss-120b", description="GPT OSS 120B"),
        ]
        return _DummyResponse(data)


@pytest.fixture(autouse=True)
def _clear_cerebras_cache(monkeypatch):
    _cerebras_models_cache["data"] = None
    _cerebras_models_cache["timestamp"] = None
    monkeypatch.setattr(Config, "CEREBRAS_API_KEY", "test-key", raising=False)
    yield
    _cerebras_models_cache["data"] = None
    _cerebras_models_cache["timestamp"] = None


def test_fetch_models_from_cerebras_handles_pydantic_like_response(monkeypatch):
    sdk_module = types.ModuleType("cerebras.cloud.sdk")
    sdk_module.Cerebras = _DummyCerebrasClient
    cloud_module = types.ModuleType("cerebras.cloud")
    cloud_module.sdk = sdk_module
    root_module = types.ModuleType("cerebras")
    root_module.cloud = cloud_module

    monkeypatch.setitem(sys.modules, "cerebras", root_module)
    monkeypatch.setitem(sys.modules, "cerebras.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "cerebras.cloud.sdk", sdk_module)

    models = fetch_models_from_cerebras()

    assert isinstance(models, list)
    assert len(models) == 2
    assert all(isinstance(model, dict) for model in models)
    assert all(model["source_gateway"] == "cerebras" for model in models)
    assert all("(" not in model["id"] for model in models)
    assert all("(" not in model["name"] for model in models)
