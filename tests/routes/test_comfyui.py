#!/usr/bin/env python3
"""
Comprehensive tests for ComfyUI playground endpoints

Tests cover:
- Workflow listing and retrieval
- Workflow execution
- Server status checking
- Authentication and authorization
- Credit validation and deduction
- Request validation
- Progress streaming
- Error handling
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from src.main import app
from src.models.comfyui_models import (
    ComfyUIExecutionResponse,
    ComfyUIServerStatus,
    ExecutionStatus,
    WorkflowType,
)


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)


@pytest.fixture
def mock_user():
    """Sample user with sufficient credits"""
    return {
        'id': 1,
        'email': 'test@example.com',
        'credits': 1000.0,
        'api_key': 'test_api_key_12345'
    }


@pytest.fixture
def mock_user_no_credits():
    """Sample user with insufficient credits"""
    return {
        'id': 2,
        'email': 'broke@example.com',
        'credits': 50.0,  # Less than 100 needed
        'api_key': 'broke_api_key_12345'
    }


@pytest.fixture
def mock_server_status_connected():
    """Sample connected server status"""
    return ComfyUIServerStatus(
        connected=True,
        server_url="http://localhost:8188",
        queue_size=2,
        running_jobs=1,
        available_models=["sd_xl_base_1.0.safetensors", "v1-5-pruned-emaonly.safetensors"],
        system_stats={"cpu_usage": 45.2, "memory_usage": 60.5},
        last_ping=datetime.now(timezone.utc)
    )


@pytest.fixture
def mock_server_status_disconnected():
    """Sample disconnected server status"""
    return ComfyUIServerStatus(
        connected=False,
        server_url="http://localhost:8188",
        queue_size=0,
        running_jobs=0,
        available_models=[],
        system_stats={},
        last_ping=None
    )


@pytest.fixture
def mock_execution_response():
    """Sample successful execution response"""
    return ComfyUIExecutionResponse(
        execution_id="abc123",
        status=ExecutionStatus.COMPLETED,
        workflow_type=WorkflowType.TEXT_TO_IMAGE,
        progress=100,
        current_node=None,
        queue_position=None,
        estimated_time_remaining=None,
        created_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        outputs=[
            {
                "type": "image",
                "filename": "ComfyUI_00001.png",
                "b64_data": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            }
        ],
        error=None,
        credits_charged=100,
        execution_time_ms=15000
    )


@pytest.fixture
def valid_execution_request():
    """Valid workflow execution request"""
    return {
        'workflow_id': 'sdxl-txt2img',
        'prompt': 'A serene mountain landscape at sunset',
        'negative_prompt': 'bad quality, blurry',
        'width': 1024,
        'height': 1024,
        'steps': 20,
        'cfg_scale': 7,
        'seed': 42
    }


# ============================================================
# TEST CLASS: Server Status
# ============================================================

class TestComfyUIServerStatus:
    """Test server status endpoint"""

    @patch('src.routes.comfyui.get_comfyui_client')
    def test_get_server_status_connected(
        self,
        mock_get_client,
        client,
        mock_server_status_connected
    ):
        """Test getting server status when connected"""
        mock_client = Mock()
        mock_client.get_server_status = AsyncMock(return_value=mock_server_status_connected)
        mock_get_client.return_value = mock_client

        response = client.get('/comfyui/status')

        assert response.status_code == 200
        data = response.json()
        assert data['connected'] is True
        assert data['server_url'] == "http://localhost:8188"
        assert data['queue_size'] == 2
        assert data['running_jobs'] == 1
        assert len(data['available_models']) == 2

    @patch('src.routes.comfyui.get_comfyui_client')
    def test_get_server_status_disconnected(
        self,
        mock_get_client,
        client,
        mock_server_status_disconnected
    ):
        """Test getting server status when disconnected"""
        mock_client = Mock()
        mock_client.get_server_status = AsyncMock(return_value=mock_server_status_disconnected)
        mock_get_client.return_value = mock_client

        response = client.get('/comfyui/status')

        assert response.status_code == 200
        data = response.json()
        assert data['connected'] is False
        assert data['queue_size'] == 0


# ============================================================
# TEST CLASS: Workflow Listing
# ============================================================

class TestComfyUIWorkflowListing:
    """Test workflow listing endpoints"""

    def test_list_all_workflows(self, client):
        """Test listing all workflow templates"""
        response = client.get('/comfyui/workflows')

        assert response.status_code == 200
        data = response.json()
        assert 'workflows' in data
        assert 'total' in data
        assert data['total'] > 0
        assert len(data['workflows']) == data['total']

    def test_list_workflows_by_type_text_to_image(self, client):
        """Test listing workflows filtered by type"""
        response = client.get('/comfyui/workflows?workflow_type=text-to-image')

        assert response.status_code == 200
        data = response.json()
        assert all(w['type'] == 'text-to-image' for w in data['workflows'])

    def test_list_workflows_by_type_text_to_video(self, client):
        """Test listing text-to-video workflows"""
        response = client.get('/comfyui/workflows?workflow_type=text-to-video')

        assert response.status_code == 200
        data = response.json()
        # All returned workflows should be text-to-video
        for workflow in data['workflows']:
            assert workflow['type'] == 'text-to-video'

    def test_get_specific_workflow(self, client):
        """Test getting a specific workflow template"""
        response = client.get('/comfyui/workflows/sdxl-txt2img')

        assert response.status_code == 200
        data = response.json()
        assert data['id'] == 'sdxl-txt2img'
        assert data['name'] == 'SDXL Text to Image'
        assert data['type'] == 'text-to-image'
        assert 'workflow_json' in data
        assert 'param_schema' in data
        assert 'credits_per_run' in data

    def test_get_nonexistent_workflow(self, client):
        """Test getting a workflow that doesn't exist"""
        response = client.get('/comfyui/workflows/nonexistent-workflow')

        assert response.status_code == 404
        assert 'not found' in response.json()['detail'].lower()


