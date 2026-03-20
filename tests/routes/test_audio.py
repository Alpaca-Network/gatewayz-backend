"""Tests for audio transcription routes."""

import base64
import io
import struct
import wave
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Mock the config before importing the app
with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
    from src.main import app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def sample_wav_bytes():
    """Generate a minimal valid WAV file."""
    # Create a simple WAV file with silence
    sample_rate = 16000
    duration = 0.1  # 100ms
    num_samples = int(sample_rate * duration)

    # Generate silence (zeros)
    samples = [0] * num_samples

    # Create WAV file in memory
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        # Pack samples as 16-bit signed integers
        wav_file.writeframes(struct.pack(f"{len(samples)}h", *samples))

    return buffer.getvalue()


@pytest.fixture
def sample_audio_base64(sample_wav_bytes):
    """Generate base64-encoded audio data."""
    return base64.b64encode(sample_wav_bytes).decode("utf-8")


class TestAudioTranscriptions:
    """Tests for POST /v1/audio/transcriptions endpoint."""

    def test_transcription_endpoint_exists(self, client):
        """Test that the transcription endpoint is registered."""
        # Send a request without a file to check the endpoint exists
        response = client.post("/v1/audio/transcriptions")
        # Should get 422 (validation error) not 404 (not found)
        assert response.status_code == 422

    def test_transcription_requires_file(self, client):
        """Test that file parameter is required."""
        response = client.post("/v1/audio/transcriptions", data={})
        assert response.status_code == 422
        assert "file" in response.text.lower() or "field required" in response.text.lower()

    @patch("src.routes.audio.get_openai_pooled_client")
    def test_transcription_success(self, mock_get_client, client, sample_wav_bytes):
        """Test successful transcription."""
        # Mock the OpenAI client response
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Hello, world!"
        mock_response.language = "en"
        mock_response.duration = 1.5
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Create file upload
        files = {"file": ("test.wav", io.BytesIO(sample_wav_bytes), "audio/wav")}
        data = {"model": "whisper-1"}

        response = client.post("/v1/audio/transcriptions", files=files, data=data)

        assert response.status_code == 200
        result = response.json()
        assert result["text"] == "Hello, world!"
        assert result["language"] == "en"
        assert result["duration"] == 1.5

    @patch("src.routes.audio.get_openai_pooled_client")
    def test_transcription_with_language_hint(self, mock_get_client, client, sample_wav_bytes):
        """Test transcription with language hint."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Bonjour le monde"
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        files = {"file": ("test.wav", io.BytesIO(sample_wav_bytes), "audio/wav")}
        data = {"model": "whisper-1", "language": "fr"}

        response = client.post("/v1/audio/transcriptions", files=files, data=data)

        assert response.status_code == 200
        # Verify language was passed to the API
        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["language"] == "fr"

    @patch("src.routes.audio.get_openai_pooled_client")
    def test_transcription_with_prompt(self, mock_get_client, client, sample_wav_bytes):
        """Test transcription with prompt context."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "The API endpoint is /v1/chat/completions"
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        files = {"file": ("test.wav", io.BytesIO(sample_wav_bytes), "audio/wav")}
        data = {
            "model": "whisper-1",
            "prompt": "Technical discussion about REST APIs and endpoints",
        }

        response = client.post("/v1/audio/transcriptions", files=files, data=data)

        assert response.status_code == 200
        # Verify prompt was passed to the API
        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["prompt"] == "Technical discussion about REST APIs and endpoints"

    def test_transcription_empty_file(self, client):
        """Test that empty files are rejected."""
        files = {"file": ("test.wav", io.BytesIO(b""), "audio/wav")}

        response = client.post("/v1/audio/transcriptions", files=files)

        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_transcription_unsupported_format(self, client):
        """Test that unsupported formats are handled."""
        # Create a fake file with unsupported content type
        files = {"file": ("test.txt", io.BytesIO(b"not audio"), "text/plain")}

        response = client.post("/v1/audio/transcriptions", files=files)

        # Should either reject or attempt to process (depends on implementation)
        # At minimum, should not crash
        assert response.status_code in [400, 502]


