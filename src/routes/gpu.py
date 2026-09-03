"""GPU provider/node registry, node auth, heartbeats, admin approval, and
the liveness sweep (Milestone 4 W-A1, gatewayz-backend#2262).

Community GPU operators register a provider account, get admin-approved,
then register nodes that serve open-weight models through an
OpenAI-compatible server. This module owns everything under
`/gpu/providers`, `/gpu/nodes`, and `/gpu/admin/providers` -- routing
traffic TO these nodes (the `community` provider adapter) is W-A2's job
(spec section 4), as is spot-check verification and payouts (W-B, spec
section 5) and the public transparency feed (W-C, spec section 6).

See spec: .../scratchpad/m4/spec.md sections 2-3.
"""

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.db.gpu import (
    create_node,
    create_provider,
    get_node,
    get_provider,
    get_provider_by_user,
    list_nodes,
    list_providers,
    record_heartbeat,
    set_node_status,
    set_provider_status,
    update_node,
)
from src.db.user_wallets import get_wallet
from src.security.deps import get_user_id, require_admin
from src.security.node_auth import get_node as get_auth_node
from src.security.wallet_signature import verify_wallet_signature
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit
from src.services.gpu.node_probe import NodeProbeError, probe_node_models
from src.utils.crypto import encrypt_api_key, sha256_key_hash
from src.utils.wallet_address import normalize_wallet_address

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gpu")

_NODE_TOKEN_PREFIX = "gw_node_"

# Node-bearer heartbeat rate limit (spec section 3: 6/min/node). The node's
# own bearer token IS the rate-limit key here (create_endpoint_rate_limit
# extracts the raw Authorization bearer value), which is unique per node --
# equivalent to keying by the token hash without needing a second lookup.
heartbeat_rl = create_endpoint_rate_limit("gpu_heartbeat", max_requests=6, window_seconds=60)

# A signature older (or newer, clock skew) than this is never attested --
# matches the W-E node agent's assumption that `ts` is close to send time.
_HEARTBEAT_SIGNATURE_MAX_SKEW_SECONDS = 300


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------


class CreateProviderRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=200)
    payout_wallet_address: str = Field(..., min_length=42, max_length=42)
    contact_email: str | None = Field(default=None, max_length=320)
    region_default: str | None = Field(default=None, max_length=100)

    @field_validator("payout_wallet_address")
    @classmethod
    def _validate_wallet(cls, v):
        return normalize_wallet_address(v)


class NodeModelSpec(BaseModel):
    id: str = Field(..., min_length=1, max_length=200)
    max_context: int | None = Field(default=None, gt=0)


class CreateNodeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    region: str = Field(..., min_length=1, max_length=100)
    gpu_model: str = Field(..., min_length=1, max_length=200)
    vram_gb: int = Field(..., gt=0)
    bandwidth_mbps: int | None = Field(default=None, gt=0)
    endpoint_url: str = Field(..., min_length=1, max_length=2000)
    endpoint_api_key: str = Field(..., min_length=1, max_length=500)
    models: list[NodeModelSpec] = Field(..., min_length=1)

    @field_validator("endpoint_url")
    @classmethod
    def _validate_https(cls, v):
        if not v.startswith("https://"):
            raise ValueError("endpoint_url must be https")
        return v


class UpdateNodeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    region: str | None = Field(default=None, min_length=1, max_length=100)
    gpu_model: str | None = Field(default=None, min_length=1, max_length=200)
    vram_gb: int | None = Field(default=None, gt=0)
    bandwidth_mbps: int | None = Field(default=None, gt=0)
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=2000)
    endpoint_api_key: str | None = Field(default=None, min_length=1, max_length=500)
    models: list[NodeModelSpec] | None = Field(default=None, min_length=1)

    @field_validator("endpoint_url")
    @classmethod
    def _validate_https(cls, v):
        if v is not None and not v.startswith("https://"):
            raise ValueError("endpoint_url must be https")
        return v


class HeartbeatLoad(BaseModel):
    outstanding: int = Field(..., ge=0)
    gpu_util_pct: float | None = Field(default=None, ge=0, le=100)


