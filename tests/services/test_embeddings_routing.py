"""Tests for embeddings provider routing.

The important behaviour is the refusal: an unroutable model must produce an
actionable 400 rather than a guessed provider and a confusing upstream 404.
"""

import pytest
from fastapi import HTTPException

from src.routes.embeddings import (
    EMBEDDING_PROVIDERS,
    MODEL_PREFIX_ROUTING,
    resolve_provider,
    strip_provider_prefix,
)


class TestResolveProvider:
    def test_openai_native_prefix(self, monkeypatch):
        monkeypatch.setattr("src.config.Config.OPENAI_API_KEY", "sk-test", raising=False)
        provider, base_url, key = resolve_provider("text-embedding-3-small")
        assert provider == "openai"
        assert "openai.com" in base_url
        assert key == "sk-test"

    def test_explicit_namespace(self, monkeypatch):
        monkeypatch.setattr("src.config.Config.TOGETHER_API_KEY", "tk", raising=False)
        provider, _, _ = resolve_provider("together/m2-bert-80M-8k-retrieval")
        assert provider == "together"

    def test_huggingface_style_routes_to_deepinfra(self, monkeypatch):
        monkeypatch.setattr("src.config.Config.DEEPINFRA_API_KEY", "dk", raising=False)
        provider, _, _ = resolve_provider("BAAI/bge-large-en-v1.5")
        assert provider == "deepinfra"

    def test_unroutable_model_is_a_400_not_a_guess(self):
        with pytest.raises(HTTPException) as exc:
            resolve_provider("mystery-embedder-v9")
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "unroutable_embedding_model"

    def test_unroutable_error_lists_supported_prefixes(self):
        """The error has to tell the caller how to fix it."""
        with pytest.raises(HTTPException) as exc:
            resolve_provider("mystery")
        assert exc.value.detail["supported_prefixes"]

    def test_unconfigured_provider_is_503(self, monkeypatch):
        monkeypatch.setattr("src.config.Config.OPENAI_API_KEY", None, raising=False)
        with pytest.raises(HTTPException) as exc:
            resolve_provider("text-embedding-3-small")
        assert exc.value.status_code == 503
        assert exc.value.detail["error"] == "provider_not_configured"

    def test_routing_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr("src.config.Config.OPENAI_API_KEY", "sk", raising=False)
        provider, _, _ = resolve_provider("TEXT-EMBEDDING-3-LARGE")
        assert provider == "openai"

    def test_empty_model_is_rejected(self):
        with pytest.raises(HTTPException):
            resolve_provider("")


class TestStripProviderPrefix:
    def test_removes_matching_namespace(self):
        assert strip_provider_prefix("openai/text-embedding-3-small", "openai") == (
            "text-embedding-3-small"
        )

    def test_leaves_unnamespaced_model_alone(self):
        assert strip_provider_prefix("text-embedding-3-small", "openai") == (
            "text-embedding-3-small"
        )

    def test_does_not_strip_a_different_providers_prefix(self):
        assert strip_provider_prefix("BAAI/bge-large", "deepinfra") == "BAAI/bge-large"


class TestRoutingTable:
    def test_every_prefix_maps_to_a_known_provider(self):
        for _, provider in MODEL_PREFIX_ROUTING:
            assert provider in EMBEDDING_PROVIDERS

    def test_every_provider_declares_a_base_url_and_key(self):
        for provider, (base_url, key_attr) in EMBEDDING_PROVIDERS.items():
            assert base_url.startswith("https://"), provider
            assert key_attr.endswith("_API_KEY"), provider
