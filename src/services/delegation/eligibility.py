"""Who may take part in delegated staking: the US / Canada block.

Counsel cleared the program everywhere except the US and Canada (2026-10-14;
Config.DELEGATION_RESTRICTED_COUNTRIES). Two checks:

* **Self-attestation.** Before an account starts delegating (links a Cardano
  stake address, or records it through POST /delegation/eligibility before
  staking ETH into the vault) it must send ``not_us_ca_person: true``: "I am
  not a resident or citizen of the United States or Canada". The attestation
  is stored (delegation_eligibility) and audited with the request's IP.
* **Request country**, only when Config.DELEGATION_COUNTRY_HEADER names a
  header a trusted edge always sets. api.gatewayz.ai has no such edge today
  (Railway sets no country header), so by default this check is off. A
  restricted country marks the account restricted for good.

Rewards (v2 accruals and v3 discount attributions) are granted only to an
eligible account -- rewards._payee_verified calls account_eligible -- and
whatever is not granted stays pending. Nothing here ever blocks an exit, a
read of an existing position, or spending a discount balance already granted.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from src.config.config import Config
from src.db.audit import record_audit
from src.db.delegation import get_eligibility, mark_restricted, record_attestation

logger = logging.getLogger(__name__)

# Bump when the attestation wording changes; stored with each attestation.
ATTESTATION_VERSION = "2026-10-14:not-us-ca-person"
ATTESTATION_TEXT = "I am not a resident or citizen of the United States or Canada."

# 451 Unavailable For Legal Reasons, one status for both refusals so the
# frontend has a single case to show; error.type says which.
REGION_RESTRICTED = "region_restricted"
ATTESTATION_REQUIRED = "attestation_required"

_MESSAGES = {
    REGION_RESTRICTED: "Delegated staking is not available in your region.",
    ATTESTATION_REQUIRED: "Confirm you are not a resident or citizen of the United States or "
    "Canada to continue.",
}


def _refuse(code: str) -> HTTPException:
    # Already in the {"error": ...} shape, so the global HTTPException handler
    # (src/utils/error_handlers.py) returns it as-is instead of mapping an
    # unknown 451 to a generic internal error.
    return HTTPException(
        status_code=451,
        detail={
            "error": {
                "type": code,
                "code": code.upper(),
                "message": _MESSAGES[code],
                "status": 451,
            }
        },
    )


def request_country(request: Any) -> str | None:
    """ISO alpha-2 country from the configured trusted header, or None (no
    header configured, header missing, or not a 2-letter code)."""
    header = Config.DELEGATION_COUNTRY_HEADER
    if not header or request is None:
        return None
    try:
        value = (request.headers.get(header) or "").strip().upper()
    except Exception:  # noqa: BLE001 - an unreadable header is "unknown"
        return None
    return value if len(value) == 2 and value.isalpha() else None


def is_restricted_country(country: str | None) -> bool:
    return bool(country) and country in Config.DELEGATION_RESTRICTED_COUNTRIES


def account_eligible(user_id: int) -> bool:
    """True only when the account has attested and is not restricted. A failed
    lookup is False -- never grant on doubt."""
    row = get_eligibility(user_id)
    return bool(row) and row.get("status") == "eligible" and bool(row.get("attested_at"))


def eligibility_view(user_id: int) -> dict[str, Any]:
    """For GET /delegation/rewards: status is eligible | restricted | none
    (none also when the lookup failed)."""
    row = get_eligibility(user_id) or {}
    status = row.get("status")
    return {
        "status": status if status in ("eligible", "restricted") else "none",
        "attested_at": row.get("attested_at"),
        "attestation_text": ATTESTATION_TEXT,
    }


def require_eligible(user_id: int, request: Any, attested: bool) -> None:
    """Gate for STARTING to delegate. Raises 451 region_restricted when the
    request comes from (or the account was marked in) a restricted country,
    451 attestation_required when the account has not attested and this
    request does not either, 503 when eligibility cannot be read or stored.
    With ``attested`` the attestation is recorded (and audited)."""
    country = request_country(request)
    if is_restricted_country(country):
        mark_restricted(user_id, country, "request_country")
        record_audit(
            {"id": user_id},
            action="delegation.eligibility.restricted",
            target_type="user",
            target_id=user_id,
            request=request,
            metadata={"country": country, "source": "request_country"},
        )
        raise _refuse(REGION_RESTRICTED)

    row = get_eligibility(user_id)
    if row is None:
        raise HTTPException(status_code=503, detail="eligibility_unavailable")
    if row.get("status") == "restricted":
        raise _refuse(REGION_RESTRICTED)

    if attested:
        if (
            row.get("status") == "eligible"
            and row.get("attestation_version") == ATTESTATION_VERSION
        ):
            return
        saved = record_attestation(user_id, ATTESTATION_VERSION, country)
        if saved is None:
            raise HTTPException(status_code=503, detail="eligibility_unavailable")
        if saved.get("status") == "restricted":
            raise _refuse(REGION_RESTRICTED)
        record_audit(
            {"id": user_id},
            action="delegation.eligibility.attested",
            target_type="user",
            target_id=user_id,
            request=request,
            metadata={"attestation_version": ATTESTATION_VERSION, "country": country},
        )
        return

    if row.get("status") != "eligible" or not row.get("attested_at"):
        raise _refuse(ATTESTATION_REQUIRED)