class HeartbeatSignature(BaseModel):
    """Wire shape decided by W-E (scripts/gpu_node_agent.py,
    build_heartbeat_payload): the spec names the signed message
    (f"gatewayz-heartbeat:{node_id}:{ts}") but not how it's carried. `ts`
    travels alongside the signature rather than being re-derived from
    server time -- clock skew/latency would otherwise break verification."""

    ts: int = Field(..., gt=0)
    value: str = Field(..., min_length=1, max_length=200)


class HeartbeatRequest(BaseModel):
    load: HeartbeatLoad
    # Model ids the node currently serves (self-reported from its local
    # /v1/models) -- plain strings, not the richer {id, max_context, dtype}
    # shape used at node registration (spec section 2's gpu_nodes.models).
    models: list[str] | None = None
    version: str | None = Field(default=None, max_length=100)
    signature: HeartbeatSignature | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_node_token() -> str:
    return _NODE_TOKEN_PREFIX + secrets.token_urlsafe(24)


def _provider_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "display_name": row.get("display_name"),
        "payout_wallet_address": row.get("payout_wallet_address"),
        "contact_email": row.get("contact_email"),
        "status": row.get("status"),
        "region_default": row.get("region_default"),
        "created_at": row.get("created_at"),
        "approved_at": row.get("approved_at"),
    }


def _node_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "provider_id": row.get("provider_id"),
        "name": row.get("name"),
        "region": row.get("region"),
        "gpu_model": row.get("gpu_model"),
        "vram_gb": row.get("vram_gb"),
        "bandwidth_mbps": row.get("bandwidth_mbps"),
        "endpoint_url": row.get("endpoint_url"),
        "models": row.get("models"),
        "status": row.get("status"),
        "last_heartbeat_at": row.get("last_heartbeat_at"),
        "health_score": row.get("health_score"),
        "outstanding_requests": row.get("outstanding_requests"),
        "created_at": row.get("created_at"),
    }