# ============================================================
# TEST CLASS: Workflow Execution - Success Cases
# ============================================================

class TestComfyUIExecutionSuccess:
    """Test successful workflow execution"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    @patch('src.routes.comfyui.deduct_credits')
    @patch('src.routes.comfyui.record_usage')
    @patch('src.routes.comfyui.increment_api_key_usage')
    def test_execute_workflow_success(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_get_client,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        mock_execution_response,
        valid_execution_request
    ):
        """Test successful workflow execution"""
        # Setup mocks
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user

        mock_client = Mock()
        mock_client.execute_workflow = AsyncMock(return_value=mock_execution_response)
        mock_client.upload_image = AsyncMock()
        mock_get_client.return_value = mock_client

        # Execute
        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_execution_request
        )

        # Verify
        assert response.status_code == 200
        data = response.json()
        assert data['execution_id'] == 'abc123'
        assert data['status'] == 'completed'
        assert len(data['outputs']) == 1
        assert data['outputs'][0]['type'] == 'image'
        assert data['credits_charged'] == 100

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    @patch('src.routes.comfyui.deduct_credits')
    @patch('src.routes.comfyui.record_usage')
    @patch('src.routes.comfyui.increment_api_key_usage')
    def test_execute_custom_workflow(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_get_client,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        mock_execution_response
    ):
        """Test executing a custom workflow JSON"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user

        mock_client = Mock()
        mock_client.execute_workflow = AsyncMock(return_value=mock_execution_response)
        mock_get_client.return_value = mock_client

        # Custom workflow JSON
        custom_request = {
            'workflow_json': {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {"seed": 42}
                }
            },
            'prompt': 'Test prompt'
        }

        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=custom_request
        )

        assert response.status_code == 200


# ============================================================
# TEST CLASS: Workflow Execution - Authentication
# ============================================================

