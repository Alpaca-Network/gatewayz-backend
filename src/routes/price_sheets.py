"""Versioned price sheets for cost disputes (Gatewayz x GenLayer, shadow mode).

  GET /v1/price-sheets/{hash}   the exact sheet bytes whose sha256 is {hash}

Every v2 job usage line records the sha256 of the rates it was charged at (see
services/job_usage_v2.price_sheet). A GenLayer validator judging a disputed line
fetches this URL and hashes the body it gets back, so the response is the stored
bytes verbatim — never re-serialised. Sheets are content-addressed and immutable
(the table refuses updates and deletes), so a URL never changes what it serves.

Public on purpose: validators have no Gatewayz key, and the sheet holds only
per-model rates that are already published in the catalog.
"""

from __future__ import annotations

import hashlib
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Response

from src.db.inference_jobs import get_price_sheet
from src.services import job_usage_v2
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_HASH = re.compile(r"^0x[0-9a-f]{64}$")

price_sheet_rl = create_endpoint_rate_limit("price_sheet", max_requests=120, window_seconds=60)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "message": "No such price sheet.",
                "type": "invalid_request_error",
                "code": "price_sheet_not_found",
            }
        },
    )


@router.get("/price-sheets/{sheet_hash}", tags=["price-sheets"])
async def get_price_sheet_by_hash(
    sheet_hash: str,
    _rl: None = Depends(price_sheet_rl),
) -> Response:
    sheet_hash = sheet_hash.lower()
    if not job_usage_v2.enabled() or not _HASH.match(sheet_hash):
        raise _not_found()
    body = get_price_sheet(sheet_hash)
    if body is None:
        raise _not_found()
    raw = body.encode()
    # The DB enforces this; checked again so a served sheet can never fail a
    # validator's hash check without us hearing about it first.
    if "0x" + hashlib.sha256(raw).hexdigest() != sheet_hash:
        logger.error("price sheet %s does not hash to its key", sheet_hash)
        raise HTTPException(status_code=500, detail="price sheet integrity check failed")
    return Response(
        content=raw,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
