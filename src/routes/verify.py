"""Gatewayz Verify — submit a finished job to GenLayer, get a verdict back.

  POST /v1/verify/cases              open a case (or dry_run: true for the cost quote)
  GET  /v1/verify/cases/{id}         verdict schema v1
  POST /v1/verify/cases/{id}/appeal  GenLayer appeal; without confirm: true it only quotes the bond
  GET  /v1/verify/cap                this key's verify budget
  PUT  /v1/verify/cap                set it (separate from any inference cap)

Async by design: POST returns 202 while validators deliberate (seconds on Studionet,
up to hours with appeals on Bradbury). State changes go out as `verify.case.updated`
webhooks. Called per job — never per inference request.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from src.db import verify_cases as db
from src.db.inference_jobs import get_job
from src.security.deps import get_current_user
from src.services import verify_cases as cases
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit
from src.services.genlayer_verify import (
    VerifyUnavailable,
    config,
    get_client,
    is_configured,
    quote_usd,
)
from src.services.job_usage import ZERO_ROOT, is_job_key_name
from src.services.verify_fetch import FetchFailed, fetch_hash
from src.services.verify_rubric import RubricInvalid, resolve, rubric_hash

logger = logging.getLogger(__name__)
router = APIRouter()

_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
DEFAULT_VERIFY_CAP_USD = Decimal("25")
MAX_VERIFY_CAP_USD = Decimal("10000")
STALE_REFRESH_S = 10

verify_create_rl = create_endpoint_rate_limit("verify_create", max_requests=10, window_seconds=60)
verify_read_rl = create_endpoint_rate_limit("verify_read", max_requests=120, window_seconds=60)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"message": message, "type": "invalid_request_error", "code": code}},
    )


async def verify_user(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if is_job_key_name(user.get("key_name")):
        raise _error(403, "job_key_forbidden", "A job-scoped key can run inference only.")
    return user


def _client_or_501():
    if not is_configured():
        raise _error(501, "verify_not_configured", "Verify is not enabled on this deployment.")
    try:
        return get_client()
    except VerifyUnavailable as e:
        raise _error(501, "verify_not_configured", str(e)) from e


class CaseRequest(BaseModel):
    job_id: str | None = Field(
        None, description="a sealed Gatewayz job; its usage root rides along"
    )
    spec_uri: str = Field(..., max_length=512)
    spec_hash: str | None = None
    deliverable_uri: str | None = Field(None, max_length=512)
    deliverable_hash: str | None = None
    private: bool = False
    redacted_uri: str | None = Field(None, max_length=512)
    rubric: dict | str = Field(..., description="a rubric object or a template name")
    dry_run: bool = False


@router.post("/verify/cases", tags=["verify"], status_code=202)
async def open_case(
    body: CaseRequest,
    background: BackgroundTasks,
    user: dict[str, Any] = Depends(verify_user),
    _rl: None = Depends(verify_create_rl),
) -> dict[str, Any]:
    try:
        rubric = resolve(body.rubric)
    except RubricInvalid as e:
        raise _error(422, "rubric_invalid", f"rubric_invalid: {e}") from e

    # Privacy rule: validators read whatever URI they are given, so a private case
    # carries ONLY a redacted copy. The original URI is never stored or forwarded.
    if body.private:
        if not body.redacted_uri:
            raise _error(
                422,
                "private_case_needs_redacted_uri",
                "A private case must supply redacted_uri; validators never see the original.",
            )
        # deliverable_hash, if given, is the hash of the REDACTED bytes.
        judged_uri = body.redacted_uri
    else:
        if not body.deliverable_uri:
            raise _error(422, "deliverable_uri_required", "deliverable_uri is required.")
        judged_uri = body.deliverable_uri

    for name, val in (("spec_hash", body.spec_hash), ("deliverable_hash", body.deliverable_hash)):
        if val is not None and not _HEX32.match(val):
            raise _error(422, f"invalid_{name}", f"{name} must be 0x + 64 hex chars.")

    job = None
    usage_root = ZERO_ROOT
    if body.job_id:
        job = get_job(body.job_id.lower(), user["id"]) if _HEX32.match(body.job_id) else None
        if not job:
            raise _error(404, "job_not_found", "No such job.")
        if not job.get("usage_root"):
            raise _error(
                409, "job_not_sealed", "Close the job before submitting it for verification."
            )
        usage_root = job["usage_root"]

    try:
        spec_hash, deliverable_hash = await asyncio.gather(
            asyncio.to_thread(fetch_hash, body.spec_uri), asyncio.to_thread(fetch_hash, judged_uri)
        )
    except FetchFailed as e:
        raise _error(422, e.code, str(e)) from e
    if body.spec_hash and body.spec_hash.lower() != spec_hash:
        raise _error(422, "spec_hash_mismatch", f"{body.spec_uri} serves {spec_hash}.")
    if job and job["spec_hash"] != spec_hash:
        raise _error(422, "spec_hash_mismatch", "The spec does not match the job's spec_hash.")
    if body.deliverable_hash and body.deliverable_hash.lower() != deliverable_hash:
        raise _error(422, "deliverable_hash_mismatch", f"{judged_uri} serves {deliverable_hash}.")

    client = _client_or_501()
    quote = await asyncio.to_thread(quote_usd, client)
    summary = {
        "quote": quote,
        "spec_hash": spec_hash,
        "deliverable_hash": deliverable_hash,
        "rubric_hash": rubric_hash(rubric),
        "usage_root": usage_root,
    }
    if body.dry_run:
        return {"dry_run": True, **summary}

    amount = Decimal(quote["usd"])
    key_id = user.get("key_id")
    if key_id is None or not db.charge_cap(key_id, amount, DEFAULT_VERIFY_CAP_USD):
        raise _error(
            402,
            "verify_cap_exhausted",
            "This key's verify budget is spent; raise it with PUT /v1/verify/cap.",
        )

    case_id = job["job_id"] if job else "0x" + secrets.token_hex(32)
    if job and db.get_case(case_id):
        db.refund_cap(key_id, amount)
        raise _error(409, "case_exists", "This job already has a Verify case.")
    case = db.insert_case(
        {
            "case_id": case_id,
            "user_id": user["id"],
            "api_key_id": key_id,
            "job_id": job["job_id"] if job else None,
            "spec_uri": body.spec_uri,
            "spec_hash": spec_hash,
            "deliverable_uri": judged_uri,
            "deliverable_hash": deliverable_hash,
            "private": body.private,
            "usage_root": usage_root,
            "rubric": rubric,
            "rubric_hash": summary["rubric_hash"],
            "network": client.network,
            "contract_address": client.contract,
            "charged_usd": str(amount),
        }
    )
    background.add_task(cases.submit, case, client)
    return cases.view(case)


@router.get("/verify/cases/{case_id}", tags=["verify"])
async def get_case(
    case_id: str,
    user: dict[str, Any] = Depends(verify_user),
    _rl: None = Depends(verify_read_rl),
) -> dict[str, Any]:
    case = db.get_case(case_id.lower(), user["id"]) if _HEX32.match(case_id) else None
    if not case:
        raise _error(404, "case_not_found", "No such case.")
    last = case.get("last_checked_at")
    stale = not last or datetime.fromisoformat(str(last).replace("Z", "+00:00")) < datetime.now(
        UTC
    ) - timedelta(seconds=STALE_REFRESH_S)
    if case["status"] in db.OPEN_STATUSES and stale and is_configured():
        try:
            case = await asyncio.to_thread(cases.refresh, case, get_client())
        except Exception as e:  # the stored state is still a correct answer
            logger.warning("on-read refresh of %s failed: %s", case_id, e)
    return cases.view(case)


class AppealRequest(BaseModel):
    confirm: bool = False


@router.post("/verify/cases/{case_id}/appeal", tags=["verify"])
async def appeal_case(
    case_id: str,
    body: AppealRequest,
    user: dict[str, Any] = Depends(verify_user),
    _rl: None = Depends(verify_create_rl),
) -> dict[str, Any]:
    case = db.get_case(case_id.lower(), user["id"]) if _HEX32.match(case_id) else None
    if not case:
        raise _error(404, "case_not_found", "No such case.")
    if case["status"] in ("submitted", "adjudicating"):
        raise _error(409, "case_not_final", "There is no decision to appeal yet.")
    if case["status"] != "decided":
        raise _error(409, "appeal_not_open", f"A case that is {case['status']} cannot be appealed.")
    client = _client_or_501()
    bond = await asyncio.to_thread(client.min_appeal_bond, case["genlayer_tx"])
    quote = {
        "appeal_bond_gen_wei": str(bond) if bond is not None else None,
        "verify_charge": quote_usd(None),
    }
    if not body.confirm:
        return {"case_id": case["case_id"], "confirm_required": True, **quote}
    amount = Decimal(quote["verify_charge"]["usd"])
    if not db.charge_cap(case["api_key_id"], amount, DEFAULT_VERIFY_CAP_USD):
        raise _error(402, "verify_cap_exhausted", "This key's verify budget is spent.")
    try:
        tx = await asyncio.to_thread(client.appeal, case["genlayer_tx"], bond)
    except Exception as e:
        db.refund_cap(case["api_key_id"], amount)
        raise _error(502, "appeal_failed", f"GenLayer refused the appeal: {str(e)[:200]}") from e
    case = db.update_case(
        case["case_id"],
        {
            "status": "appealed",
            "appeal_tx": tx,
            "charged_usd": str(Decimal(str(case.get("charged_usd") or 0)) + amount),
        },
    )
    return cases.view(case)


class CapRequest(BaseModel):
    cap_usd: str


@router.get("/verify/cap", tags=["verify"])
async def get_verify_cap(user: dict[str, Any] = Depends(verify_user)) -> dict[str, Any]:
    row = db.get_cap(user["key_id"]) or {}
    return {
        "cap_usd": str(row.get("cap_usd", DEFAULT_VERIFY_CAP_USD)),
        "spent_usd": str(row.get("spent_usd", "0")),
        "price_per_case": quote_usd(None),
        "network": config()["network"],
    }


@router.put("/verify/cap", tags=["verify"])
async def put_verify_cap(
    body: CapRequest, user: dict[str, Any] = Depends(verify_user)
) -> dict[str, Any]:
    try:
        cap = Decimal(body.cap_usd)
    except InvalidOperation as e:
        raise _error(422, "invalid_cap", "cap_usd must be a decimal string.") from e
    if not cap.is_finite() or cap < 0 or cap > MAX_VERIFY_CAP_USD:
        raise _error(422, "invalid_cap", f"cap_usd must be between 0 and {MAX_VERIFY_CAP_USD}.")
    row = db.set_cap(user["key_id"], cap)
    return {"cap_usd": str(row["cap_usd"]), "spent_usd": str(row.get("spent_usd", "0"))}
