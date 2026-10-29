"""DB access for outbound_webhooks (migration 20261005020000)."""

from __future__ import annotations

from datetime import UTC, datetime

from src.config.supabase_config import get_supabase_client

_T = "outbound_webhooks"
_PUBLIC = "id,url,events,active,failure_count,last_status,last_attempt_at,created_at"


def create_hook(user_id: int, url: str, events: list[str], secret_enc: str, key_version) -> dict:
    r = (
        get_supabase_client()
        .table(_T)
        .insert(
            {
                "user_id": user_id,
                "url": url,
                "events": events,
                "secret_enc": secret_enc,
                "key_version": key_version,
            }
        )
        .execute()
    )
    if not r.data:
        raise RuntimeError("outbound_webhooks insert returned no row")
    return {k: r.data[0].get(k) for k in _PUBLIC.split(",")}


def list_hooks(user_id: int) -> list[dict]:
    r = (
        get_supabase_client()
        .table(_T)
        .select(_PUBLIC)
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return r.data or []


def delete_hook(user_id: int, hook_id: str) -> bool:
    r = get_supabase_client().table(_T).delete().eq("id", hook_id).eq("user_id", user_id).execute()
    return bool(r.data)


def hooks_for(user_id: int, event_type: str) -> list[dict]:
    r = (
        get_supabase_client()
        .table(_T)
        .select("*")
        .eq("user_id", user_id)
        .eq("active", True)
        .contains("events", [event_type])
        .execute()
    )
    return r.data or []


def record_attempt(hook: dict, ok: bool, status: int | None, max_failures: int) -> None:
    failures = 0 if ok else int(hook.get("failure_count") or 0) + 1
    update = {
        "failure_count": failures,
        "last_status": status,
        "last_attempt_at": datetime.now(UTC).isoformat(),
    }
    if failures >= max_failures:
        update["active"] = False
    get_supabase_client().table(_T).update(update).eq("id", hook["id"]).execute()
