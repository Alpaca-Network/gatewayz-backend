"""A signed usage export a third party can verify offline.

The partner plan asks for "a signed, verifiable usage feed the transparency log
can fold in". A signature is only worth anything if the consumer can check it
without trusting us, so these tests run the SAME verification a consumer would
-- including the two ways a signed feed is usually broken in practice:

  1. the signature covers a re-serialization rather than the served bytes, so
     verification silently passes on a document nobody served;
  2. no key is configured and the endpoint serves an unsigned body anyway, so
     the consumer's verify step quietly becomes a no-op.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routes import api_keys as api_keys_route
from src.services.usage_signing import ENV_KEY, verify

ROWS = [{"tag": "init/abc", "calls": 2, "failed": 1, "input_tokens": 9, "output_tokens": 3}]


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv(ENV_KEY, base64.b64encode(b"k" * 32).decode())


def _client():
    app = FastAPI()
    app.include_router(api_keys_route.router)
    app.dependency_overrides[api_keys_route.get_api_key] = lambda: "gw_test"
    return TestClient(app)


@pytest.fixture(autouse=True)
def _user(monkeypatch):
    monkeypatch.setattr(api_keys_route, "get_user", lambda _k: {"id": 1})


def test_the_served_bytes_are_what_was_signed(monkeypatch):
    # The property that matters. Verification runs against the RAW body, never
    # a re-parse -- if the route re-serialized the payload, this fails.
    monkeypatch.setattr(api_keys_route, "get_usage_by_tag", lambda *a, **k: ROWS)
    c = _client()
    r = c.get("/v1/usage/export?tag=init/abc")
    assert r.status_code == 200
    pub = c.get("/v1/usage/export/key").json()["public_key"]
    assert verify(r.content, r.headers["x-gatewayz-signature"], pub)


def test_a_tampered_body_fails_verification(monkeypatch):
    monkeypatch.setattr(api_keys_route, "get_usage_by_tag", lambda *a, **k: ROWS)
    c = _client()
    r = c.get("/v1/usage/export")
    pub = c.get("/v1/usage/export/key").json()["public_key"]
    tampered = r.content.replace(b'"calls":2', b'"calls":9999')
    assert tampered != r.content
    assert not verify(tampered, r.headers["x-gatewayz-signature"], pub)


def test_the_body_is_valid_json_and_carries_the_rollup(monkeypatch):
    monkeypatch.setattr(api_keys_route, "get_usage_by_tag", lambda *a, **k: ROWS)
    body = json.loads(_client().get("/v1/usage/export").content)
    assert body["usage_export"] == "1"
    assert body["issuer"] == "gatewayz.ai"
    assert body["data"] == ROWS
    assert body["measured"] is True


def test_failures_survive_into_the_signed_record(monkeypatch):
    # A failed call cost real compute; the signed feed must carry it rather
    # than a total that quietly excludes it.
    monkeypatch.setattr(api_keys_route, "get_usage_by_tag", lambda *a, **k: ROWS)
    body = json.loads(_client().get("/v1/usage/export").content)
    assert body["data"][0]["failed"] == 1


def test_no_key_configured_is_503_not_an_unsigned_body(monkeypatch):
    # The failure that makes a signed feed worthless: serving an unsigned body
    # that looks signed turns the consumer's check into a no-op.
    monkeypatch.delenv(ENV_KEY, raising=False)
    monkeypatch.setattr(api_keys_route, "get_usage_by_tag", lambda *a, **k: ROWS)
    assert _client().get("/v1/usage/export").status_code == 503


def test_the_public_key_needs_no_credential(monkeypatch):
    # A key you need our credential to fetch cannot be used by the third party
    # the signature exists for.
    r = _client().get("/v1/usage/export/key")
    assert r.status_code == 200
    assert len(base64.b64decode(r.json()["public_key"])) == 32


def test_a_broken_rollup_is_503_not_a_signed_empty_set(monkeypatch):
    monkeypatch.setattr(api_keys_route, "get_usage_by_tag", lambda *a, **k: None)
    assert _client().get("/v1/usage/export").status_code == 503
