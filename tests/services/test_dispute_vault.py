"""Dispute content vault: opt-in, encrypted, served verbatim only inside the window."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from src.services import dispute_vault as dv
from src.services import job_usage_v2 as v2

JOB = "0x" + "ab" * 32
K1 = base64.b64encode(b"\x01" * 32).decode()
K2 = base64.b64encode(b"\x02" * 32).decode()
MESSAGES = [{"role": "user", "content": "Écris un haïku."}]
OUTPUT = "Pluie sur le toit"
BUNDLE = v2.canonical_json({"messages": MESSAGES, "output": OUTPUT})
HASH = v2.sha256_hex(BUNDLE)


class FakeDB:
    def __init__(self):
        self.content: dict[str, dict] = {}
        self.reveals: dict[str, dict] = {}
        self.window_end = datetime.now(UTC) + timedelta(hours=72)

    def insert_content(self, request_id, job_id, content_hash, key_id, nonce, ciphertext):
        self.content.setdefault(
            request_id,
            {
                "request_id": request_id,
                "job_id": job_id,
                "content_hash": content_hash,
                "key_id": key_id,
                "nonce": nonce,
                "ciphertext": ciphertext,
            },
        )

    def get_content(self, request_id):
        return self.content.get(request_id)

    def content_expiry(self, job_id):
        return self.window_end

    def insert_reveal(self, token_hash, request_id, expires_at):
        self.reveals[token_hash] = {"request_id": request_id, "expires_at": expires_at.isoformat()}

    def get_reveal(self, token_hash):
        return self.reveals.get(token_hash)


@pytest.fixture
def vault(monkeypatch):
    from src.config.config import Config
    from src.db import dispute_vault as real

    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "DISPUTE_VAULT_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "DISPUTE_VAULT_KEYS", f"k1:{K1}", raising=False)
    monkeypatch.setattr(Config, "DISPUTE_VAULT_PUBLIC_BASE", "https://api.example", raising=False)
    fake = FakeDB()
    for name in ("insert_content", "get_content", "content_expiry", "insert_reveal", "get_reveal"):
        monkeypatch.setattr(real, name, getattr(fake, name))
    return fake


def _token(out: dict) -> str:
    return out["content_uri"].rsplit("/", 1)[1]


def test_kept_encrypted_and_served_byte_for_byte(vault):
    assert dv.store(JOB, "req-1", MESSAGES, OUTPUT, HASH)
    row = vault.content["req-1"]
    assert BUNDLE not in row["ciphertext"] and b"Pluie" not in row["ciphertext"]
    out = dv.reveal(JOB, "req-1")
    assert out["content_hash"] == HASH and out["content_uri"].startswith(
        "https://api.example/v1/dispute-content/"
    )
    token = _token(out)
    assert token not in str(vault.reveals)  # only sha256(token) is stored
    body = dv.open_reveal(token)
    assert body == BUNDLE and "0x" + hashlib.sha256(body).hexdigest() == HASH


def test_nothing_kept_when_the_bundle_does_not_match_or_is_too_large(vault):
    assert not dv.store(JOB, "req-1", MESSAGES, "different output", HASH)
    big = "x" * (dv.MAX_BUNDLE_BYTES + 1)
    assert not dv.store(JOB, "req-2", MESSAGES, big, v2.content_hash(MESSAGES, big))
    assert not dv.store(JOB, "req-3", MESSAGES, OUTPUT, v2.ZERO_HASH)
    assert vault.content == {}


def test_ciphertext_is_bound_to_its_request(vault):
    dv.store(JOB, "req-1", MESSAGES, OUTPUT, HASH)
    row = vault.content["req-1"]
    with pytest.raises(Exception):
        dv.decrypt("req-2", HASH, row["key_id"], row["nonce"], row["ciphertext"])


def test_rotated_keys_still_decrypt(vault, monkeypatch):
    from src.config.config import Config

    dv.store(JOB, "req-1", MESSAGES, OUTPUT, HASH)
    monkeypatch.setattr(Config, "DISPUTE_VAULT_KEYS", f"k2:{K2},k1:{K1}", raising=False)
    assert dv.store(JOB, "req-2", MESSAGES, OUTPUT, HASH)
    assert vault.content["req-2"]["key_id"] == "k2"
    assert dv.open_reveal(_token(dv.reveal(JOB, "req-1"))) == BUNDLE


def test_no_reveal_for_another_job_or_after_the_window(vault):
    dv.store(JOB, "req-1", MESSAGES, OUTPUT, HASH)
    assert dv.reveal("0x" + "ff" * 32, "req-1") is None
    token = _token(dv.reveal(JOB, "req-1"))
    vault.window_end = datetime.now(UTC) - timedelta(seconds=1)
    assert dv.reveal(JOB, "req-1") is None
    assert dv.open_reveal(token) is None


def test_expired_or_unknown_links_serve_nothing(vault):
    dv.store(JOB, "req-1", MESSAGES, OUTPUT, HASH)
    token = _token(dv.reveal(JOB, "req-1"))
    assert dv.open_reveal("not-a-token") is None
    for rev in vault.reveals.values():
        rev["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert dv.open_reveal(token) is None


def test_tampered_plaintext_is_never_served(vault):
    dv.store(JOB, "req-1", MESSAGES, OUTPUT, HASH)
    token = _token(dv.reveal(JOB, "req-1"))
    row = vault.content["req-1"]
    kid, nonce, ct = dv.encrypt("req-1", HASH, b'{"messages":[],"output":"forged"}')
    row.update(key_id=kid, nonce=nonce, ciphertext=ct)
    with pytest.raises(RuntimeError):
        dv.open_reveal(token)


def test_off_unless_flagged_keyed_and_v2(monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "DISPUTE_VAULT_ENABLED", True, raising=False)
    monkeypatch.setattr(Config, "DISPUTE_VAULT_KEYS", "k1:not-base64!", raising=False)
    assert not dv.enabled()
    monkeypatch.setattr(Config, "DISPUTE_VAULT_KEYS", f"k1:{K1}", raising=False)
    assert dv.enabled()
    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", False, raising=False)
    assert not dv.enabled()


@pytest.mark.parametrize(
    "value,expected", [("on", True), ("TRUE", True), ("1", True), ("off", False), (None, False)]
)
def test_opt_in_header(value, expected):
    headers = {} if value is None else {dv.HEADER: value}
    assert dv.opted_in(SimpleNamespace(headers=headers)) is expected


# --------------------------------------------------------------------------- routes


@pytest.fixture
def client(vault, monkeypatch):
    from src.routes import dispute_content as route
    from src.routes import jobs as jobs_route

    monkeypatch.setattr(route, "_load", lambda job_id, user_id: {"job_id": job_id.lower()})
    monkeypatch.setattr(
        route, "list_usage_v2", lambda job_id: [{"request_id": "req-0"}, {"request_id": "req-1"}]
    )
    app = FastAPI()
    v1 = APIRouter(prefix="/v1")
    v1.include_router(route.router)
    app.include_router(v1)
    app.dependency_overrides[jobs_route.get_current_user] = lambda: {"id": 42, "key_name": "prod"}
    app.dependency_overrides[jobs_route.jobs_read_rl] = lambda: None
    app.dependency_overrides[route.dispute_content_rl] = lambda: None
    return TestClient(app)


def test_reveal_then_public_fetch(client, vault):
    dv.store(JOB, "req-1", MESSAGES, OUTPUT, HASH)
    r = client.post(f"/v1/jobs/{JOB}/usage/1/reveal")
    assert r.status_code == 200, r.text
    assert r.json()["content_hash"] == HASH
    served = client.get("/v1/dispute-content/" + _token(r.json()))
    assert served.status_code == 200 and served.content == BUNDLE
    assert served.headers["cache-control"] == "no-store"


def test_reveal_errors(client, vault):
    assert (
        client.post(f"/v1/jobs/{JOB}/usage/0/reveal").json()["detail"]["error"]["code"]
        == "content_not_kept"
    )
    assert client.post(f"/v1/jobs/{JOB}/usage/5/reveal").status_code == 404
    assert client.get("/v1/dispute-content/nope").status_code == 404


def test_routes_dark_when_disabled(client, monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "DISPUTE_VAULT_ENABLED", False, raising=False)
    r = client.post(f"/v1/jobs/{JOB}/usage/1/reveal")
    assert r.json()["detail"]["error"]["code"] == "dispute_vault_disabled"
    assert client.get("/v1/dispute-content/x").status_code == 404


def test_job_keys_cannot_reveal(client):
    from src.routes import jobs as jobs_route

    client.app.dependency_overrides[jobs_route.get_current_user] = lambda: {
        "id": 42,
        "key_name": f"job:{JOB}",
    }
    assert client.post(f"/v1/jobs/{JOB}/usage/1/reveal").status_code == 403


# --------------------------------------------------------------------------- handler hook


def test_handler_keeps_content_only_when_asked(vault, monkeypatch):
    from src.handlers.chat_handler import ChatInferenceHandler

    calls = []
    monkeypatch.setattr(dv, "store", lambda *a: calls.append(a) or True)

    def handler(headers):
        h = ChatInferenceHandler(
            api_key="k",
            background_tasks=None,
            request=SimpleNamespace(headers=headers, state=SimpleNamespace()),
        )
        h.user = {"id": 1, "key_name": f"job:{JOB}"}
        h._client_messages = MESSAGES
        h._records_v2 = lambda: True
        return h

    handler({})._job_line_kwargs("req-1", {"output": OUTPUT})
    assert calls == []
    handler({dv.HEADER: "on"})._job_line_kwargs("req-1", {"output": OUTPUT})
    assert calls == [(JOB, "req-1", MESSAGES, OUTPUT, HASH)]


def test_a_broken_vault_never_breaks_the_usage_line(vault, monkeypatch):
    from src.handlers.chat_handler import ChatInferenceHandler

    def boom(*a):
        raise RuntimeError("vault down")

    monkeypatch.setattr(dv, "opted_in", boom)
    h = ChatInferenceHandler(
        api_key="k",
        background_tasks=None,
        request=SimpleNamespace(headers={}, state=SimpleNamespace()),
    )
    h.user = {"id": 1, "key_name": f"job:{JOB}"}
    h._client_messages = MESSAGES
    h._records_v2 = lambda: True
    assert h._job_line_kwargs("req-1", {"output": OUTPUT})["content_hash"] == HASH
