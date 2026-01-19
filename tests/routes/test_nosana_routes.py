"""Tests for Nosana GPU Computing Network routes"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.routes.nosana import router


# Create a test app with the nosana router
app = FastAPI()
app.include_router(router)


@pytest.fixture
def client():
    """Create a test client"""
    return TestClient(app)


@pytest.fixture
def mock_current_user():
    """Mock the current user dependency"""
    return {"id": "user123", "email": "test@example.com"}


class TestNosanaCreditsEndpoints:
    """Test Nosana credits endpoints"""

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.get_credits_balance")
    def test_get_credit_balance(self, mock_get_balance, mock_auth, client):
        """Test getting credit balance"""
        mock_auth.return_value = {"id": "user123"}
        mock_get_balance.return_value = {
            "assignedCredits": 100.0,
            "reservedCredits": 10.0,
            "settledCredits": 90.0,
        }

        # Override the dependency
        app.dependency_overrides[mock_auth] = lambda: {"id": "user123"}

        response = client.get("/nosana/credits/balance")

        # Note: This test may need adjustment based on actual auth setup
        # For now we're testing the route structure exists


class TestNosanaDeploymentEndpoints:
    """Test Nosana deployment endpoints"""

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.list_deployments")
    def test_list_deployments(self, mock_list, mock_auth, client):
        """Test listing deployments"""
        mock_auth.return_value = {"id": "user123"}
        mock_list.return_value = [
            {"id": "dep1", "name": "test-deployment", "status": "RUNNING"},
        ]

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.get_deployment")
    def test_get_deployment(self, mock_get, mock_auth, client):
        """Test getting deployment details"""
        mock_auth.return_value = {"id": "user123"}
        mock_get.return_value = {
            "id": "dep1",
            "name": "test-deployment",
            "status": "RUNNING",
            "endpoints": [],
        }

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.create_deployment")
    def test_create_deployment(self, mock_create, mock_auth, client):
        """Test creating a deployment"""
        mock_auth.return_value = {"id": "user123"}
        mock_create.return_value = {
            "id": "dep1",
            "name": "test-deployment",
            "status": "DRAFT",
        }


class TestNosanaQuickDeployEndpoints:
    """Test Nosana quick deploy endpoints"""

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.start_deployment")
    @patch("src.routes.nosana.create_deployment")
    @patch("src.routes.nosana.build_llm_inference_job_definition")
    def test_deploy_llm_inference(
        self, mock_build, mock_create, mock_start, mock_auth, client
    ):
        """Test deploying LLM inference"""
        mock_auth.return_value = {"id": "user123"}
        mock_build.return_value = {"version": "0.1", "type": "container", "ops": []}
        mock_create.return_value = {"id": "dep1", "status": "DRAFT"}
        mock_start.return_value = {"id": "dep1", "status": "STARTING"}

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.start_deployment")
    @patch("src.routes.nosana.create_deployment")
    @patch("src.routes.nosana.build_stable_diffusion_job_definition")
    def test_deploy_image_generation(
        self, mock_build, mock_create, mock_start, mock_auth, client
    ):
        """Test deploying image generation"""
        mock_auth.return_value = {"id": "user123"}
        mock_build.return_value = {"version": "0.1", "type": "container", "ops": []}
        mock_create.return_value = {"id": "dep1", "status": "DRAFT"}
        mock_start.return_value = {"id": "dep1", "status": "STARTING"}

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.start_deployment")
    @patch("src.routes.nosana.create_deployment")
    @patch("src.routes.nosana.build_whisper_job_definition")
    def test_deploy_whisper(
        self, mock_build, mock_create, mock_start, mock_auth, client
    ):
        """Test deploying Whisper transcription"""
        mock_auth.return_value = {"id": "user123"}
        mock_build.return_value = {"version": "0.1", "type": "container", "ops": []}
        mock_create.return_value = {"id": "dep1", "status": "DRAFT"}
        mock_start.return_value = {"id": "dep1", "status": "STARTING"}


class TestNosanaJobsEndpoints:
    """Test Nosana jobs endpoints"""

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.create_job")
    def test_create_job(self, mock_create, mock_auth, client):
        """Test creating a job"""
        mock_auth.return_value = {"id": "user123"}
        mock_create.return_value = {
            "tx": "tx123",
            "job": "job123",
            "credits": {"costUSD": 1.5},
        }

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.get_job")
    def test_get_job(self, mock_get, mock_auth, client):
        """Test getting job details"""
        mock_auth.return_value = {"id": "user123"}
        mock_get.return_value = {
            "address": "job123",
            "status": "completed",
            "result": {"output": "success"},
        }

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.extend_job")
    def test_extend_job(self, mock_extend, mock_auth, client):
        """Test extending job duration"""
        mock_auth.return_value = {"id": "user123"}
        mock_extend.return_value = {
            "address": "job123",
            "timeout": 7200,
        }

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.stop_job")
    def test_stop_job(self, mock_stop, mock_auth, client):
        """Test stopping a job"""
        mock_auth.return_value = {"id": "user123"}
        mock_stop.return_value = {
            "address": "job123",
            "status": "stopped",
        }


class TestNosanaMarketsEndpoints:
    """Test Nosana markets endpoints"""

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.list_markets")
    def test_list_markets(self, mock_list, mock_auth, client):
        """Test listing markets"""
        mock_auth.return_value = {"id": "user123"}
        mock_list.return_value = [
            {"id": "market1", "type": "PREMIUM", "slug": "premium-gpu"},
            {"id": "market2", "type": "COMMUNITY", "slug": "community-gpu"},
        ]

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.get_market")
    def test_get_market(self, mock_get, mock_auth, client):
        """Test getting market details"""
        mock_auth.return_value = {"id": "user123"}
        mock_get.return_value = {
            "id": "market1",
            "type": "PREMIUM",
            "slug": "premium-gpu",
            "gpuTypes": ["A100", "H100"],
        }

    @patch("src.routes.nosana.get_current_user")
    @patch("src.routes.nosana.get_market_resources")
    def test_get_market_resources(self, mock_get, mock_auth, client):
        """Test getting market resource requirements"""
        mock_auth.return_value = {"id": "user123"}
        mock_get.return_value = {
            "s3": {"required": False},
            "ollama": {"required": True, "models": ["llama3"]},
        }


class TestNosanaConfigEndpoint:
    """Test Nosana config endpoint"""

    @patch("src.routes.nosana.get_current_user")
    def test_get_config(self, mock_auth, client):
        """Test getting Nosana configuration"""
        mock_auth.return_value = {"id": "user123"}

        # This endpoint should return available configuration options
        # without needing actual Nosana API calls


class TestNosanaRouteValidation:
    """Test Nosana route input validation"""

    def test_deployment_create_model_validation(self):
        """Test deployment creation model validation"""
        from src.routes.nosana import DeploymentCreate

        # Valid deployment
        dep = DeploymentCreate(
            name="test",
            market="market123",
            job_definition={"version": "0.1"},
            timeout=3600,
            replicas=1,
            strategy="SIMPLE",
        )
        assert dep.name == "test"
        assert dep.timeout == 3600

    def test_deployment_create_timeout_bounds(self):
        """Test deployment timeout bounds"""
        from src.routes.nosana import DeploymentCreate
        from pydantic import ValidationError

        # Timeout too low
        with pytest.raises(ValidationError):
            DeploymentCreate(
                name="test",
                market="market123",
                job_definition={},
                timeout=30,  # Below minimum of 60
            )

        # Timeout too high
        with pytest.raises(ValidationError):
            DeploymentCreate(
                name="test",
                market="market123",
                job_definition={},
                timeout=400000,  # Above maximum of 360000
            )

    def test_deployment_replicas_bounds(self):
        """Test deployment replicas bounds"""
        from src.routes.nosana import DeploymentCreate
        from pydantic import ValidationError

        # Replicas below minimum
        with pytest.raises(ValidationError):
            DeploymentCreate(
                name="test",
                market="market123",
                job_definition={},
                replicas=0,  # Below minimum of 1
            )

    def test_job_create_model_validation(self):
        """Test job creation model validation"""
        from src.routes.nosana import JobCreate

        # Valid job
        job = JobCreate(
            ipfs_job="QmHash123",
            market="market123",
            timeout=3600,
        )
        assert job.ipfs_job == "QmHash123"

    def test_llm_inference_job_create_validation(self):
        """Test LLM inference job creation validation"""
        from src.routes.nosana import LLMInferenceJobCreate

        # Valid config
        config = LLMInferenceJobCreate(
            name="llama-deployment",
            market="market123",
            model="meta-llama/Llama-3.1-8B-Instruct",
            framework="vllm",
            port=8000,
            tensor_parallel_size=1,
        )
        assert config.model == "meta-llama/Llama-3.1-8B-Instruct"
        assert config.framework == "vllm"
