"""
Railway terminates TLS and forwards plain HTTP. Without --proxy-headers uvicorn
reports the scheme as http, so every URL the app builds is http:// — production
answered https://api.gatewayz.ai/v1/status with
`307 -> http://api.gatewayz.ai/v1/status/`, downgrading the connection.

start.sh is the production entrypoint (Railway/Docker) and is not import-testable,
so assert on the command line itself, and prove the behaviour it fixes against a
real ASGI app below.
"""

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

START_SH = Path(__file__).resolve().parents[2] / "start.sh"


def _uvicorn_command() -> str:
    text = START_SH.read_text()
    match = re.search(r"exec uvicorn .*?(?=\n\n|\Z)", text, re.S)
    assert match, "start.sh no longer execs uvicorn"
    return match.group(0)


def test_start_sh_passes_proxy_headers():
    command = _uvicorn_command()
    assert "--proxy-headers" in command
    assert "--forwarded-allow-ips" in command


@pytest.fixture
def app():
    inner = FastAPI()

    @inner.get("/thing/")
    def thing():  # pragma: no cover - the redirect is what matters
        return {"ok": True}

    return inner


# Production shape: the client spoke https to Railway's edge, which forwards
# plain HTTP to the container and records the original scheme in the header.
# base_url is http:// because that is what the app's socket actually sees.
FORWARDED = {"X-Forwarded-Proto": "https"}


def test_without_proxy_headers_the_redirect_downgrades_to_http(app):
    """The production bug, reproduced: the header is ignored and https is lost."""
    client = TestClient(app, base_url="http://api.example.com")
    response = client.get("/thing", headers=FORWARDED, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "http://api.example.com/thing/"


def test_with_proxy_headers_the_redirect_stays_https(app):
    """What start.sh now runs: X-Forwarded-Proto is honoured, so https is kept."""
    client = TestClient(
        ProxyHeadersMiddleware(app, trusted_hosts="*"), base_url="http://api.example.com"
    )
    response = client.get("/thing", headers=FORWARDED, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://api.example.com/thing/"
