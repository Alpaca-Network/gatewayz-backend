"""Public health endpoints must not return raw exception or credential details.

/health/google-vertex is unauthenticated; it used to return the exception text,
the credentials file path and the service-account email from the diagnosis.
"""

import asyncio
from unittest.mock import patch

from src.routes import health

SECRET_TEXT = "invalid_grant for svc@my-project.iam.gserviceaccount.com at /etc/creds.json"


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


def test_google_vertex_exception_is_not_returned():
    with patch(
        "src.services.providers.google_vertex_client.diagnose_google_vertex_credentials",
        side_effect=RuntimeError(SECRET_TEXT),
    ):
        result = _run(health.check_google_vertex_health())

    assert result["status"] == "unhealthy"
    assert SECRET_TEXT not in str(result)
    assert result["request_id"] in result["error"]


def test_google_vertex_diagnosis_is_redacted():
    diagnosis = {
        "credentials_available": True,
        "credential_source": "file",
        "project_id": "my-project",
        "location": "us-central1",
        "initialization_successful": False,
        "error": SECRET_TEXT,
        "health_status": "unhealthy",
        "steps": [
            {"step": "Credential check", "passed": True, "details": "/etc/creds.json"},
            {"step": "Vertex AI initialization", "passed": False, "details": SECRET_TEXT},
        ],
    }
    with patch(
        "src.services.providers.google_vertex_client.diagnose_google_vertex_credentials",
        return_value=diagnosis,
    ):
        result = _run(health.check_google_vertex_health())

    text = str(result)
    assert SECRET_TEXT not in text
    assert "/etc/creds.json" not in text
    assert "gserviceaccount" not in text
    public = result["diagnosis"]
    assert public["credential_source"] == "file"
    assert public["initialization_successful"] is False
    assert public["steps"] == [
        {"step": "Credential check", "passed": True},
        {"step": "Vertex AI initialization", "passed": False},
    ]
    assert public["request_id"] in public["error"]


def test_google_vertex_healthy_diagnosis_has_no_error():
    diagnosis = {
        "credentials_available": True,
        "credential_source": "env_json",
        "initialization_successful": True,
        "error": None,
        "health_status": "healthy",
        "steps": [{"step": "Configuration check", "passed": True, "details": "Project ID: p"}],
    }
    with patch(
        "src.services.providers.google_vertex_client.diagnose_google_vertex_credentials",
        return_value=diagnosis,
    ):
        result = _run(health.check_google_vertex_health())

    assert result["status"] == "healthy"
    assert result["diagnosis"]["error"] is None
    assert "request_id" not in result["diagnosis"]
