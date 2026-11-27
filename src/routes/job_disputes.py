"""Cost disputes on job usage lines (Gatewayz x GenLayer, shadow mode).

  POST /v1/jobs/{job_id}/disputes                 owner only: contest 1-10 lines of a
                                                  sealed job; judged on GenLayer
  GET  /v1/jobs/{job_id}/disputes                 owner only: the job's disputes
  GET  /v1/jobs/{job_id}/disputes/{dispute_id}    owner only: one dispute + its verdict

The CostDispute Intelligent Contract rules under the Fair Billing Policy; Gatewayz
submits on the owner's behalf (the owner needs no wallet or GEN) and records the
verdict. Shadow mode: verdicts are recorded, no refund is paid. Webhook event:
`job.dispute.updated`. See services/cost_disputes.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from src.db import job_disputes as db
from src.routes.jobs import _error, _load, job_owner_id, jobs_create_rl, jobs_read_rl
from src.services import cost_disputes

logger = logging.getLogger(__name__)

router = APIRouter()

_HEX32 = re.compile(r"^0x[0-9a-fA-F]{64}$")


class DisputedLine(BaseModel):
    index: int = Field(..., ge=0, description="usage line index (see GET /v1/jobs/{id}/usage)")
    claim: str | None = Field(None, max_length=1000, description="why this charge is unfair")
    content_uri: str | None = Field(
        None,
        max_length=512,
        description="https URL serving this line's content bundle; omit to use the "
        "dispute vault (if the request was kept) or have the line judged on metadata",
    )


class OpenDisputeRequest(BaseModel):
    lines: list[DisputedLine] = Field(..., min_length=1, max_length=cost_disputes.MAX_LINES)


@router.post("/jobs/{job_id}/disputes", tags=["jobs"], status_code=202)
async def open_job_dispute(
    job_id: str,
    body: OpenDisputeRequest,
    background: BackgroundTasks,
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_create_rl),
) -> dict[str, Any]:
    if not cost_disputes.is_configured():
        raise _error(404, "cost_disputes_disabled", "Cost disputes are not enabled.")
    job = _load(job_id, user_id)
    indexes = [ln.index for ln in body.lines]
    claims = {str(ln.index): ln.claim for ln in body.lines if ln.claim}
    uris = {str(ln.index): ln.content_uri for ln in body.lines if ln.content_uri}
    try:
        # Reads every usage line and rebuilds the Merkle tree: keep it off the event loop.
        dispute = await asyncio.to_thread(
            cost_disputes.open_dispute, job, user_id, indexes, claims, uris
        )
    except cost_disputes.DisputeError as e:
        raise _error(e.status, e.code, e.message)
    logger.info(
        "dispute opened dispute=%s job=%s user=%s lines=%s",
        dispute["dispute_id"],
        job["job_id"],
        user_id,
        dispute["line_indexes"],
    )
    background.add_task(cost_disputes.submit_now, dispute["dispute_id"])
    return cost_disputes.view(dispute)


@router.get("/jobs/{job_id}/disputes", tags=["jobs"])
async def list_job_disputes(
    job_id: str,
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_read_rl),
) -> dict[str, Any]:
    job = _load(job_id, user_id)
    return {
        "job_id": job["job_id"],
        "disputes": [cost_disputes.view(d) for d in db.list_for_job(job["job_id"], user_id)],
    }


@router.get("/jobs/{job_id}/disputes/{dispute_id}", tags=["jobs"])
async def get_job_dispute(
    job_id: str,
    dispute_id: str,
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_read_rl),
) -> dict[str, Any]:
    job = _load(job_id, user_id)
    not_found = _error(404, "dispute_not_found", "No such dispute on this job.")
    if not _HEX32.match(dispute_id):
        raise not_found
    d = db.get_dispute(dispute_id.lower(), user_id)
    if not d or d["job_id"] != job["job_id"]:
        raise not_found
    return cost_disputes.view(d)
