"""Inference jobs — job-scoped keys and sealed usage records.

Gatewayz x GenLayer inference escrow (PRD 2026-10-01, Feature 3). The contracts,
relayer and demo live in Alpaca-Network/gatewayz-genlayer; this module is the
Gatewayz side of the "sealed usage record":

  POST /v1/jobs                       create a job + its job-scoped key (shown once)
  GET  /v1/jobs/{id}                  job state
  POST /v1/jobs/{id}/close            retire the key, seal the log into a Merkle root
  GET  /v1/jobs/{id}/usage            totals + root (sealed, or live preview while running)
  GET  /v1/jobs/{id}/usage/proof?i=n  one line + its Merkle path

A job key is an ordinary key: the inference endpoints do not change. The usage
log holds billing metadata only — never prompt or completion content.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from src.db.api_keys import create_api_key
from src.db.inference_jobs import get_job, insert_job, list_usage, mark_closed, store_seal
from src.security.deps import get_current_user
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit
from src.services.job_usage import JOB_KEY_SCOPES, is_job_key_name, job_key_name, proof_for, seal

logger = logging.getLogger(__name__)

router = APIRouter()

_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
MAX_CAP_USD = Decimal("10000")
MAX_JOB_DAYS = 30

jobs_create_rl = create_endpoint_rate_limit("jobs_create", max_requests=20, window_seconds=60)
jobs_read_rl = create_endpoint_rate_limit("jobs_read", max_requests=120, window_seconds=60)


async def job_owner_id(user: dict[str, Any] = Depends(get_current_user)) -> int:
    """The account managing jobs. A job key may run inference and nothing else:
    if it could open jobs it could mint fresh keys and escape its own cap."""
    if is_job_key_name(user.get("key_name")):
        raise _error(403, "job_key_forbidden", "A job-scoped key cannot manage jobs.")
    return user["id"]


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"message": message, "type": "invalid_request_error", "code": code}},
    )


class CreateJobRequest(BaseModel):
    spec_hash: str = Field(..., description="sha256 of the job spec bytes, 0x-prefixed")
    cap_usd: str = Field(..., description="USD spend cap for the job key, as a decimal string")
    deadline: datetime = Field(..., description="ISO-8601; the key stops working after it")
    buyer: str | None = Field(None, max_length=128)
    seller: str | None = Field(None, max_length=128)

    @field_validator("spec_hash")
    @classmethod
    def _hex32(cls, v: str) -> str:
        if not _HEX32.match(v):
            raise ValueError("spec_hash must be 0x + 64 hex chars")
        return v.lower()

    @field_validator("cap_usd")
    @classmethod
    def _cap(cls, v: str) -> str:
        try:
            d = Decimal(v)
        except InvalidOperation as e:
            raise ValueError("cap_usd must be a decimal string") from e
        if not d.is_finite() or d <= 0 or d > MAX_CAP_USD:
            raise ValueError(f"cap_usd must be > 0 and <= {MAX_CAP_USD}")
        return str(d)


def _job_view(job: dict) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "spec_hash": job["spec_hash"],
        "buyer": job.get("buyer"),
        "seller": job.get("seller"),
        "cap_usd": str(job["cap_usd"]),
        "spent_usd": str(job["spent_usd"]),
        "deadline": job["deadline"],
        "usage_root": job.get("usage_root"),
        "escrow_tx": job.get("escrow_tx"),
        "genlayer_tx": job.get("genlayer_tx"),
        "created_at": job.get("created_at"),
        "closed_at": job.get("closed_at"),
    }


def _load(job_id: str, user_id: int) -> dict:
    if not _HEX32.match(job_id):
        raise _error(404, "job_not_found", "No such job.")
    job = get_job(job_id.lower(), user_id)
    if not job:
        raise _error(404, "job_not_found", "No such job.")
    return job


@router.post("/jobs", tags=["jobs"], status_code=201)
async def create_job(
    body: CreateJobRequest,
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_create_rl),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    deadline = body.deadline if body.deadline.tzinfo else body.deadline.replace(tzinfo=UTC)
    if deadline <= now or deadline > now + timedelta(days=MAX_JOB_DAYS):
        raise _error(422, "invalid_deadline", f"deadline must be in the next {MAX_JOB_DAYS} days.")

    job_id = "0x" + secrets.token_hex(32)
    # The key is billed to the caller's account like any other key; the job adds a
    # USD cap on top. expiration_days rounds the deadline UP so the key's own
    # expiry never cuts a job short — the job deadline is the binding limit.
    days = max(1, -(-int((deadline - now).total_seconds()) // 86400))
    api_key, api_key_id = create_api_key(
        user_id=user_id,
        key_name=job_key_name(job_id),
        expiration_days=days,
        scope_permissions=JOB_KEY_SCOPES,
    )
    job = insert_job(
        {
            "job_id": job_id,
            "user_id": user_id,
            "api_key_id": api_key_id,
            "buyer": body.buyer,
            "seller": body.seller,
            "spec_hash": body.spec_hash,
            "cap_usd": body.cap_usd,
            "deadline": deadline.isoformat(),
        }
    )
    logger.info("job created job=%s user=%s cap_usd=%s", job_id, user_id, body.cap_usd)
    return {**_job_view(job), "api_key": api_key}


@router.get("/jobs/{job_id}", tags=["jobs"])
async def get_job_state(
    job_id: str,
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_read_rl),
) -> dict[str, Any]:
    return _job_view(_load(job_id, user_id))


@router.post("/jobs/{job_id}/close", tags=["jobs"])
async def close_job(
    job_id: str,
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_create_rl),
) -> dict[str, Any]:
    """Idempotent. Retires the job key first, then seals exactly the stored lines."""
    job = _load(job_id, user_id)
    if job.get("usage_root"):
        return {**_job_view(job), "usage": _usage_totals(job)}
    mark_closed(job["job_id"], job.get("api_key_id"))
    sealed = seal(list_usage(job["job_id"]))
    job = store_seal(job["job_id"], sealed)
    return {**_job_view(job), "usage": _usage_totals(job)}


def _usage_totals(job: dict) -> dict[str, Any]:
    return {
        "root": job["usage_root"],
        "requests": job["usage_requests"],
        "tokens_in": job["usage_tokens_in"],
        "tokens_out": job["usage_tokens_out"],
        "cost_usd": job["usage_cost_usd"],
        "sealed": True,
    }


@router.get("/jobs/{job_id}/usage", tags=["jobs"])
async def get_job_usage(
    job_id: str,
    full: bool = Query(False, description="include every line (billing metadata only)"),
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_read_rl),
) -> dict[str, Any]:
    job = _load(job_id, user_id)
    entries = list_usage(job["job_id"])
    if job.get("usage_root"):
        out = _usage_totals(job)
        # The stored root is authoritative; recompute to catch a tampered log.
        if seal(entries)["root"] != job["usage_root"]:
            logger.error("usage log for %s no longer matches its sealed root", job["job_id"])
            raise _error(500, "usage_root_mismatch", "The stored log does not match its seal.")
    else:
        out = {**seal(entries), "sealed": False}
    out["job_id"] = job["job_id"]
    if full:
        out["entries"] = entries
    return out


@router.get("/jobs/{job_id}/usage/proof", tags=["jobs"])
async def get_job_usage_proof(
    job_id: str,
    i: int = Query(..., ge=0),
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_read_rl),
) -> dict[str, Any]:
    job = _load(job_id, user_id)
    if not job.get("usage_root"):
        raise _error(409, "job_not_sealed", "Close the job before requesting proofs.")
    entries = list_usage(job["job_id"])
    if i >= len(entries):
        raise _error(404, "usage_line_not_found", f"The job has {len(entries)} usage lines.")
    return {"job_id": job["job_id"], **proof_for(entries, i)}
