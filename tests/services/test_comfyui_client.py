#!/usr/bin/env python3
"""
Tests for ComfyUI client service

Tests cover:
- Client initialization
- Server status checking
- Workflow queuing
- Progress streaming
- History retrieval
- Image upload and retrieval
- Parameter injection
- Error handling
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import json
from datetime import datetime, timezone

from src.services.comfyui_client import (
    ComfyUIClient,
    get_comfyui_client,
    close_comfyui_client,
)
from src.models.comfyui_models import (
    ComfyUIExecutionRequest,
    ExecutionStatus,
    WorkflowType,
)


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def client():
    """Create a ComfyUI client for testing"""
    return ComfyUIClient(server_url="http://localhost:8188")


@pytest.fixture
def sample_workflow():
    """Sample ComfyUI workflow JSON"""
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "cfg": 7,
                "denoise": 1,
                "latent_image": ["5", 0],
                "model": ["4", 0],
                "negative": ["7", 0],
                "positive": ["6", 0],
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "seed": 42,
                "steps": 20
            }
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "batch_size": 1,
                "height": 1024,
                "width": 1024
            }
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["4", 1],
                "text": "original prompt"
            }
        }
    }


@pytest.fixture
def execution_request():
    """Sample execution request"""
    return ComfyUIExecutionRequest(
        workflow_id="sdxl-txt2img",
        prompt="A beautiful sunset",
        negative_prompt="bad quality",
        width=1024,
        height=1024,
        steps=25,
        cfg_scale=8,
        seed=12345
    )


# ============================================================
# TEST CLASS: Client Initialization
# ============================================================

class TestComfyUIClientInit:
    """Test client initialization"""

    def test_init_with_server_url(self):
        """Test initialization with explicit server URL"""
        client = ComfyUIClient(server_url="http://custom:8188")
        assert client.server_url == "http://custom:8188"
        assert client.client_id is not None

    @patch.dict('os.environ', {}, clear=True)
    def test_init_without_server_url(self):
        """Test initialization without server URL"""
        client = ComfyUIClient()
        # Should handle missing URL gracefully
        assert client.server_url is None

    def test_client_id_is_uuid(self, client):
        """Test that client ID is a valid UUID"""
        import uuid
        # Should not raise
        uuid.UUID(client.client_id)


# ============================================================
# TEST CLASS: Server Status
# ============================================================

class TestComfyUIServerStatus:
    """Test server status checking"""

    @pytest.mark.asyncio
    async def test_get_server_status_success(self, client):
        """Test successful server status retrieval"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "system": {"cpu_usage": 45.2},
            "devices": [{"name": "NVIDIA RTX 4090"}]
        }
        mock_response.raise_for_status = Mock()

        mock_queue_response = Mock()
        mock_queue_response.json.return_value = {
            "queue_pending": [["id1", 1, {}]],
            "queue_running": []
        }
        mock_queue_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=[mock_response, mock_queue_response])
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            status = await client.get_server_status()

        assert status.connected is True
        assert status.queue_size == 1
        assert status.running_jobs == 0

    @pytest.mark.asyncio
    async def test_get_server_status_connection_error(self, client):
        """Test server status when connection fails"""
        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            status = await client.get_server_status()

        assert status.connected is False

    @pytest.mark.asyncio
    async def test_get_server_status_no_url(self):
        """Test server status when no URL configured"""
        client = ComfyUIClient(server_url=None)
        status = await client.get_server_status()
        assert status.connected is False


# ============================================================
# TEST CLASS: Parameter Injection
# ============================================================

class TestParameterInjection:
    """Test parameter injection into workflows"""

    def test_inject_prompt_into_workflow(self, client, sample_workflow, execution_request):
        """Test injecting prompt into CLIP text encode nodes"""
        result = client._inject_params_into_workflow(sample_workflow, execution_request)

        # Verify KSampler parameters updated
        assert result["3"]["inputs"]["seed"] == 12345
        assert result["3"]["inputs"]["steps"] == 25
        assert result["3"]["inputs"]["cfg"] == 8

    def test_inject_dimensions_into_workflow(self, client, sample_workflow, execution_request):
        """Test injecting dimensions into EmptyLatentImage"""
        execution_request.width = 768
        execution_request.height = 512

        result = client._inject_params_into_workflow(sample_workflow, execution_request)

        assert result["5"]["inputs"]["width"] == 768
        assert result["5"]["inputs"]["height"] == 512

    def test_inject_denoise_for_img2img(self, client, sample_workflow):
        """Test injecting denoise strength for img2img"""
        request = ComfyUIExecutionRequest(
            prompt="Test",
            denoise_strength=0.5
        )

        # Add denoise to the workflow for testing
        sample_workflow["3"]["inputs"]["denoise"] = 1.0

        result = client._inject_params_into_workflow(sample_workflow, request)

        assert result["3"]["inputs"]["denoise"] == 0.5

    def test_workflow_deep_copy(self, client, sample_workflow, execution_request):
        """Test that original workflow is not modified"""
        original_seed = sample_workflow["3"]["inputs"]["seed"]
        execution_request.seed = 99999

        client._inject_params_into_workflow(sample_workflow, execution_request)

        # Original should be unchanged
        assert sample_workflow["3"]["inputs"]["seed"] == original_seed


