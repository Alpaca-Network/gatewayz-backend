"""API key purpose: the single switch for validator (no-logging) mode.

A key's ``purpose`` lives in ``api_keys_new.purpose`` (migration
20261005010000_api_key_purpose.sql). ``NULL`` means a general key and is what
every key created before this module existed has; ``'validator'`` marks a key
used by a GenLayer validator (or anyone else who wants the same guarantees):

* **No logging beyond billing.** Only what credit deduction needs is persisted
  — the ``credit_transactions`` debit, the ``usage_records`` row (user, key id,
  model, tokens, cost, timestamp) and the key's request-cap counter. The
  per-request analytics row (``chat_completion_requests``), ``activity_log``,
  chat history, and the per-request IP/user-agent audit line are skipped, so a
  validator key never appears in analytics, rankings or arrivals aggregates.
* **No silent substitution.** The model sent is the model served, or the
  request fails: router aliases are refused, auto web search (which rewrites
  the prompt and sends it to a search vendor) is off, and a provider hop that
  would serve a different model id is refused instead of taken.

Everything is keyed off :func:`is_validator_key`. A general key returns False
at every guard, so its behaviour is byte-identical to before this module.

Two ways a guard can ask:

* With the user dict in hand — ``is_validator_key(user)``.
* From a leaf persistence function that only receives ids —
  ``validator_mode_active()``, a request-scoped ContextVar bound once in
  :func:`src.security.deps.get_api_key` right after the key is resolved to a
  user. ContextVars are copied into ``asyncio.to_thread``, ``create_task`` and
  Starlette's threadpool, so background tasks spawned by the request see it;
  every other request starts from the default (False).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException

PURPOSE_GENERAL = "general"
PURPOSE_VALIDATOR = "validator"
VALID_PURPOSES = (PURPOSE_GENERAL, PURPOSE_VALIDATOR)

# Key under which the resolved user dict carries the key's purpose
# (src/db/users.py::_get_user_uncached). Not "purpose", so it can never collide
# with a column of the users table, which is splatted into the same dict.
USER_FIELD = "key_purpose"

_validator_mode: ContextVar[bool] = ContextVar(  # noqa: B039 - immutable bool default
    "gatewayz_validator_mode", default=False
)


def normalize_purpose(value: str | None) -> str | None:
    """Map caller input to the stored value: None for general, 'validator' otherwise.

    General is stored as NULL so a general key's row is identical to one
    created before the column existed. Raises ValueError on anything else.
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("", PURPOSE_GENERAL):
        return None
    if v == PURPOSE_VALIDATOR:
        return PURPOSE_VALIDATOR
    raise ValueError(f"Invalid purpose '{value}'. Must be one of: {list(VALID_PURPOSES)}")


def is_validator_key(user: dict[str, Any] | None) -> bool:
    """True when the authenticated key is a validator key. The one predicate."""
    return bool(user) and user.get(USER_FIELD) == PURPOSE_VALIDATOR


def bind_request_key_purpose(user: dict[str, Any] | None) -> None:
    """Record, for the rest of this request, whether its key is a validator key."""
    _validator_mode.set(is_validator_key(user))


def validator_mode_active() -> bool:
    """True inside a request authenticated with a validator key."""
    return _validator_mode.get()


def suppress_request_logging(user: dict[str, Any] | None = None) -> bool:
    """Guard for every non-billing per-request write.

    True if either the user dict at hand or the request context says the key
    is a validator key. Checking both means a call site that has the user is
    covered even if it runs outside the request context (e.g. a detached
    thread), and a leaf that only has ids is covered via the context.
    """
    return is_validator_key(user) or validator_mode_active()


# --- No silent substitution ------------------------------------------------

# Superset of src/routes/chat_routing.py's router aliases, plus OpenRouter's
# own auto-router: chat_routing deliberately lets `openrouter/auto` through as
# a passthrough model, and model_transformations remaps it to a fixed fallback
# model on non-OpenRouter providers — a substitution either way.
_ROUTER_ALIASES = frozenset({"auto", "gatewayz/auto", "gatewayz-router", "openrouter/auto"})
_ROUTER_PREFIXES = ("router", "auto:", "gatewayz-general", "gatewayz-code")


def is_routing_alias(model: str | None) -> bool:
    """True for ids that mean "let the gateway (or an upstream router) pick a model"."""
    if not model:
        return False
    m = model.strip().lower()
    return m in _ROUTER_ALIASES or m.startswith(_ROUTER_PREFIXES) or m.endswith("/auto")


def substitution_refused(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": "model_substitution_refused",
            }
        },
    )


def enforce_no_routing_alias(user: dict[str, Any] | None, model: str | None) -> None:
    """Validator keys must name a model; a routing alias is a substitution by design."""
    if is_validator_key(user) and is_routing_alias(model):
        raise substitution_refused(
            f"Model '{model}' asks the gateway to choose a model. Validator keys must "
            f"send an explicit model id (see GET /v1/models)."
        )


def _strip_free(model_id: str) -> str:
    m = model_id.strip().lower()
    return m[: -len(":free")] if m.endswith(":free") else m


def is_same_model(requested: str | None, provider_model_id: str | None) -> bool:
    """True when the id sent upstream names the model the caller asked for.

    Same model = identical id, or identical final path segment once vendor /
    provider namespaces are stripped (``openai/gpt-4o`` -> ``gpt-4o``;
    ``deepseek-ai/deepseek-v3`` -> ``accounts/fireworks/models/deepseek-v3``),
    case-insensitively, ignoring a ``:free`` suffix. Anything else — a
    retired-model redirect (gemini-1.5-pro -> gemini-2.5-flash), a version bump
    in a provider mapping (deepseek-v3 -> deepseek-v3p1), an ``openrouter/auto``
    fallback — is a different model.
    """
    if not requested or not provider_model_id:
        return False
    want = _strip_free(requested)
    sent = _strip_free(provider_model_id)
    return sent == want or sent.rsplit("/", 1)[-1] == want.rsplit("/", 1)[-1]


def enforce_same_model(
    user: dict[str, Any] | None, requested: str | None, provider_model_id: str | None
) -> None:
    """Refuse, for validator keys only, an upstream hop that serves a different model."""
    if is_validator_key(user) and not is_same_model(requested, provider_model_id):
        raise substitution_refused(
            f"Model '{requested}' would be served as '{provider_model_id}', which is a "
            f"different model. Validator keys never receive a substitute."
        )
