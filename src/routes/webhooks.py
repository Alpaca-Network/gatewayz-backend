"""Outbound webhooks: subscribe to job and Verify case state changes.

  POST   /v1/webhooks       {url, events}  -> the signing secret, shown ONCE
  GET    /v1/webhooks
  DELETE /v1/webhooks/{id}

Events: job.closed, verify.case.updated. Signature scheme in
src/services/outbound_webhooks.py (HMAC-SHA256 over "<t>.<raw body>").
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from src.db.outbound_webhooks import create_hook, delete_hook, list_hooks
from src.routes.jobs import job_owner_id
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit
from src.services.outbound_webhooks import EVENTS, new_secret
from src.services.webhook_target import InvalidWebhookTarget, validate_webhook_url
from src.utils.crypto import encrypt_api_key

router = APIRouter()
MAX_HOOKS = 10
webhooks_rl = create_endpoint_rate_limit("webhooks", max_requests=20, window_seconds=60)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"message": message, "type": "invalid_request_error", "code": code}},
    )


class WebhookRequest(BaseModel):
    url: str = Field(..., max_length=2048)
    events: list[str] = Field(..., min_length=1)


@router.post("/webhooks", tags=["webhooks"], status_code=201)
async def create_webhook(
    body: WebhookRequest,
    user_id: int = Depends(job_owner_id),
    _rl: None = Depends(webhooks_rl),
) -> dict[str, Any]:
    unknown = sorted(set(body.events) - EVENTS)
    if unknown:
        raise _error(422, "unknown_event", f"unknown events {unknown}; one of {sorted(EVENTS)}")
    try:
        validate_webhook_url(body.url)
    except InvalidWebhookTarget as e:
        raise _error(422, "invalid_webhook_url", str(e)) from e
    if len(list_hooks(user_id)) >= MAX_HOOKS:
        raise _error(409, "too_many_webhooks", f"At most {MAX_HOOKS} webhooks per account.")
    secret = new_secret()
    enc, version = encrypt_api_key(secret)
    hook = create_hook(user_id, body.url, sorted(set(body.events)), enc, version)
    return {**hook, "secret": secret}


@router.get("/webhooks", tags=["webhooks"])
async def get_webhooks(user_id: int = Depends(job_owner_id)) -> dict[str, Any]:
    return {"webhooks": list_hooks(user_id)}


@router.delete("/webhooks/{hook_id}", tags=["webhooks"], status_code=204, response_class=Response)
async def remove_webhook(hook_id: str, user_id: int = Depends(job_owner_id)) -> Response:
    if not delete_hook(user_id, hook_id):
        raise _error(404, "webhook_not_found", "No such webhook.")
    return Response(status_code=204)
