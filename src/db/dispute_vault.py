"""DB access for the dispute content vault (migration 20261027000000).

Rows hold ciphertext only; encryption and decryption happen in
services/dispute_vault.py with a key the database never sees.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.config.supabase_config import get_supabase_client

_CONTENT = "dispute_content"
_REVEALS = "dispute_reveals"


def _hex(b: bytes) -> str:
    # PostgREST takes bytea as the Postgres hex form.
    return "\\x" + b.hex()


def _bytes(value: Any) -> bytes:
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    s = str(value)
    return bytes.fromhex(s[2:] if s.startswith("\\x") else s)


def insert_content(
    request_id: str, job_id: str, content_hash: str, key_id: str, nonce: bytes, ciphertext: bytes
) -> None:
    get_supabase_client().table(_CONTENT).upsert(
        {
            "request_id": request_id,
            "job_id": job_id,
            "content_hash": content_hash,
            "key_id": key_id,
            "nonce": _hex(nonce),
            "ciphertext": _hex(ciphertext),
        },
        on_conflict="request_id",
        ignore_duplicates=True,
    ).execute()


def get_content(request_id: str) -> dict | None:
    result = (
        get_supabase_client()
        .table(_CONTENT)
        .select("request_id, job_id, content_hash, key_id, nonce, ciphertext")
        .eq("request_id", request_id)
        .execute()
    )
    if not result.data:
        return None
    row = dict(result.data[0])
    row["nonce"], row["ciphertext"] = _bytes(row["nonce"]), _bytes(row["ciphertext"])
    return row


def content_expiry(job_id: str) -> datetime | None:
    """When the job's dispute window ends (72 h after close, or after its deadline)."""
    result = get_supabase_client().rpc("dispute_content_expiry", {"p_job_id": job_id}).execute()
    return datetime.fromisoformat(result.data.replace("Z", "+00:00")) if result.data else None


def insert_reveal(token_hash: str, request_id: str, expires_at: datetime) -> None:
    get_supabase_client().table(_REVEALS).insert(
        {"token_hash": token_hash, "request_id": request_id, "expires_at": expires_at.isoformat()}
    ).execute()


def get_reveal(token_hash: str) -> dict | None:
    result = (
        get_supabase_client()
        .table(_REVEALS)
        .select("request_id, expires_at")
        .eq("token_hash", token_hash)
        .execute()
    )
    return result.data[0] if result.data else None


def purge() -> dict:
    return get_supabase_client().rpc("purge_dispute_content", {}).execute().data or {}
