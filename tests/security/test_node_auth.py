"""Tests for src.security.node_auth (Milestone 4 W-A1, gatewayz-backend#2262)."""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from src.security.node_auth import get_node
from src.utils.crypto import sha256_key_hash

# get_node_by_token_hash hashes the presented bearer token via
# sha256_key_hash, which requires KEY_HASH_SALT to be configured -- same
# setup as tests/conceptual_model/test_cm01_auth_api_key_security.py.
os.environ.setdefault("KEY_HASH_SALT", "test-key-hash-salt-minimum-sixteen")


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_get_node_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc_info:
        await get_node(request=MagicMock(), credentials=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "node_token_required"


@pytest.mark.asyncio
async def test_get_node_rejects_wrong_token_prefix():
    with pytest.raises(HTTPException) as exc_info:
        await get_node(request=MagicMock(), credentials=_creds("gw_live_notanode"))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid_node_token"


@pytest.mark.asyncio
async def test_get_node_rejects_unknown_token():
    with patch("src.security.node_auth.get_node_by_token_hash", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await get_node(request=MagicMock(), credentials=_creds("gw_node_" + "a" * 32))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid_node_token"


@pytest.mark.asyncio
async def test_get_node_rejects_disabled_node():
    node_row = {"id": 1, "status": "disabled"}
    with patch("src.security.node_auth.get_node_by_token_hash", return_value=node_row):
        with pytest.raises(HTTPException) as exc_info:
            await get_node(request=MagicMock(), credentials=_creds("gw_node_" + "a" * 32))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "node_disabled"


@pytest.mark.asyncio
async def test_get_node_returns_active_node():
    node_row = {"id": 1, "status": "active"}
    token = "gw_node_" + "a" * 32
    with patch(
        "src.security.node_auth.get_node_by_token_hash", return_value=node_row
    ) as mock_lookup:
        result = await get_node(request=MagicMock(), credentials=_creds(token))

    assert result == node_row
    mock_lookup.assert_called_once_with(sha256_key_hash(token))
