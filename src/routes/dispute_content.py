"""Dispute content vault routes (Gatewayz x GenLayer cost disputes, opt-in).

  POST /v1/jobs/{job_id}/usage/{i}/reveal   owner only: a short-lived link to line i's
                                            kept content bundle, for a dispute
  GET  /v1/dispute-content/{token}          public: the exact bundle bytes, for the
                                            GenLayer validators judging the dispute

The public route needs no key because validators have none; the unguessable,
expiring token is the credential, and only its sha256 is stored. The body is
served verbatim: validators hash it and compare with the line's content_hash.
See services/dispute_vault.py.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Path, Response

from src.db.inference_jobs import list_usage_v2
from src.routes.jobs import _error, _load, job_owner_id, jobs_read_rl
from src.services import dispute_vault
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

dispute_content_rl = create_endpoint_rate_limit(
    "dispute_content", max_requests=120, window_seconds=60
)


@router.post("/jobs/{job_id}/usage/{i}/reveal", tags=["jobs"])
async def reveal_usage_content(
    job_id: str,
    i: int = Path(..., ge=0),
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(jobs_read_rl),
) -> dict[str, Any]:
    if not dispute_vault.enabled():
        raise _error(404, "dispute_vault_disabled", "The dispute content vault is not enabled.")
    job = _load(job_id, user_id)
    entries_v2 = list_usage_v2(job["job_id"]) or []
    if i >= len(entries_v2):
        raise _error(404, "usage_line_not_found", f"The job has {len(entries_v2)} usage lines.")
    entry = entries_v2[i]
    if not entry:
        raise _error(404, "usage_line_has_no_v2_entry", "This line has no cost-dispute entry.")
    out = dispute_vault.reveal(job["job_id"], entry["request_id"])
    if out is None:
        raise _error(
            404,
            "content_not_kept",
            "No kept content for this line: it was not sent with X-Gatewayz-Dispute-Vault: on, "
            "was too large, or its dispute window has closed. Reveal your own copy instead.",
        )
    return {"job_id": job["job_id"], "index": i, **out}


@router.get("/dispute-content/{token}", tags=["jobs"])
async def get_dispute_content(token: str, _rl: None = Depends(dispute_content_rl)) -> Response:
    not_found = _error(404, "dispute_content_not_found", "No such link, or it has expired.")
    if not dispute_vault.enabled():
        raise not_found
    try:
        body = dispute_vault.open_reveal(token)
    except Exception:
        logger.exception("dispute content could not be served")
        raise _error(
            500, "dispute_content_unavailable", "The kept content failed its integrity check."
        )
    if body is None:
        raise not_found
    return Response(
        content=body, media_type="application/json", headers={"Cache-Control": "no-store"}
    )
