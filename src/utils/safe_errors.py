"""Generic client-facing errors that never echo raw exception text.

Exception messages from the database, Stripe or an upstream provider can carry
hostnames, SQL, ids or key fragments. Routes log the detail server-side with
the request id and return a fixed message that tells the client which id to
quote to support.
"""

import logging
import uuid

from src.middleware.request_id_middleware import current_request_id
from src.utils.security_validators import sanitize_for_logging

_default_logger = logging.getLogger(__name__)


def log_internal_error(
    what: str, exc: BaseException, log: logging.Logger | None = None
) -> tuple[str, str]:
    """Log ``exc`` with the request id; return ``(public_message, request_id)``.

    ``what`` is a short, fixed description such as "Failed to create checkout
    session". It must not contain user input or exception text.
    """
    request_id = current_request_id() or f"req_{uuid.uuid4().hex[:12]}"
    (log or _default_logger).error(
        "%s [request_id=%s]: %s: %s",
        what,
        request_id,
        type(exc).__name__,
        sanitize_for_logging(str(exc)),
        exc_info=exc,
    )
    return public_error_message(what, request_id), request_id


def public_error_message(what: str, request_id: str) -> str:
    """Fixed client-facing text for an internal failure."""
    return f"{what}. Quote request id {request_id} to support."