class TestComfyUIExecutionAuth:
    """Test authentication for workflow execution"""

    def test_execute_without_auth_header(self, client, valid_execution_request):
        """Test execution without Authorization header"""
        response = client.post(
            '/comfyui/execute',
            json=valid_execution_request
        )

        assert response.status_code in [401, 403]

    @patch('src.routes.comfyui.get_user')
    def test_execute_with_invalid_api_key(
        self,
        mock_get_user,
        client,
        valid_execution_request
    ):
        """Test execution with invalid API key"""
        mock_get_user.return_value = None

        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer invalid_key'},
            json=valid_execution_request
        )

        assert response.status_code == 401


# ============================================================
# TEST CLASS: Workflow Execution - Credit Validation
# ============================================================

class TestComfyUIExecutionCredits:
    """Test credit validation for workflow execution"""

    @patch('src.routes.comfyui.get_user')
    def test_execute_insufficient_credits(
        self,
        mock_get_user,
        client,
        mock_user_no_credits,
        valid_execution_request
    ):
        """Test execution with insufficient credits"""
        mock_get_user.return_value = mock_user_no_credits

        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer broke_api_key_12345'},
            json=valid_execution_request
        )

        assert response.status_code == 402
        assert 'insufficient credits' in response.json()['detail'].lower()


# ============================================================
# TEST CLASS: Workflow Execution - Validation
# ============================================================

