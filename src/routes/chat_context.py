"""Conversation-context injection for the chat route (Gatewayz One Context Injector).

Route-level helper that prepends stored thread history to the incoming messages
for an authenticated session. Extracted verbatim from the chat_completions
handler (Phase 0d). The budgeted assembly logic lives in
``src/services/context_assembly.py`` (Phase 4); this is the current
chat-history prepend the request path uses today.
"""

from __future__ import annotations

import logging

from src.db.chat_history import get_chat_session
from src.routes.chat_helpers import _to_thread
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

# PostgreSQL 32-bit signed integer range for session_id validation.
_PG_INT_MIN = -2147483648
_PG_INT_MAX = 2147483647


async def inject_conversation_history(
    session_id: int | None,
    is_anonymous: bool,
    user: dict | None,
    messages: list[dict],
) -> tuple[list[dict], int | None]:
    """Prepend stored thread history to ``messages`` for an authenticated session.

    Returns ``(messages, session_id)``. ``session_id`` is returned as ``None`` when
    it is out of PostgreSQL integer range (so downstream persistence skips it).
    History is authenticated-only; for anonymous requests the inputs are returned
    unchanged. A history-fetch failure is logged and swallowed (non-fatal).
    """
    if not session_id:
        return messages, session_id

    if is_anonymous:
        logger.debug("Ignoring session_id for anonymous request")
        return messages, session_id

    # Validate session_id is within valid PostgreSQL integer range
    if session_id < _PG_INT_MIN or session_id > _PG_INT_MAX:
        logger.warning(
            "Invalid session_id %s: out of PostgreSQL integer range. Ignoring session history.",
            sanitize_for_logging(str(session_id)),
        )
        return messages, None

    try:
        # Fetch the session with its message history
        session = await _to_thread(get_chat_session, session_id, user["id"])

        if session and session.get("messages"):
            # Transform DB messages to OpenAI format and prepend to current messages
            history_messages = [
                {"role": msg["role"], "content": msg["content"]} for msg in session["messages"]
            ]
            messages = history_messages + messages
            logger.info(
                "Injected %d messages from session %s",
                len(history_messages),
                sanitize_for_logging(str(session_id)),
            )
        else:
            logger.debug(
                "No history found for session %s or session doesn't exist",
                sanitize_for_logging(str(session_id)),
            )
    except Exception as e:
        # Don't fail the request if history fetch fails
        logger.warning(
            "Failed to fetch chat history for session %s: %s",
            sanitize_for_logging(str(session_id)),
            sanitize_for_logging(str(e)),
        )

    return messages, session_id