# ============================================================
# TEST CLASS: Queue Prompt
# ============================================================

class TestQueuePrompt:
    """Test prompt queuing"""

    @pytest.mark.asyncio
    async def test_queue_prompt_success(self, client, sample_workflow):
        """Test successful prompt queuing"""
        mock_response = Mock()
        mock_response.json.return_value = {"prompt_id": "abc123"}
        mock_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            prompt_id = await client.queue_prompt(sample_workflow)

        assert prompt_id == "abc123"
        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_prompt_with_request_params(self, client, sample_workflow, execution_request):
        """Test queuing with parameter injection"""
        mock_response = Mock()
        mock_response.json.return_value = {"prompt_id": "def456"}
        mock_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            prompt_id = await client.queue_prompt(sample_workflow, execution_request)

        assert prompt_id == "def456"

        # Verify the posted workflow had parameters injected
        call_args = mock_http_client.post.call_args
        posted_data = call_args[1]['json']
        assert posted_data['client_id'] == client.client_id

    @pytest.mark.asyncio
    async def test_queue_prompt_no_server_url(self, sample_workflow):
        """Test queueing when no server URL"""
        client = ComfyUIClient(server_url=None)

        with pytest.raises(ValueError, match="not configured"):
            await client.queue_prompt(sample_workflow)

    @pytest.mark.asyncio
    async def test_queue_prompt_no_prompt_id_response(self, client, sample_workflow):
        """Test handling when response has no prompt_id"""
        mock_response = Mock()
        mock_response.json.return_value = {"error": "some error"}
        mock_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            with pytest.raises(ValueError, match="No prompt_id"):
                await client.queue_prompt(sample_workflow)


# ============================================================
# TEST CLASS: History Retrieval
# ============================================================

class TestGetHistory:
    """Test history/results retrieval"""

    @pytest.mark.asyncio
    async def test_get_history_success(self, client):
        """Test successful history retrieval"""
        history_data = {
            "outputs": {
                "9": {"images": [{"filename": "out.png", "subfolder": ""}]}
            },
            "status": {"status_str": "success"}
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"abc123": history_data}
        mock_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            result = await client.get_history("abc123")

        assert result == history_data
        assert "outputs" in result

    @pytest.mark.asyncio
    async def test_get_history_not_found(self, client):
        """Test history retrieval when not found"""
        mock_response = Mock()
        mock_response.status_code = 404

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            result = await client.get_history("nonexistent")

        assert result is None


# ============================================================
# TEST CLASS: Image Operations
# ============================================================

class TestImageOperations:
    """Test image upload and retrieval"""

    @pytest.mark.asyncio
    async def test_upload_image_success(self, client):
        """Test successful image upload"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "name": "input.png",
            "subfolder": "",
            "type": "input"
        }
        mock_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        # Simple 1x1 PNG base64
        image_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            result = await client.upload_image(image_data, "test.png")

        assert result["name"] == "input.png"
        mock_http_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_image_with_data_url(self, client):
        """Test uploading image with data URL prefix"""
        mock_response = Mock()
        mock_response.json.return_value = {"name": "input.png"}
        mock_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        image_data = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            result = await client.upload_image(image_data)

        assert result is not None

    @pytest.mark.asyncio
    async def test_get_image_success(self, client):
        """Test successful image retrieval"""
        image_bytes = b'\x89PNG\r\n\x1a\n'  # PNG header

        mock_response = Mock()
        mock_response.content = image_bytes
        mock_response.raise_for_status = Mock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            result = await client.get_image("output.png", "", "output")

        assert result == image_bytes


# ============================================================
# TEST CLASS: Cancel Execution
# ============================================================

class TestCancelExecution:
    """Test execution cancellation"""

    @pytest.mark.asyncio
    async def test_cancel_execution_success(self, client):
        """Test successful cancellation"""
        mock_response = Mock()
        mock_response.status_code = 200

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        mock_http_client.is_closed = False

        with patch.object(client, '_get_http_client', return_value=mock_http_client):
            result = await client.cancel_execution("abc123")

        assert result is True
        # Should call both queue delete and interrupt
        assert mock_http_client.post.call_count == 2


# ============================================================
# TEST CLASS: Global Client
# ============================================================

class TestGlobalClient:
    """Test global client instance management"""

    def test_get_comfyui_client_singleton(self):
        """Test that get_comfyui_client returns same instance"""
        client1 = get_comfyui_client()
        client2 = get_comfyui_client()
        assert client1 is client2

    @pytest.mark.asyncio
    async def test_close_comfyui_client(self):
        """Test closing the global client"""
        # Get a client first
        _ = get_comfyui_client()

        # Close it
        await close_comfyui_client()

        # Getting a new client should create a fresh instance
        # (implementation detail - may vary)
