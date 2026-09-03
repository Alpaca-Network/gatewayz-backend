"""Tests for src.services.gpu.catalog (gatewayz-backend#2262, spec §4 item 3)."""

from __future__ import annotations

import sys
import types

from src.services.gpu.catalog import community_catalog_models, sync_community_catalog

NODE_A = {
    "id": "node-a",
    "status": "active",
    "models": [
        {"id": "llama-3.1-8b-instruct", "max_context": 8192, "dtype": "bf16"},
        {"id": "qwen2.5-7b-instruct", "max_context": 32768, "dtype": "bf16"},
    ],
}
NODE_B = {
    "id": "node-b",
    "status": "active",
    "models": [
        {"id": "llama-3.1-8b-instruct", "max_context": 4096, "dtype": "bf16"},
    ],
}
NODE_OFFLINE = {
    "id": "node-c",
    "status": "offline",
    "models": [{"id": "should-not-appear", "max_context": 1024}],
}


def test_unions_models_across_active_nodes():
    result = community_catalog_models([NODE_A, NODE_B])
    ids = {m["id"] for m in result}
    assert ids == {"community/llama-3.1-8b-instruct", "community/qwen2.5-7b-instruct"}


def test_ids_are_community_prefixed_and_tagged():
    result = community_catalog_models([NODE_A])
    for model in result:
        assert model["id"].startswith("community/")
        assert model["source_gateway"] == "community"
        assert model["provider_slug"] == "community"


def test_context_length_from_max_context():
    result = community_catalog_models([NODE_A])
    by_id = {m["id"]: m for m in result}
    assert by_id["community/llama-3.1-8b-instruct"]["context_length"] == 8192


def test_duplicate_model_across_nodes_appears_once_first_node_wins():
    result = community_catalog_models([NODE_A, NODE_B])
    by_id = {m["id"]: m for m in result}
    # NODE_A declared it first with max_context=8192; NODE_B's 4096 loses.
    assert by_id["community/llama-3.1-8b-instruct"]["context_length"] == 8192


def test_non_active_node_excluded():
    result = community_catalog_models([NODE_OFFLINE])
    assert result == []


def test_empty_node_list():
    assert community_catalog_models([]) == []


def test_sync_community_catalog_returns_empty_when_gpu_module_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "src.db.gpu", raising=False)
    assert sync_community_catalog() == []


def test_sync_community_catalog_uses_list_active_nodes(monkeypatch):
    module = types.ModuleType("src.db.gpu")
    module.list_active_nodes = lambda: [NODE_A]
    monkeypatch.setitem(sys.modules, "src.db.gpu", module)

    result = sync_community_catalog()
    assert result[0]["id"] == "community/llama-3.1-8b-instruct"


def test_sync_community_catalog_swallows_db_errors(monkeypatch):
    module = types.ModuleType("src.db.gpu")

    def _boom():
        raise RuntimeError("db down")

    module.list_active_nodes = _boom
    monkeypatch.setitem(sys.modules, "src.db.gpu", module)

    assert sync_community_catalog() == []
