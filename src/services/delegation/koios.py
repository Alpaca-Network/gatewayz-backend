"""Read-only Koios client for the Gatewayz Cardano pool.

Three calls (endpoint shapes from Koios' published OpenAPI spec,
api.koios.rest/koiosapi.yaml):

* ``GET /tip`` -> ``[{epoch_no, ...}]`` -- the current epoch;
* ``GET /pool_delegators?_pool_bech32=`` -> ``[{stake_address, amount
  (lovelace, string), active_epoch_no, latest_delegation_tx_hash}]`` -- live
  delegators, paginated PostgREST-style with ``offset``/``limit`` (Koios
  caps a page at 1000 rows);
* ``GET /pool_history?_pool_bech32=`` -> ``[{epoch_no, pool_fees (lovelace,
  string), deleg_rewards, ...}]`` -- per-epoch operator fees, which is our
  revenue.

KOIOS_API_KEY (optional) goes in the Authorization header, never in a URL,
and errors are described by class and HTTP status only -- never ``str(exc)``
-- so neither the key nor a query string reaches a log line.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.config.config import Config

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15
PAGE_SIZE = 1000
_MAX_PAGES = 500


class KoiosError(Exception):
    """A Koios read failed. The message is safe to log."""


def _describe(exc: BaseException) -> str:
    parts = [type(exc).__name__]
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    return " ".join(parts)


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    key = (Config.KOIOS_API_KEY or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{Config.KOIOS_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = requests.get(
            url, params=params or {}, headers=_headers(), timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        raise KoiosError(f"Koios {path} failed: {_describe(exc)}") from None


def get_tip_epoch() -> int:
    rows = _get("tip")
    if not isinstance(rows, list) or not rows or "epoch_no" not in rows[0]:
        raise KoiosError("Koios tip: unexpected payload")
    return int(rows[0]["epoch_no"])


def list_pool_delegators(pool_id: str) -> list[dict[str, Any]]:
    """Every live delegator of `pool_id`, all pages. Raises KoiosError if any
    page fails: a partial delegator list would value some delegators at zero."""
    delegators: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        rows = _get(
            "pool_delegators",
            {
                "_pool_bech32": pool_id,
                "offset": page * PAGE_SIZE,
                "limit": PAGE_SIZE,
                "order": "stake_address.asc",
            },
        )
        if not isinstance(rows, list):
            raise KoiosError("Koios pool_delegators: unexpected payload")
        delegators.extend(rows)
        if len(rows) < PAGE_SIZE:
            return delegators
    raise KoiosError("Koios pool_delegators: page limit exceeded")


def get_pool_history(pool_id: str) -> list[dict[str, Any]]:
    rows = _get("pool_history", {"_pool_bech32": pool_id})
    if not isinstance(rows, list):
        raise KoiosError("Koios pool_history: unexpected payload")
    return rows
