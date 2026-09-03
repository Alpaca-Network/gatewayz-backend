"""Tests for src.routes.gpu (Milestone 4 W-A1, gatewayz-backend#2262)."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_user_id, require_admin
from src.security.node_auth import get_node as get_auth_node

# create_node's node_token_hash and node_auth's lookup both hash via
# sha256_key_hash, which requires KEY_HASH_SALT.
os.environ.setdefault("KEY_HASH_SALT", "test-key-hash-salt-minimum-sixteen")

client = TestClient(app)

_TEST_USER_ID = 42
_APPROVED_PROVIDER = {
    "id": 1,
    "user_id": _TEST_USER_ID,
    "display_name": "Acme GPUs",
    "payout_wallet_address": "0x" + "a" * 40,
    "contact_email": None,
    "status": "approved",
    "region_default": None,
    "created_at": "2026-09-03T00:00:00Z",
    "approved_at": "2026-09-03T00:00:00Z",
}
_PENDING_PROVIDER = {**_APPROVED_PROVIDER, "status": "pending"}

_VALID_NODE_BODY = {
    "name": "node-a",
    "region": "us-east",
    "gpu_model": "H100",
    "vram_gb": 80,
    "endpoint_url": "https://node.example.com",
    "endpoint_api_key": "node-secret-key",
    "models": [{"id": "llama-3.1-8b-instruct"}],
}


@pytest.fixture(autouse=True)
def _override_user_id():
    """Same save/restore pattern as tests/routes/test_wallet_auth.py --
    app.dependency_overrides is a shared dict across every route test
    module under pytest-xdist."""
    previous = app.dependency_overrides.get(get_user_id)
    app.dependency_overrides[get_user_id] = lambda: _TEST_USER_ID
    yield
    if previous is None:
        app.dependency_overrides.pop(get_user_id, None)
    else:
        app.dependency_overrides[get_user_id] = previous


def _override_admin():
    previous = app.dependency_overrides.get(require_admin)
    app.dependency_overrides[require_admin] = lambda: {"id": 999, "is_admin": True}
    return previous


def _restore_admin(previous):
    if previous is None:
        app.dependency_overrides.pop(require_admin, None)
    else:
        app.dependency_overrides[require_admin] = previous


def _override_node(node_row):
    previous = app.dependency_overrides.get(get_auth_node)
    app.dependency_overrides[get_auth_node] = lambda: node_row
    return previous


def _restore_node(previous):
    if previous is None:
        app.dependency_overrides.pop(get_auth_node, None)
    else:
        app.dependency_overrides[get_auth_node] = previous


# ---------------------------------------------------------------------------
# POST /gpu/providers
# ---------------------------------------------------------------------------


@patch("src.routes.gpu.create_provider")
@patch("src.routes.gpu.get_provider_by_user")
@patch("src.routes.gpu.get_wallet")
def test_register_provider_success(mock_get_wallet, mock_get_provider_by_user, mock_create):
    mock_get_wallet.return_value = {"user_id": _TEST_USER_ID, "wallet_address": "0x" + "a" * 40}
    mock_get_provider_by_user.return_value = None
    mock_create.return_value = _APPROVED_PROVIDER

    response = client.post(
        "/gpu/providers",
        json={
            "display_name": "Acme GPUs",
            "payout_wallet_address": "0x" + "A" * 40,
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["status"] == "approved"
    _, kwargs = mock_create.call_args
    # Pydantic's normalize_wallet_address validator lower-cases on the way in.
    assert kwargs["payout_wallet_address"] == "0x" + "a" * 40


@patch("src.routes.gpu.get_wallet")
def test_register_provider_rejects_unlinked_wallet(mock_get_wallet):
    mock_get_wallet.return_value = None

    response = client.post(
        "/gpu/providers",
        json={"display_name": "Acme GPUs", "payout_wallet_address": "0x" + "a" * 40},
    )

    assert response.status_code == 400
    assert response.json()["error"]["context"]["parameter_value"] == "wallet_not_linked"


@patch("src.routes.gpu.get_wallet")
def test_register_provider_rejects_wallet_linked_to_other_user(mock_get_wallet):
    mock_get_wallet.return_value = {"user_id": 999, "wallet_address": "0x" + "a" * 40}

    response = client.post(
        "/gpu/providers",
        json={"display_name": "Acme GPUs", "payout_wallet_address": "0x" + "a" * 40},
    )

    assert response.status_code == 400
    assert response.json()["error"]["context"]["parameter_value"] == "wallet_not_linked"


@patch("src.routes.gpu.get_provider_by_user")
@patch("src.routes.gpu.get_wallet")
def test_register_provider_rejects_second_registration(mock_get_wallet, mock_get_provider):
    mock_get_wallet.return_value = {"user_id": _TEST_USER_ID, "wallet_address": "0x" + "a" * 40}
    mock_get_provider.return_value = _APPROVED_PROVIDER

    response = client.post(
        "/gpu/providers",
        json={"display_name": "Acme GPUs", "payout_wallet_address": "0x" + "a" * 40},
    )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# GET /gpu/providers/me
# ---------------------------------------------------------------------------


@patch("src.routes.gpu._earnings_summary")
@patch("src.routes.gpu.list_nodes")
@patch("src.routes.gpu.get_provider_by_user")
def test_get_my_provider_returns_provider_nodes_and_earnings(
    mock_get_provider, mock_list_nodes, mock_earnings
):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_list_nodes.return_value = [{"id": 1, "provider_id": 1, "status": "active"}]
    mock_earnings.return_value = {"accrued_wei": "0", "settled_wei": "0"}

    response = client.get("/gpu/providers/me")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["provider"]["id"] == 1
    assert len(data["nodes"]) == 1
    assert data["earnings"] == {"accrued_wei": "0", "settled_wei": "0"}


@patch("src.routes.gpu.get_provider_by_user")
def test_get_my_provider_404_when_not_registered(mock_get_provider):
    mock_get_provider.return_value = None

    response = client.get("/gpu/providers/me")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /gpu/nodes
# ---------------------------------------------------------------------------


@patch("src.routes.gpu.get_provider_by_user")
def test_register_node_requires_approved_provider(mock_get_provider):
    mock_get_provider.return_value = _PENDING_PROVIDER

    response = client.post("/gpu/nodes", json=_VALID_NODE_BODY)

    assert response.status_code == 403
    # 403 detail text is masked into a canned message by the global
    # HTTPException handler (pre-existing, app-wide) -- status code is the
    # checked contract, same as wallet_auth's 409 tests.


@patch("src.routes.gpu.encrypt_api_key")
@patch("src.routes.gpu.create_node")
@patch("src.routes.gpu.probe_node_models")
@patch("src.routes.gpu.get_provider_by_user")
def test_register_node_success_shows_token_once_and_stores_only_hash(
    mock_get_provider, mock_probe, mock_create_node, mock_encrypt
):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_probe.return_value = {"llama-3.1-8b-instruct", "other-model"}
    mock_encrypt.return_value = ("encrypted-blob", 1)
    mock_create_node.return_value = {
        "id": 5,
        "provider_id": 1,
        "name": "node-a",
        "status": "registered",
    }

    response = client.post("/gpu/nodes", json=_VALID_NODE_BODY)

    assert response.status_code == 201
    body = response.json()["data"]
    node_token = body["node_token"]
    assert node_token.startswith("gw_node_")

    _, kwargs = mock_create_node.call_args
    assert kwargs["node_token_hash"] != node_token
    assert len(kwargs["node_token_hash"]) == 64  # sha256 hex digest
    # The plaintext token is never sent to the DB layer.
    assert node_token not in str(mock_create_node.call_args)


@patch("src.routes.gpu.probe_node_models")
@patch("src.routes.gpu.get_provider_by_user")
def test_register_node_endpoint_unreachable(mock_get_provider, mock_probe):
    from src.services.gpu.node_probe import NodeProbeError

    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_probe.side_effect = NodeProbeError("endpoint_unreachable")

    response = client.post("/gpu/nodes", json=_VALID_NODE_BODY)

    assert response.status_code == 400
    assert response.json()["error"]["context"]["parameter_value"] == "endpoint_unreachable"


@patch("src.routes.gpu.probe_node_models")
@patch("src.routes.gpu.get_provider_by_user")
def test_register_node_models_mismatch(mock_get_provider, mock_probe):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_probe.return_value = {"some-other-model"}  # doesn't include declared model

    response = client.post("/gpu/nodes", json=_VALID_NODE_BODY)

    assert response.status_code == 400
    assert response.json()["error"]["context"]["parameter_value"] == "models_mismatch"


def test_register_node_rejects_non_https_endpoint():
    body = {**_VALID_NODE_BODY, "endpoint_url": "http://node.example.com"}
    response = client.post("/gpu/nodes", json=body)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /gpu/nodes/{id}, DELETE, rotate-token
# ---------------------------------------------------------------------------


@patch("src.routes.gpu.update_node")
@patch("src.routes.gpu.get_node")
@patch("src.routes.gpu.get_provider_by_user")
def test_update_node_without_endpoint_change_skips_reprobe(
    mock_get_provider, mock_get_node, mock_update_node
):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_get_node.return_value = {
        "id": 5,
        "provider_id": 1,
        "endpoint_url": "https://node.example.com",
        "models": [{"id": "llama-3.1-8b-instruct"}],
    }
    mock_update_node.return_value = {"id": 5, "provider_id": 1, "name": "renamed"}

    with patch("src.routes.gpu.probe_node_models") as mock_probe:
        response = client.patch("/gpu/nodes/5", json={"name": "renamed"})
        mock_probe.assert_not_called()

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "renamed"


@patch("src.routes.gpu.encrypt_api_key")
@patch("src.routes.gpu.update_node")
@patch("src.routes.gpu.probe_node_models")
@patch("src.routes.gpu.get_node")
@patch("src.routes.gpu.get_provider_by_user")
def test_update_node_endpoint_change_reprobes(
    mock_get_provider, mock_get_node, mock_probe, mock_update_node, mock_encrypt
):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_get_node.return_value = {
        "id": 5,
        "provider_id": 1,
        "endpoint_url": "https://node.example.com",
        "models": [{"id": "llama-3.1-8b-instruct"}],
    }
    mock_probe.return_value = {"llama-3.1-8b-instruct"}
    mock_encrypt.return_value = ("encrypted-blob", 1)
    mock_update_node.return_value = {"id": 5, "provider_id": 1}

    response = client.patch(
        "/gpu/nodes/5",
        json={"endpoint_url": "https://new.example.com", "endpoint_api_key": "new-key"},
    )

    assert response.status_code == 200
    mock_probe.assert_called_once_with("https://new.example.com", "new-key")


@patch("src.routes.gpu.get_node")
@patch("src.routes.gpu.get_provider_by_user")
def test_update_node_404_when_not_owned(mock_get_provider, mock_get_node):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_get_node.return_value = {"id": 5, "provider_id": 999}  # different provider

    response = client.patch("/gpu/nodes/5", json={"name": "x"})

    assert response.status_code == 404


@patch("src.routes.gpu.set_node_status")
@patch("src.routes.gpu.get_node")
@patch("src.routes.gpu.get_provider_by_user")
def test_delete_node_disables(mock_get_provider, mock_get_node, mock_set_status):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_get_node.return_value = {"id": 5, "provider_id": 1}
    mock_set_status.return_value = {"id": 5, "provider_id": 1, "status": "disabled"}

    response = client.delete("/gpu/nodes/5")

    assert response.status_code == 200
    mock_set_status.assert_called_once_with(5, "disabled")


@patch("src.routes.gpu.update_node")
@patch("src.routes.gpu.get_node")
@patch("src.routes.gpu.get_provider_by_user")
def test_rotate_token_returns_new_token_once(mock_get_provider, mock_get_node, mock_update_node):
    mock_get_provider.return_value = _APPROVED_PROVIDER
    mock_get_node.return_value = {"id": 5, "provider_id": 1}
    mock_update_node.return_value = {"id": 5, "provider_id": 1}

    response = client.post("/gpu/nodes/5/rotate-token")

    assert response.status_code == 200
    assert response.json()["data"]["node_token"].startswith("gw_node_")


# ---------------------------------------------------------------------------
# POST /gpu/nodes/{id}/heartbeat (node-bearer auth)
# ---------------------------------------------------------------------------


def test_heartbeat_rejects_missing_token():
    response = client.post("/gpu/nodes/5/heartbeat", json={"load": {"outstanding": 0}})
    assert response.status_code == 401


@patch("src.security.node_auth.get_node_by_token_hash")
def test_heartbeat_rejects_disabled_node_token(mock_lookup):
    mock_lookup.return_value = {"id": 5, "status": "disabled"}

    response = client.post(
        "/gpu/nodes/5/heartbeat",
        json={"load": {"outstanding": 0}},
        headers={"Authorization": "Bearer gw_node_" + "a" * 32},
    )

    assert response.status_code == 403


@patch("src.routes.gpu.record_heartbeat")
def test_heartbeat_rejects_token_node_id_mismatch(mock_record):
    previous = _override_node({"id": 999, "provider_id": 1, "status": "active"})
    try:
        response = client.post("/gpu/nodes/5/heartbeat", json={"load": {"outstanding": 0}})
    finally:
        _restore_node(previous)

    assert response.status_code == 403
    # 403 detail text is masked by the global HTTPException handler.
    mock_record.assert_not_called()


@patch("src.routes.gpu.record_heartbeat")
def test_heartbeat_success_without_signature(mock_record):
    mock_record.return_value = {"id": 5, "provider_id": 1, "status": "active"}
    previous = _override_node({"id": 5, "provider_id": 1, "status": "active"})
    try:
        response = client.post("/gpu/nodes/5/heartbeat", json={"load": {"outstanding": 3}})
    finally:
        _restore_node(previous)

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["attested_heartbeat"] is False
    _, kwargs = mock_record.call_args
    assert kwargs["node_id"] == 5
    assert kwargs["outstanding"] == 3


@patch("src.routes.gpu.record_heartbeat")
@patch("src.routes.gpu.get_provider")
def test_heartbeat_with_valid_wallet_signature_is_attested(mock_get_provider, mock_record):
    account = Account.create()
    mock_get_provider.return_value = {
        "id": 1,
        "payout_wallet_address": account.address.lower(),
    }
    mock_record.return_value = {"id": 5, "provider_id": 1, "status": "active"}

    ts = int(time.time())
    message = f"gatewayz-heartbeat:5:{ts}"
    signature = account.sign_message(encode_defunct(text=message)).signature.hex()
    if not signature.startswith("0x"):
        signature = f"0x{signature}"

    previous = _override_node({"id": 5, "provider_id": 1, "status": "active"})
    try:
        response = client.post(
            "/gpu/nodes/5/heartbeat",
            json={"load": {"outstanding": 0}, "ts": ts, "signature": signature},
        )
    finally:
        _restore_node(previous)

    assert response.status_code == 200
    assert response.json()["data"]["attested_heartbeat"] is True
    _, kwargs = mock_record.call_args
    assert kwargs["attested"] is True


@patch("src.routes.gpu.record_heartbeat")
@patch("src.routes.gpu.get_provider")
def test_heartbeat_with_wrong_signer_is_not_attested(mock_get_provider, mock_record):
    owner = Account.create()
    impostor = Account.create()
    mock_get_provider.return_value = {"id": 1, "payout_wallet_address": owner.address.lower()}
    mock_record.return_value = {"id": 5, "provider_id": 1, "status": "active"}

    ts = int(time.time())
    message = f"gatewayz-heartbeat:5:{ts}"
    signature = impostor.sign_message(encode_defunct(text=message)).signature.hex()
    if not signature.startswith("0x"):
        signature = f"0x{signature}"

    previous = _override_node({"id": 5, "provider_id": 1, "status": "active"})
    try:
        response = client.post(
            "/gpu/nodes/5/heartbeat",
            json={"load": {"outstanding": 0}, "ts": ts, "signature": signature},
        )
    finally:
        _restore_node(previous)

    assert response.status_code == 200  # invalid signature doesn't fail the heartbeat
    assert response.json()["data"]["attested_heartbeat"] is False


@patch("src.services.endpoint_rate_limiter._check_rate_limit")
@patch("src.routes.gpu.record_heartbeat")
def test_heartbeat_honours_rate_limit(mock_record, mock_check_rl):
    mock_check_rl.return_value = (False, 0, 30)
    previous = _override_node({"id": 5, "provider_id": 1, "status": "active"})
    try:
        response = client.post(
            "/gpu/nodes/5/heartbeat",
            json={"load": {"outstanding": 0}},
            # The rate-limit dependency extracts its key straight from the
            # Authorization header (independent of the overridden node-auth
            # dependency) -- without one it skips limiting entirely.
            headers={"Authorization": "Bearer gw_node_" + "a" * 32},
        )
    finally:
        _restore_node(previous)

    assert response.status_code == 429
    mock_record.assert_not_called()


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@patch("src.routes.gpu.set_provider_status")
def test_admin_approve_provider(mock_set_status):
    mock_set_status.return_value = {**_PENDING_PROVIDER, "status": "approved"}
    previous = _override_admin()
    try:
        response = client.post("/gpu/admin/providers/1/approve")
    finally:
        _restore_admin(previous)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "approved"
    _, kwargs = mock_set_status.call_args
    assert kwargs["approved_by"] == 999


@patch("src.routes.gpu.set_provider_status")
def test_admin_suspend_provider(mock_set_status):
    mock_set_status.return_value = {**_APPROVED_PROVIDER, "status": "suspended"}
    previous = _override_admin()
    try:
        response = client.post("/gpu/admin/providers/1/suspend")
    finally:
        _restore_admin(previous)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "suspended"


@patch("src.routes.gpu.set_provider_status")
def test_admin_approve_provider_404_when_missing(mock_set_status):
    mock_set_status.return_value = None
    previous = _override_admin()
    try:
        response = client.post("/gpu/admin/providers/999/approve")
    finally:
        _restore_admin(previous)

    assert response.status_code == 404


def test_admin_endpoints_reject_non_admin():
    # No require_admin override -- falls through to the real dependency,
    # which needs a real user auth chain; simplest un-authed check is that
    # it's rejected rather than silently succeeding.
    response = client.post("/gpu/admin/providers/1/approve")
    assert response.status_code in (401, 403)


@patch("src.routes.gpu.list_providers")
def test_admin_list_providers(mock_list):
    mock_list.return_value = [_APPROVED_PROVIDER]
    previous = _override_admin()
    try:
        response = client.get("/gpu/admin/providers")
    finally:
        _restore_admin(previous)

    assert response.status_code == 200
    assert len(response.json()["data"]["providers"]) == 1
