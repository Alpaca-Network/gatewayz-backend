import json
from urllib.parse import urlparse

import pytest
import requests
from requests import Response


def _make_response(status_code: int, data=None) -> Response:
    resp = Response()
    resp.status_code = status_code
    resp.url = ""
    if data is not None:
        resp._content = json.dumps(data).encode("utf-8")
        resp.headers["Content-Type"] = "application/json"
    else:
        resp._content = b""
    return resp


@pytest.fixture(autouse=True)
def _mock_smoke_requests(monkeypatch, request):
    """
    Provide predictable responses for smoke tests without requiring a live server.

    Only applies to tests marked with pytest.mark.smoke.
    """
    if "smoke" not in request.keywords:
        return

    def fake_get(url, *args, **kwargs):
        path = urlparse(url).path
        if path == "/health":
            return _make_response(200, {"status": "ok"})
        if path == "/":
            return _make_response(200, {"message": "Gateway API"})
        if path == "/catalog/models":
            return _make_response(200, [])
        if path == "/catalog/providers":
            return _make_response(200, [])
        # Default: pretend endpoint requires authentication
        return _make_response(401, {"detail": "Unauthorized"})

    def fake_post(url, *args, **kwargs):
        path = urlparse(url).path
        if path in ("/v1/chat/completions", "/v1/messages", "/v1/images/generations"):
            return _make_response(401, {"detail": "Unauthorized"})
        # Unknown POST endpoint -> 404
        return _make_response(404, {"detail": "Not Found"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)


@pytest.fixture
def supabase_client():
    pytest.skip("Supabase client is not configured in this test environment")


@pytest.fixture
def test_prefix():
    return "test"