class TestComfyUIExecutionValidation:
    """Test request validation for workflow execution"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_user')
    def test_execute_missing_workflow(
        self,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test execution without workflow_id or workflow_json"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user

        request_data = {
            'prompt': 'Test prompt'
            # Missing both workflow_id and workflow_json
        }

        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=request_data
        )

        assert response.status_code == 400
        assert 'workflow_id or workflow_json' in response.json()['detail'].lower()

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_user')
    def test_execute_invalid_workflow_id(
        self,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test execution with invalid workflow_id"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user

        request_data = {
            'workflow_id': 'nonexistent-workflow',
            'prompt': 'Test prompt'
        }

        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=request_data
        )

        assert response.status_code == 404
        assert 'not found' in response.json()['detail'].lower()


# ============================================================
# TEST CLASS: Execution Status
# ============================================================

class TestComfyUIExecutionStatus:
    """Test execution status endpoint"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    def test_get_execution_status_completed(
        self,
        mock_get_client,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test getting status of completed execution"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user

        mock_client = Mock()
        mock_client.get_history = AsyncMock(return_value={
            'outputs': {
                'node1': {
                    'images': [{'filename': 'output.png', 'subfolder': ''}]
                }
            },
            'status': {'status_str': 'success'}
        })
        mock_client.server_url = 'http://localhost:8188'
        mock_get_client.return_value = mock_client

        response = client.get(
            '/comfyui/executions/abc123',
            headers={'Authorization': 'Bearer test_api_key_12345'}
        )

        assert response.status_code == 200
        data = response.json()
        assert data['execution_id'] == 'abc123'
        assert data['status'] == 'completed'
        assert len(data['outputs']) > 0

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    def test_get_execution_status_not_found(
        self,
        mock_get_client,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test getting status of non-existent execution"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user

        mock_client = Mock()
        mock_client.get_history = AsyncMock(return_value=None)
        mock_get_client.return_value = mock_client

        response = client.get(
            '/comfyui/executions/nonexistent',
            headers={'Authorization': 'Bearer test_api_key_12345'}
        )

        assert response.status_code == 404


# ============================================================
# TEST CLASS: Cancel Execution
# ============================================================

class TestComfyUICancelExecution:
    """Test execution cancellation"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    def test_cancel_execution_success(
        self,
        mock_get_client,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test successful execution cancellation"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user

        mock_client = Mock()
        mock_client.cancel_execution = AsyncMock(return_value=True)
        mock_get_client.return_value = mock_client

        response = client.post(
            '/comfyui/executions/abc123/cancel',
            headers={'Authorization': 'Bearer test_api_key_12345'}
        )

        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'cancelled'
        assert data['execution_id'] == 'abc123'

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    def test_cancel_execution_failure(
        self,
        mock_get_client,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test failed execution cancellation"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user

        mock_client = Mock()
        mock_client.cancel_execution = AsyncMock(return_value=False)
        mock_get_client.return_value = mock_client

        response = client.post(
            '/comfyui/executions/abc123/cancel',
            headers={'Authorization': 'Bearer test_api_key_12345'}
        )

        assert response.status_code == 400


# ============================================================
# TEST CLASS: Queue and Models
# ============================================================

class TestComfyUIQueueAndModels:
    """Test queue status and model listing"""

    @patch('src.routes.comfyui.get_comfyui_client')
    def test_get_queue_status(
        self,
        mock_get_client,
        client,
        mock_server_status_connected
    ):
        """Test getting queue status"""
        mock_client = Mock()
        mock_client.get_server_status = AsyncMock(return_value=mock_server_status_connected)
        mock_get_client.return_value = mock_client

        response = client.get('/comfyui/queue')

        assert response.status_code == 200
        data = response.json()
        assert 'queue_size' in data
        assert 'running_jobs' in data
        assert 'connected' in data

    @patch('src.routes.comfyui.get_comfyui_client')
    def test_get_available_models(
        self,
        mock_get_client,
        client,
        mock_server_status_connected
    ):
        """Test getting available models"""
        mock_client = Mock()
        mock_client.get_server_status = AsyncMock(return_value=mock_server_status_connected)
        mock_get_client.return_value = mock_client

        response = client.get('/comfyui/models')

        assert response.status_code == 200
        data = response.json()
        assert 'models' in data
        assert 'connected' in data
        assert len(data['models']) == 2


# ============================================================
# TEST CLASS: Error Handling
# ============================================================

class TestComfyUIErrorHandling:
    """Test error handling"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    def test_execution_server_error(
        self,
        mock_get_client,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        valid_execution_request
    ):
        """Test handling of ComfyUI server errors"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user

        mock_client = Mock()
        mock_client.execute_workflow = AsyncMock(side_effect=Exception("ComfyUI server error"))
        mock_get_client.return_value = mock_client

        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_execution_request
        )

        assert response.status_code == 500
        assert 'failed' in response.json()['detail'].lower()

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.comfyui.get_user')
    @patch('src.routes.comfyui.get_comfyui_client')
    def test_image_upload_error(
        self,
        mock_get_client,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test handling of image upload errors"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user

        mock_client = Mock()
        mock_client.upload_image = AsyncMock(side_effect=Exception("Upload failed"))
        mock_get_client.return_value = mock_client

        request_data = {
            'workflow_id': 'sdxl-img2img',
            'prompt': 'Test',
            'input_image': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        }

        response = client.post(
            '/comfyui/execute',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=request_data
        )

        assert response.status_code == 500
        assert 'upload' in response.json()['detail'].lower()


# ============================================================
# TEST CLASS: History
# ============================================================

class TestComfyUIHistory:
    """Test execution history"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    def test_get_history_empty(
        self,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test getting empty execution history"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user

        response = client.get(
            '/comfyui/history',
            headers={'Authorization': 'Bearer test_api_key_12345'}
        )

        assert response.status_code == 200
        data = response.json()
        assert 'history' in data
        assert 'total' in data
        assert 'page' in data
        assert 'page_size' in data
        assert data['total'] == 0  # Currently returns empty (placeholder)

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    def test_get_history_with_pagination(
        self,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test history pagination parameters"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user

        response = client.get(
            '/comfyui/history?page=2&page_size=10',
            headers={'Authorization': 'Bearer test_api_key_12345'}
        )

        assert response.status_code == 200
        data = response.json()
        assert data['page'] == 2
        assert data['page_size'] == 10
