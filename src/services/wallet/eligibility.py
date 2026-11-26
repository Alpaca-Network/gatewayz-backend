"""Who may link a Cardano wallet: the US / Canada block.

Counsel cleared the program everywhere except the US and Canada (2026-10-14;
Config.WALLET_LINK_RESTRICTED_COUNTRIES); the Gatewayz vault has the same
restriction. Two checks:

* **Self-attestation.** Before an account links a Cardano stake address (or
  through POST /wallet/eligibility) it must send ``not_us_ca_person: true``:
  "I am not a resident or citizen of the United States or Canada". The
  attestation is stored (table delegation_eligibility -- historical name) and
  audited with the request's IP.
* **Request country**, from the first of:

  1. Config.WALLET_LINK_COUNTRY_HEADER, when it names a header a trusted
     edge always sets. api.gatewayz.ai has no such edge today (Railway sets
     no country header), so by default this source is off.
  2. The country of the trusted client IP
     (src.services.auth_rate_limiting.get_client_ip) in the local database
     named by Config.WALLET_LINK_IP_COUNTRY_DB_PATH
     (src/services/wallet/ip_country.py; docs/wallet/ip-country.md). Off
     when the path is empty.

  A restricted country marks the account restricted for good. No country
  (both sources off, the database unreadable, or the IP unknown) leaves the
  attestation as the only check. A VPN defeats the IP source, which is why
  the attestation stays.

Nothing here ever blocks a read, removing a link, or spending discount
credits already granted.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from src.config.config import Config
from src.db.audit import record_audit
from src.db.wallet_eligibility import get_eligibility, mark_restricted, record_attestation
from src.services.wallet.ip_country import country_for_ip, ip_country_enabled

logger = logging.getLogger(__name__)

# Bump when the attestation wording changes; stored with each attestation.
ATTESTATION_VERSION = "2026-10-14:not-us-ca-person"
ATTESTATION_TEXT = "I am not a resident or citizen of the United States or Canada."

# 451 Unavailable For Legal Reasons, one status for both refusals so the
# frontend has a single case to show; error.type says which.
REGION_RESTRICTED = "region_restricted"
ATTESTATION_REQUIRED = "attestation_required"

# Where the request country came from, recorded in the audit metadata.
SOURCE_HEADER = "header"
SOURCE_IP = "ip"
SOURCE_NONE = "none"
# restricted_reason stored on the eligibility row, per source.
_RESTRICTED_REASONS = {SOURCE_HEADER: "request_country", SOURCE_IP: "ip_country"}

_MESSAGES = {
    REGION_RESTRICTED: "This feature is not available in your region.",
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
    header = Config.WALLET_LINK_COUNTRY_HEADER
    if not header or request is None:
        return None
    try:
        value = (request.headers.get(header) or "").strip().upper()
    except Exception:  # noqa: BLE001 - an unreadable header is "unknown"
        return None
    return value if len(value) == 2 and value.isalpha() else None


def _trusted_client_ip(request: Any) -> str | None:
    """The client IP from the one trusted header (the same function every
    client-IP read uses), or None."""
    try:
        from src.services.auth_rate_limiting import get_client_ip

        ip = get_client_ip(request)
    except Exception:  # noqa: BLE001 - an unreadable request is "unknown"
        return None
    return None if not ip or ip == "unknown" else ip


def request_country_and_source(request: Any) -> tuple[str | None, str]:
    """(country, source): the trusted header first, then the IP database.
    source is "header", "ip", or "none" (no country)."""
    country = request_country(request)
    if country:
        return country, SOURCE_HEADER
    if request is not None and ip_country_enabled():
        country = country_for_ip(_trusted_client_ip(request))
        if country:
            return country, SOURCE_IP
    return None, SOURCE_NONE


def is_restricted_country(country: str | None) -> bool:
    return bool(country) and country in Config.WALLET_LINK_RESTRICTED_COUNTRIES


def account_eligible(user_id: int) -> bool:
    """True only when the account has attested and is not restricted. A failed
    lookup is False -- never grant on doubt."""
    row = get_eligibility(user_id)
    return bool(row) and row.get("status") == "eligible" and bool(row.get("attested_at"))


def eligibility_view(user_id: int) -> dict[str, Any]:
    """For POST /wallet/eligibility: status is eligible | restricted | none
    (none also when the lookup failed)."""
    row = get_eligibility(user_id) or {}
    status = row.get("status")
    return {
        "status": status if status in ("eligible", "restricted") else "none",
        "attested_at": row.get("attested_at"),
        "attestation_text": ATTESTATION_TEXT,
    }


def require_eligible(user_id: int, request: Any, attested: bool) -> None:
    """Gate for linking a Cardano wallet. Raises 451 region_restricted when the
    request comes from (or the account was marked in) a restricted country,
    451 attestation_required when the account has not attested and this
    request does not either, 503 when eligibility cannot be read or stored.
    With ``attested`` the attestation is recorded (and audited)."""
    country, source = request_country_and_source(request)
    if is_restricted_country(country):
        mark_restricted(user_id, country, _RESTRICTED_REASONS[source])
        record_audit(
            {"id": user_id},
            action="wallet.eligibility.restricted",
            target_type="user",
            target_id=user_id,
            request=request,
            metadata={
                "country": country,
                "source": _RESTRICTED_REASONS[source],
                "country_source": source,
            },
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
            action="wallet.eligibility.attested",
            target_type="user",
            target_id=user_id,
            request=request,
            metadata={
                "attestation_version": ATTESTATION_VERSION,
                "country": country,
                "country_source": source,
            },
        )
        return

    if row.get("status") != "eligible" or not row.get("attested_at"):
        raise _refuse(ATTESTATION_REQUIRED)
