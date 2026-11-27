"""Admin routes must not echo raw exception text to the client.

Exception messages from the database or an upstream service can carry
hostnames, SQL, ids or key fragments. The admin routes log the detail with the
request id (src/utils/safe_errors.py) and return a fixed message instead.
"""

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.routes import admin
from src.schemas import SetRateLimitRequest
from src.schemas.users import UserRegistrationRequest

SECRET_TEXT = 'relation "users" violates policy at db.internal.example:5432 key=sk-live-123'
ADMIN = {"id": 1, "email": "admin@example.com", "role": "admin"}


def _run(coro):
    """Run a coroutine on a private loop.

    Not asyncio.run(): that unsets the thread's current event loop on exit,
    which breaks later tests in the same worker that call get_event_loop().
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _assert_generic(exc_info, status_code: int, what: str):
    assert exc_info.value.status_code == status_code
    detail = exc_info.value.detail
    assert SECRET_TEXT not in detail
    assert detail.startswith(f"{what}. Quote request id ")


def test_debug_models_error_is_generic():
    with patch.object(admin, "get_cached_models", side_effect=RuntimeError(SECRET_TEXT)):
        with pytest.raises(HTTPException) as exc_info:
            _run(admin.admin_debug_models(admin_user=ADMIN))

    _assert_generic(exc_info, 500, "Failed to debug")


def test_request_counts_error_is_generic():
    with patch("src.db.client.get_db", side_effect=RuntimeError(SECRET_TEXT)):
        with pytest.raises(HTTPException) as exc_info:
            _run(admin.get_request_counts_by_model_admin(admin_user=ADMIN))

    _assert_generic(exc_info, 500, "Failed to get request counts")


def test_create_api_key_value_error_is_generic():
    req = UserRegistrationRequest(username="alice", email="alice@example.com")
    with patch.object(admin, "create_enhanced_user", side_effect=ValueError(SECRET_TEXT)):
        with pytest.raises(HTTPException) as exc_info:
            _run(admin.create_api_key(req))

    _assert_generic(exc_info, 400, "Failed to create API key")


def test_set_rate_limit_value_error_is_generic():
    req = SetRateLimitRequest(api_key="gw_live_abc", rate_limits={})
    with patch.object(admin, "set_user_rate_limits", side_effect=ValueError(SECRET_TEXT)):
        with pytest.raises(HTTPException) as exc_info:
            _run(admin.admin_set_rate_limit(req, admin_user=ADMIN))

    _assert_generic(exc_info, 400, "Failed to set rate limits")


def test_delete_by_domain_failure_entries_are_generic():
    users = [{"id": 7, "email": "a@spam.example", "username": "a", "created_at": None}]
    client = MagicMock()
    client.table.return_value.select.return_value.ilike.return_value.execute.return_value = (
        MagicMock(data=users)
    )
    client.table.return_value.delete.return_value.eq.return_value.execute.side_effect = (
        RuntimeError(SECRET_TEXT)
    )
    with patch("src.db.client.get_db", return_value=client):
        result = _run(admin.delete_users_by_domain("spam.example", dry_run=False, admin_user=ADMIN))

    assert result["count"] == 0
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == 7
    assert SECRET_TEXT not in str(result)
    assert result["failed"][0]["error"].startswith("Failed to delete user. Quote request id ")


def test_no_admin_route_returns_exception_text():
    """Static guard: no HTTPException detail built from the caught exception."""
    pattern = re.compile(
        r"detail=(?:str\((?:e|exc|err)\)|f[\"'][^\"']*\{(?:str\()?(?:e|exc|err)\b)"
    )
    routes = Path(admin.__file__).parent
    offenders = []
    for path in sorted(routes.glob("admin*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == []
