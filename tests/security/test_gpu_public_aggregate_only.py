"""Aggregate-only guarantee for the public GPU transparency feed
(gatewayz-backend#2263 #2264, spec §6 -- "responses contain no wallet
address, no user_id, no billing_ref, no endpoint URL").

Same shape as tests/security/test_upstream_identity_firewall.py's
leak-canary: seed rows carrying sentinel identity values that would only
appear if a route accidentally selected/forwarded a raw DB row instead of
going through src.db.gpu_rollups's allowlisted read helpers, call every
public endpoint, and recursively scan the JSON bodies for every sentinel
byte sequence. No real network or DB calls -- src.db.gpu_rollups's DB
client is mocked to return the seeded sentinel-carrying rows.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.routes.gpu_public as gpu_public
from src.main import app

client = TestClient(app)

# --- Sentinel identity values (spec §6) --------------------------------------

SENTINEL_WALLET = "0xCA11A2000000000000000000000000000000CA11"
SENTINEL_ENDPOINT = "https://canary-node.example.test"
SENTINEL_PROVIDER_USER_ID = 424242
SENTINEL_BILLING_REF = "canary-req-424242"
SENTINEL_EMAIL = "canary-424242@example.test"
SENTINEL_NODE_TOKEN_HASH = "canary-node-token-hash-deadbeef"

ALL_SENTINELS = [
    SENTINEL_WALLET,
    SENTINEL_WALLET.lower(),
    SENTINEL_ENDPOINT,
    str(SENTINEL_PROVIDER_USER_ID),
    SENTINEL_BILLING_REF,
    SENTINEL_EMAIL,
    SENTINEL_NODE_TOKEN_HASH,
]

# A gpu_nodes row shaped exactly as W-A1's migration will produce it (spec
# §2), carrying every field that must NEVER reach a public response.
_SENTINEL_NODE_ROW = {
    "id": "node-canary",
    "name": "canary-public-name",
    "region": "us-east",
    "gpu_model": "A100",
    "vram_gb": 80,
    "status": "active",
    "models": [{"id": "llama-3.1-8b-instruct", "max_context": 8192}],
    "endpoint_url": SENTINEL_ENDPOINT,
    "endpoint_api_key_encrypted": "should-never-appear-either",
    "node_token_hash": SENTINEL_NODE_TOKEN_HASH,
    "provider_id": "provider-canary",
    "gpu_providers": {
        "status": "approved",
        "user_id": SENTINEL_PROVIDER_USER_ID,
        "payout_wallet_address": SENTINEL_WALLET.lower(),
        "contact_email": SENTINEL_EMAIL,
    },
}

_SENTINEL_WORK_ROW = {
    "model": "llama-3.1-8b-instruct",
    "status": "completed",
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "latency_ms": 200,
    "node_id": "node-canary",
    "billing_ref": SENTINEL_BILLING_REF,
    "provider_id": "provider-canary",
}

_SENTINEL_UTIL_ROW = {
    "hour": "2026-09-03T17:00:00+00:00",
    "region": "us-east",
    "model": "llama-3.1-8b-instruct",
    "requests": 5,
    "prompt_tokens": 50,
    "completion_tokens": 25,
    "avg_latency_ms": 200,
    "error_rate": 0.0,
    "active_nodes": 1,
}


def _mock_table_client(table_data: dict):
    """gpu_rollups.py's DB reads, seeded with rows carrying every
    forbidden field. Mirrors the chain-mock helper in
    tests/db/test_gpu_rollups.py."""
    queries: dict = {}

    def make_query(name):
        if name not in queries:
            query = MagicMock()
            for method in ("select", "eq", "gte", "lt", "lte", "in_", "order", "limit"):
                getattr(query, method).return_value = query
            query.execute.return_value = MagicMock(
                data=table_data.get(name, []), count=len(table_data.get(name, []))
            )
            queries[name] = query
        return queries[name]

    client = MagicMock()
    client.table.side_effect = make_query
    return client


def _recursive_haystack(value) -> str:
    """Flatten a JSON-decoded response body into one lowercase string for
    substring scanning -- keys included, so a sentinel leaking as a dict
    *key* (not just a value) is still caught."""
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            parts.append(str(k))
            parts.append(_recursive_haystack(v))
        return " ".join(parts).lower()
    if isinstance(value, list):
        return " ".join(_recursive_haystack(v) for v in value).lower()
    return str(value).lower()


@pytest.fixture(autouse=True)
def _bypass_rate_limit_and_cache(monkeypatch):
    monkeypatch.setattr(
        gpu_public, "sliding_window_check", lambda key, limit, window: (True, limit, None)
    )
    gpu_public._local_cache.clear()
    yield
    gpu_public._local_cache.clear()


@pytest.fixture
def sentinel_db_client():
    return _mock_table_client(
        {
            "gpu_nodes": [_SENTINEL_NODE_ROW],
            "provider_work": [_SENTINEL_WORK_ROW],
            "gpu_utilization_hourly": [_SENTINEL_UTIL_ROW],
            "gpu_providers": [{"id": "provider-canary", "status": "approved"}],
        }
    )


@pytest.mark.parametrize(
    "path,params",
    [
        ("/gpu/public/summary", None),
        ("/gpu/public/nodes", None),
        ("/gpu/public/utilization", {"window": "24h", "group": "region"}),
        ("/gpu/public/utilization", {"window": "7d", "group": "model"}),
        ("/gpu/public/schema", None),
    ],
)
def test_no_sentinel_in_public_response(path, params, sentinel_db_client):
    with patch("src.db.gpu_rollups.get_db", return_value=sentinel_db_client):
        response = client.get(path, params=params)

    assert response.status_code == 200, response.text
    haystack = _recursive_haystack(response.json())
    for sentinel in ALL_SENTINELS:
        assert (
            sentinel.lower() not in haystack
        ), f"{path}: sentinel {sentinel!r} leaked into the public response body"


def test_nodes_response_exposes_only_allowlisted_keys(sentinel_db_client):
    with patch("src.db.gpu_rollups.get_db", return_value=sentinel_db_client):
        response = client.get("/gpu/public/nodes")

    assert response.status_code == 200
    body = response.json()
    assert body, "negative-control check needs at least one node in the response"
    for node in body:
        assert set(node.keys()) == {
            "name",
            "region",
            "gpu_model",
            "vram_gb",
            "status",
            "uptime_24h_pct",
            "models",
        }


def test_negative_control_sentinel_scan_can_detect_a_real_leak():
    """Proof the recursive scan actually works: a body that DOES contain a
    sentinel must fail the assertion the positive tests rely on."""
    leaking_body = {"nodes": [{"name": "ok", "endpoint_url": SENTINEL_ENDPOINT}]}
    haystack = _recursive_haystack(leaking_body)
    assert SENTINEL_ENDPOINT.lower() in haystack
