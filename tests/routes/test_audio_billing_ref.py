"""Audio transcription billing must be idempotent on the server-minted billing ref.

Threat model L7/G4 (docs/security/ANONYMITY_THREAT_MODEL.md): every credit
deduction is keyed by ``request.state.billing_ref`` (minted by
RequestIDMiddleware, never derived from the client-settable X-Request-ID) so
that a retried/duplicated request cannot double-charge. Chat got this in
PR #2282; this file pins the same guarantee for both audio endpoints.

Everything except the route handler, RequestIDMiddleware and the billing
helper is mocked: no DB, no OpenAI, no rate limiter.
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.routes.audio as audio_module
from src.middleware.request_id_middleware import RequestIDMiddleware
from src.security.deps import get_api_key

TEST_API_KEY = "gw_live_audio_billing_ref_test_key"
CLIENT_REQUEST_ID = "client-controlled-canary-id"
USER_ROW = {
    "id": 4242,
    "api_key": TEST_API_KEY,
    "subscription_allowance": 100.0,
    "purchased_credits": 100.0,
    "credits": 200.0,
}


def _deduct_request_id(mock_deduct: MagicMock) -> str | None:
    """deduct_credits(api_key, tokens, description, metadata, request_id) —
    accept the idempotency key either positionally or by keyword."""
    assert mock_deduct.call_count == 1, mock_deduct.call_args_list
    args, kwargs = mock_deduct.call_args
    if "request_id" in kwargs:
        return kwargs["request_id"]
    return args[4] if len(args) > 4 else None


@pytest.fixture
def audio_app():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.include_router(audio_module.router, prefix="/v1")
    app.dependency_overrides[get_api_key] = lambda: TEST_API_KEY
    return app


@pytest.fixture
def mocked_audio_pipeline():
    """Stub every external effect of the transcription handlers."""
    # Plain object (not MagicMock): the handler serializes whatever attributes
    # it finds on the response, so only give it JSON-safe ones.
    whisper_response = SimpleNamespace(text="hello world", language="en", duration=30.0)

    openai_client = MagicMock()
    openai_client.audio.transcriptions.create.return_value = whisper_response

    with (
        patch.object(audio_module, "enforce_request_rate_limit", new=AsyncMock()),
        patch.object(audio_module, "get_user", return_value=dict(USER_ROW)),
        patch.object(audio_module, "enforce_subscription_status_gate"),
        patch.object(audio_module, "get_openai_pooled_client", return_value=openai_client),
        patch.object(audio_module, "deduct_credits") as mock_deduct,
        patch.object(audio_module, "record_usage"),
        patch.object(audio_module, "increment_api_key_usage"),
    ):
        yield mock_deduct


class TestDeductAudioCreditsHelper:
    def test_helper_passes_request_id_as_idempotency_key(self):
        """_deduct_audio_credits must hand its request_id to deduct_credits as
        the idempotency key — not only use it for log lines."""

        async def run():
            loop = asyncio.get_running_loop()
            with (
                patch.object(audio_module, "deduct_credits") as mock_deduct,
                patch.object(audio_module, "get_user", return_value=dict(USER_ROW)),
                patch.object(audio_module, "record_usage"),
                patch.object(audio_module, "increment_api_key_usage"),
            ):
                await audio_module._deduct_audio_credits(
                    api_key=TEST_API_KEY,
                    user=dict(USER_ROW),
                    model="whisper-1",
                    total_cost=0.01,
                    duration_minutes=1.0,
                    elapsed_ms=10,
                    request_id="billing-ref-canary",
                    endpoint="/v1/audio/transcriptions",
                    loop=loop,
                    executor=ThreadPoolExecutor(max_workers=1),
                )
            return mock_deduct

        mock_deduct = asyncio.run(run())
        assert _deduct_request_id(mock_deduct) == "billing-ref-canary"


class TestTranscriptionRoutesUseServerMintedBillingRef:
    def test_multipart_endpoint_deducts_with_billing_ref(self, audio_app, mocked_audio_pipeline):
        client = TestClient(audio_app)
        response = client.post(
            "/v1/audio/transcriptions",
            headers={"X-Request-ID": CLIENT_REQUEST_ID},
            files={"file": ("clip.wav", b"\x00" * 2048, "audio/wav")},
            data={"model": "whisper-1"},
        )
        assert response.status_code == 200, response.text

        billing_ref = response.headers["X-Gatewayz-Request-Id"]
        used = _deduct_request_id(mocked_audio_pipeline)
        assert used == billing_ref
        assert used != CLIENT_REQUEST_ID
        assert CLIENT_REQUEST_ID not in used

    def test_base64_endpoint_deducts_with_billing_ref(self, audio_app, mocked_audio_pipeline):
        client = TestClient(audio_app)
        response = client.post(
            "/v1/audio/transcriptions/base64",
            headers={"X-Request-ID": CLIENT_REQUEST_ID},
            data={
                "audio_data": base64.b64encode(b"\x00" * 2048).decode(),
                "content_type": "audio/wav",
                "model": "whisper-1",
            },
        )
        assert response.status_code == 200, response.text

        billing_ref = response.headers["X-Gatewayz-Request-Id"]
        used = _deduct_request_id(mocked_audio_pipeline)
        assert used == billing_ref
        assert used != CLIENT_REQUEST_ID
