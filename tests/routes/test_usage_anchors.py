"""Tests for src.routes.usage_anchors -- public anchor reads, per-user
inclusion proofs, and the superadmin manual run."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_current_user, require_superadmin
from src.services.chain.usage_anchor import ProofNotAvailableError, ProofNotFoundError

client = TestClient(app)

CONTRACT = "0x00000000000000000000000000000000000000aa"
_ROW = {
    "chain_id": 43113,
    "contract_address": CONTRACT,
    "day": "2026-10-18",
    "day_index": 20379,
    "root": "0x" + "42" * 32,
    "leaf_count": 3,
    "status": "confirmed",
    "tx_hash": "0x" + "ab" * 32,
    "block_number": 123,
}


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def configured(monkeypatch):
    from src.routes import usage_anchors

    monkeypatch.setattr(usage_anchors.Config, "USAGE_ANCHOR_CONTRACT_ADDRESS", CONTRACT)
    monkeypatch.setattr(usage_anchors.Config, "USAGE_ANCHOR_CHAIN_ID", 43113)
    monkeypatch.setattr(usage_anchors.Config, "USAGE_ANCHOR_PRIVATE_KEY", None)


def test_well_known_unconfigured_is_sane(monkeypatch):
    from src.routes import usage_anchors

    monkeypatch.setattr(usage_anchors.Config, "USAGE_ANCHOR_CONTRACT_ADDRESS", None)
    response = client.get("/.well-known/anchors.json")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False and body["anchors"] == []
    assert "RFC 6962" in body["merkle"]["spec"]


@patch("src.routes.usage_anchors.db.list_anchors", return_value=[_ROW])
def test_well_known_lists_anchors_with_explorer_links(mock_list, configured):
    body = client.get("/.well-known/anchors.json").json()
    assert body["chain_id"] == 43113 and body["network"] == "fuji-testnet"
    assert body["contract_explorer_url"] == f"https://testnet.snowtrace.io/address/{CONTRACT}"
    assert (
        body["anchors"][0]["explorer_url"] == f"https://testnet.snowtrace.io/tx/{_ROW['tx_hash']}"
    )
    # Only published states are listed.
    assert mock_list.call_args.kwargs["statuses"] == ["pending", "confirmed"]
    text = str(body).lower()
    assert "partner" not in text and "endorse" not in text
    # Request volume is never disclosed publicly.
    for anchor in body["anchors"]:
        assert "leaf_count" not in anchor and "tree_size" not in anchor
    assert "leaf_count" not in body


@patch("src.routes.usage_anchors.db.list_anchors", return_value=[_ROW])
def test_list_anchors_clamps_limit(mock_list, configured):
    response = client.get("/v1/anchors?limit=100000")
    assert response.status_code == 200
    assert mock_list.call_args.kwargs["limit"] == 366
    assert "leaf_count" not in response.text and "tree_size" not in response.text


@patch("src.routes.usage_anchors.db.get_anchor", return_value=_ROW)
def test_get_anchor_for_day(mock_get, configured):
    response = client.get("/v1/anchors/2026-10-18")
    assert response.status_code == 200
    assert response.json()["anchor"]["root"] == _ROW["root"]
    assert "leaf_count" not in response.text and "tree_size" not in response.text


@patch("src.routes.usage_anchors.db.get_anchor", return_value={**_ROW, "status": "failed"})
def test_unpublished_day_is_404(mock_get, configured):
    assert client.get("/v1/anchors/2026-10-18").status_code == 404


def test_bad_day_is_422(configured):
    assert client.get("/v1/anchors/not-a-date").status_code == 422


def test_proof_requires_auth():
    assert client.get("/v1/anchors/proof/1").status_code in (401, 403)


@patch("src.routes.usage_anchors.build_proof", return_value={"leaf_index": 0})
def test_proof_uses_the_callers_user_id(mock_proof):
    app.dependency_overrides[get_current_user] = lambda: {"id": 7}
    response = client.get("/v1/anchors/proof/55")
    assert response.status_code == 200
    mock_proof.assert_called_once_with(55, 7)


@patch("src.routes.usage_anchors.build_proof", side_effect=ProofNotFoundError("x"))
def test_proof_for_foreign_row_is_404(mock_proof):
    app.dependency_overrides[get_current_user] = lambda: {"id": 7}
    assert client.get("/v1/anchors/proof/55").status_code == 404


@patch("src.routes.usage_anchors.build_proof", side_effect=ProofNotAvailableError("not yet"))
def test_proof_for_unanchored_day_is_409(mock_proof):
    app.dependency_overrides[get_current_user] = lambda: {"id": 7}
    assert client.get("/v1/anchors/proof/55").status_code == 409


def test_admin_run_requires_superadmin():
    assert client.post("/admin/usage-anchor/run", json={}).status_code in (401, 403)


@patch("src.routes.usage_anchors.record_audit")
@patch(
    "src.routes.usage_anchors.run_usage_anchor_once",
    return_value={"day": "2026-10-18", "status": "pending"},
)
def test_admin_run_runs_and_audits(mock_run, mock_audit):
    app.dependency_overrides[require_superadmin] = lambda: {"id": 1, "role": "superadmin"}
    response = client.post("/admin/usage-anchor/run", json={"day": "2026-10-18"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending"
    assert str(mock_run.call_args.args[0]) == "2026-10-18"
    assert mock_audit.call_args.kwargs["action"] == "usage_anchor.run"
