"""Server-minted billing correlation ref (threat model L7/G4).

Every billable route must key its credit deduction/refund on
``request.state.billing_ref`` — minted by RequestIDMiddleware independently of
any client input — so that a retried or duplicated request cannot double-charge
(``deduct_credits`` skips the deduction when a transaction with that
``request_id`` already exists) and so that billing rows are never correlatable
to a value the client chose (the client-settable X-Request-ID is only ever
echoed back for the client's own tracing).

Shared by chat, audio and any other route that charges credits; see
docs/security/ANONYMITY_THREAT_MODEL.md.
"""

from __future__ import annotations

import uuid

from fastapi import Request


def resolve_billing_ref(request: Request | None) -> str:
    """Resolve the server-minted billing correlation ref for this request.

    Reads request.state.billing_ref, set by RequestIDMiddleware independently of
    any client-supplied header (threat model L7/G4: the client-settable
    X-Request-ID must never be the join key between billing rows and a
    request). Falls back to a fresh UUID only when no request/middleware state
    is available (e.g. a handler invoked outside the normal middleware stack in
    a test), so billing never silently lacks an idempotency key.
    """
    state = getattr(request, "state", None) if request is not None else None
    billing_ref = getattr(state, "billing_ref", None) if state is not None else None
    return billing_ref or str(uuid.uuid4())