class TestAudioTranscriptionsBase64:
    """Tests for POST /v1/audio/transcriptions/base64 endpoint."""

    def test_base64_endpoint_exists(self, client):
        """Test that the base64 transcription endpoint is registered."""
        response = client.post("/v1/audio/transcriptions/base64")
        # Should get 422 (validation error) not 404 (not found)
        assert response.status_code == 422

    @patch("src.routes.audio.get_openai_pooled_client")
    def test_base64_transcription_success(self, mock_get_client, client, sample_audio_base64):
        """Test successful base64 transcription."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Hello from base64!"
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        response = client.post(
            "/v1/audio/transcriptions/base64",
            data={
                "audio_data": sample_audio_base64,
                "content_type": "audio/wav",
                "model": "whisper-1",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["text"] == "Hello from base64!"

    @patch("src.routes.audio.get_openai_pooled_client")
    def test_base64_data_url_format(self, mock_get_client, client, sample_audio_base64):
        """Test handling of data URL format."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Data URL test"
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Send as data URL format
        data_url = f"data:audio/wav;base64,{sample_audio_base64}"

        response = client.post(
            "/v1/audio/transcriptions/base64",
            data={
                "audio_data": data_url,
                "model": "whisper-1",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["text"] == "Data URL test"

    def test_base64_invalid_data(self, client):
        """Test that invalid base64 data is rejected."""
        response = client.post(
            "/v1/audio/transcriptions/base64",
            data={
                "audio_data": "not-valid-base64!!!",
                "content_type": "audio/wav",
            },
        )

        assert response.status_code == 400
        assert (
            "invalid" in response.json()["detail"].lower()
            or "base64" in response.json()["detail"].lower()
        )

    def test_base64_empty_data(self, client):
        """Test that empty base64 data is rejected."""
        # Base64 of empty string
        empty_base64 = base64.b64encode(b"").decode("utf-8")

        response = client.post(
            "/v1/audio/transcriptions/base64",
            data={
                "audio_data": empty_base64,
                "content_type": "audio/wav",
            },
        )

        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()


class TestAudioFormats:
    """Tests for supported audio format handling."""

    @pytest.mark.parametrize(
        "content_type,extension",
        [
            ("audio/wav", ".wav"),
            ("audio/webm", ".webm"),
            ("audio/mp3", ".mp3"),
            ("audio/mpeg", ".mp3"),
            ("audio/ogg", ".ogg"),
            ("audio/flac", ".flac"),
            ("audio/m4a", ".m4a"),
        ],
    )
    @patch("src.routes.audio.get_openai_pooled_client")
    def test_supported_formats(
        self, mock_get_client, client, sample_wav_bytes, content_type, extension
    ):
        """Test that various audio formats are accepted."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Format test"
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Use the sample WAV bytes but with different content type
        files = {"file": (f"test{extension}", io.BytesIO(sample_wav_bytes), content_type)}

        response = client.post("/v1/audio/transcriptions", files=files)

        # Should not reject based on content type
        # May fail at Whisper API level if format doesn't match, but that's expected
        assert response.status_code in [200, 502]


class TestAudioErrorHandling:
    """Tests for error handling in audio transcription."""

    @patch("src.routes.audio.get_openai_pooled_client")
    def test_whisper_api_error(self, mock_get_client, client, sample_wav_bytes):
        """Test handling of Whisper API errors."""
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        files = {"file": ("test.wav", io.BytesIO(sample_wav_bytes), "audio/wav")}

        response = client.post("/v1/audio/transcriptions", files=files)

        assert response.status_code == 502
        assert "transcription failed" in response.json()["detail"].lower()

    @patch("src.routes.audio.get_openai_pooled_client")
    def test_client_unavailable(self, mock_get_client, client, sample_wav_bytes):
        """Test handling when OpenAI client is unavailable."""
        mock_get_client.side_effect = Exception("Client unavailable")

        files = {"file": ("test.wav", io.BytesIO(sample_wav_bytes), "audio/wav")}

        response = client.post("/v1/audio/transcriptions", files=files)

        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"].lower()