def _require_own_provider(user_id: int) -> dict[str, Any]:
    provider = get_provider_by_user(user_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider_not_registered")
    return provider


def _require_own_node(user_id: int, node_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    provider = _require_own_provider(user_id)
    node = get_node(node_id)
    if node is None or node.get("provider_id") != provider.get("id"):
        raise HTTPException(status_code=404, detail="node_not_found")
    return provider, node


def _probe_and_validate_models(
    endpoint_url: str, endpoint_api_key: str, declared: list[NodeModelSpec]
) -> None:
    try:
        available = probe_node_models(endpoint_url, endpoint_api_key)
    except NodeProbeError as e:
        raise HTTPException(status_code=400, detail=e.reason) from e

    declared_ids = {m.id for m in declared}
    if not declared_ids.issubset(available):
        raise HTTPException(status_code=400, detail="models_mismatch")


def _encrypt_endpoint_key(plaintext: str) -> str:
    try:
        token, _version = encrypt_api_key(plaintext)
        return token
    except RuntimeError as e:
        logger.error("Node endpoint key encryption unavailable: %s", e)
        raise HTTPException(status_code=503, detail="encryption_unavailable") from e


def _merge_heartbeat_models(
    existing_models: list[dict[str, Any]] | None, reported_ids: list[str]
) -> list[dict[str, Any]]:
    """Rebuild the stored `models` jsonb (spec section 2's {id, max_context,
    dtype} shape) from a heartbeat's plain id list, preserving max_context/
    dtype for ids that were already declared at registration/patch time."""
    existing_by_id = {m.get("id"): m for m in (existing_models or [])}
    return [existing_by_id.get(model_id, {"id": model_id}) for model_id in reported_ids]


def _earnings_summary(provider_id: int) -> dict[str, Any]:
    """Accrued/settled totals for a provider. The provider_earnings CRUD
    module belongs to W-B (spec section 5) -- this reads the table
    directly with the same safe-default (zeros) on any error, so
    GET /gpu/providers/me has something to show before W-B ships."""
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()
        result = (
            client.table("provider_earnings")
            .select("amount_wei, status")
            .eq("provider_id", provider_id)
            .execute()
        )
        rows = result.data or []
    except Exception as e:
        logger.warning(f"provider_earnings summary lookup failed for {provider_id}: {e}")
        rows = []

    accrued = sum(int(r["amount_wei"]) for r in rows if r.get("status") == "accrued")
    settled = sum(int(r["amount_wei"]) for r in rows if r.get("status") == "settled")
    return {"accrued_wei": str(accrued), "settled_wei": str(settled)}


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


@router.post("/providers", tags=["gpu"], status_code=201)
async def register_provider(
    body: CreateProviderRequest, user_id: int = Depends(get_user_id)
) -> dict[str, Any]:
    wallet = get_wallet(body.payout_wallet_address)
    if wallet is None or wallet.get("user_id") != user_id:
        raise HTTPException(status_code=400, detail="wallet_not_linked")

    if get_provider_by_user(user_id) is not None:
        raise HTTPException(status_code=409, detail="provider_already_registered")

    provider = create_provider(
        user_id=user_id,
        display_name=body.display_name,
        payout_wallet_address=body.payout_wallet_address,
        contact_email=body.contact_email,
        region_default=body.region_default,
    )
    if provider is None:
        # Insert raced with a concurrent registration, or a transient DB
        # error -- re-check rather than assume either (same pattern as
        # wallet_auth.wallet_link).
        existing = get_provider_by_user(user_id)
        if existing is not None:
            raise HTTPException(status_code=409, detail="provider_already_registered")
        raise HTTPException(status_code=500, detail="provider_registration_failed")

    return {"success": True, "data": _provider_view(provider)}


@router.get("/providers/me", tags=["gpu"])
async def get_my_provider(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    provider = _require_own_provider(user_id)
    nodes = list_nodes(provider["id"])
    return {
        "success": True,
        "data": {
            "provider": _provider_view(provider),
            "nodes": [_node_view(n) for n in nodes],
            "earnings": _earnings_summary(provider["id"]),
        },
    }


# ---------------------------------------------------------------------------
# Node registration/management
# ---------------------------------------------------------------------------


@router.post("/nodes", tags=["gpu"], status_code=201)
async def register_node(
    body: CreateNodeRequest, user_id: int = Depends(get_user_id)
) -> dict[str, Any]:
    provider = _require_own_provider(user_id)
    if provider.get("status") != "approved":
        raise HTTPException(status_code=403, detail="provider_not_approved")

    _probe_and_validate_models(body.endpoint_url, body.endpoint_api_key, body.models)
    encrypted_key = _encrypt_endpoint_key(body.endpoint_api_key)

    node_token = _generate_node_token()
    node_token_hash = sha256_key_hash(node_token)

    node = create_node(
        provider_id=provider["id"],
        name=body.name,
        region=body.region,
        gpu_model=body.gpu_model,
        vram_gb=body.vram_gb,
        bandwidth_mbps=body.bandwidth_mbps,
        endpoint_url=body.endpoint_url,
        endpoint_api_key_encrypted=encrypted_key,
        models=[m.model_dump() for m in body.models],
        node_token_hash=node_token_hash,
    )
    if node is None:
        raise HTTPException(status_code=500, detail="node_registration_failed")

    return {
        "success": True,
        "data": {
            "node": _node_view(node),
            # Shown exactly once -- only the hash is stored.
            "node_token": node_token,
        },
    }


@router.patch("/nodes/{node_id}", tags=["gpu"])
async def update_node_route(
    node_id: int, body: UpdateNodeRequest, user_id: int = Depends(get_user_id)
) -> dict[str, Any]:
    _provider, node = _require_own_node(user_id, node_id)

    updates: dict[str, Any] = {}
    for field in ("name", "region", "gpu_model", "vram_gb", "bandwidth_mbps"):
        value = getattr(body, field)
        if value is not None:
            updates[field] = value

    new_endpoint_url = body.endpoint_url or node["endpoint_url"]
    endpoint_changed = body.endpoint_url is not None or body.endpoint_api_key is not None
    models_changed = body.models is not None
    if endpoint_changed or models_changed:
        # Re-probe with whatever endpoint/key/models are in effect after
        # this update -- a stale, unreachable endpoint or a model list
        # that the node no longer actually serves must never pass silently.
        probe_key = body.endpoint_api_key
        if probe_key is None:
            raise HTTPException(status_code=400, detail="endpoint_api_key_required_to_reverify")
        declared = (
            body.models
            if body.models is not None
            else [NodeModelSpec(**m) for m in (node.get("models") or [])]
        )
        _probe_and_validate_models(new_endpoint_url, probe_key, declared)

    if body.endpoint_url is not None:
        updates["endpoint_url"] = body.endpoint_url
    if body.endpoint_api_key is not None:
        updates["endpoint_api_key_encrypted"] = _encrypt_endpoint_key(body.endpoint_api_key)
    if body.models is not None:
        updates["models"] = [m.model_dump() for m in body.models]

    if not updates:
        return {"success": True, "data": _node_view(node)}

    updated = update_node(node_id, updates)
    if updated is None:
        raise HTTPException(status_code=500, detail="node_update_failed")
    return {"success": True, "data": _node_view(updated)}


@router.delete("/nodes/{node_id}", tags=["gpu"])
async def delete_node_route(node_id: int, user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    _provider, _node = _require_own_node(user_id, node_id)
    updated = set_node_status(node_id, "disabled")
    if updated is None:
        raise HTTPException(status_code=500, detail="node_disable_failed")
    return {"success": True, "data": _node_view(updated)}


@router.post("/nodes/{node_id}/rotate-token", tags=["gpu"])
async def rotate_node_token(node_id: int, user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    _provider, _node = _require_own_node(user_id, node_id)
    node_token = _generate_node_token()
    updated = update_node(node_id, {"node_token_hash": sha256_key_hash(node_token)})
    if updated is None:
        raise HTTPException(status_code=500, detail="node_token_rotation_failed")
    return {"success": True, "data": {"node": _node_view(updated), "node_token": node_token}}


# ---------------------------------------------------------------------------
# Heartbeat (node-bearer auth, not user auth)
# ---------------------------------------------------------------------------


@router.post("/nodes/{node_id}/heartbeat", tags=["gpu"])
async def node_heartbeat(
    node_id: int,
    body: HeartbeatRequest,
    node: dict[str, Any] = Depends(get_auth_node),
    _rl: None = Depends(heartbeat_rl),
) -> dict[str, Any]:
    if node.get("id") != node_id:
        # The bearer token authenticates a specific node -- a token for
        # node A can't be used to heartbeat node B.
        raise HTTPException(status_code=403, detail="node_token_mismatch")

    attested_heartbeat = False
    if body.signature is not None:
        skew = abs(int(time.time()) - body.signature.ts)
        if skew <= _HEARTBEAT_SIGNATURE_MAX_SKEW_SECONDS:
            provider = get_provider(node["provider_id"])
            payout_wallet = provider.get("payout_wallet_address") if provider else None
            if payout_wallet:
                message = f"gatewayz-heartbeat:{node_id}:{body.signature.ts}"
                attested_heartbeat = verify_wallet_signature(
                    payout_wallet, message, body.signature.value
                )

    models_payload = (
        _merge_heartbeat_models(node.get("models"), body.models)
        if body.models is not None
        else None
    )
    updated = record_heartbeat(
        node_id=node_id,
        outstanding=body.load.outstanding,
        models=models_payload,
        attested=attested_heartbeat,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="heartbeat_recording_failed")

    return {
        "success": True,
        "data": {"node": _node_view(updated), "attested_heartbeat": attested_heartbeat},
    }


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@router.post("/admin/providers/{provider_id}/approve", tags=["gpu"])
async def admin_approve_provider(
    provider_id: int, admin_user: dict[str, Any] = Depends(require_admin)
) -> dict[str, Any]:
    updated = set_provider_status(provider_id, "approved", approved_by=admin_user.get("id"))
    if updated is None:
        raise HTTPException(status_code=404, detail="provider_not_found")
    return {"success": True, "data": _provider_view(updated)}


@router.post("/admin/providers/{provider_id}/suspend", tags=["gpu"])
async def admin_suspend_provider(
    provider_id: int, _admin_user: dict[str, Any] = Depends(require_admin)
) -> dict[str, Any]:
    updated = set_provider_status(provider_id, "suspended")
    if updated is None:
        raise HTTPException(status_code=404, detail="provider_not_found")
    return {"success": True, "data": _provider_view(updated)}


@router.get("/admin/providers", tags=["gpu"])
async def admin_list_providers(
    status: str | None = None, _admin_user: dict[str, Any] = Depends(require_admin)
) -> dict[str, Any]:
    providers = list_providers(status=status)
    return {"success": True, "data": {"providers": [_provider_view(p) for p in providers]}}
