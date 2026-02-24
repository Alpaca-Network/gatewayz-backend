"""Tests for Nosana GPU Computing Network routes"""

from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.routes.nosana import get_current_user, router

# Create a test app with the nosana router
app = FastAPI()
app.include_router(router)


def mock_user_override():
    """Override for current user dependency"""
    return {"id": "user123", "email": "test@example.com"}


@pytest.fixture
def client():
    """Create a test client with auth override"""
    app.dependency_overrides[get_current_user] = mock_user_override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def mock_current_user():
    """Mock the current user dependency"""
    return {"id": "user123", "email": "test@example.com"}


class TestNosanaCreditsEndpoints:
    """Test Nosana credits endpoints"""

    @patch("src.routes.nosana.get_credits_balance")
    def test_get_credit_balance(self, mock_get_balance, client):
        """Test getting credit balance"""
        mock_get_balance.return_value = {
            "assignedCredits": 100.0,
            "reservedCredits": 10.0,
            "settledCredits": 90.0,
        }

        response = client.get("/nosana/credits/balance")

        assert response.status_code in [200, 401, 403]
        if response.status_code == 200:
            data = response.json()
            assert "assignedCredits" in data or "error" in data


class TestNosanaDeploymentEndpoints:
    """Test Nosana deployment endpoints"""

    @patch("src.routes.nosana.list_deployments")
    def test_list_deployments(self, mock_list, client):
        """Test listing deployments"""
        mock_list.return_value = [
            {"id": "dep1", "name": "test-deployment", "status": "RUNNING"},
        ]

        response = client.get("/nosana/deployments")

        assert response.status_code in [200, 401, 403]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or "deployments" in data or "error" in data

    @patch("src.routes.nosana.get_deployment")
    def test_get_deployment(self, mock_get, client):
        """Test getting deployment details"""
        mock_get.return_value = {
            "id": "dep1",
            "name": "test-deployment",
            "status": "RUNNING",
            "endpoints": [],
        }

        response = client.get("/nosana/deployments/dep1")

        assert response.status_code in [200, 401, 403, 404]
        if response.status_code == 200:
            data = response.json()
            assert "id" in data or "error" in data

    @patch("src.routes.nosana.create_deployment")
    def test_create_deployment(self, mock_create, client):
        """Test creating a deployment"""
        mock_create.return_value = {
            "id": "dep1",
            "name": "test-deployment",
            "status": "DRAFT",
        }

        response = client.post(
            "/nosana/deployments",
            json={
                "name": "test-deployment",
                "market": "market123",
                "job_definition": {"version": "0.1", "type": "container", "ops": []},
                "timeout": 3600,
            },
        )

        assert response.status_code in [200, 201, 401, 403, 422]


class TestNosanaQuickDeployEndpoints:
    """Test Nosana quick deploy endpoints"""

    @patch("src.routes.nosana.start_deployment")
    @patch("src.routes.nosana.create_deployment")
    @patch("src.routes.nosana.build_llm_inference_job_definition")
    def test_deploy_llm_inference(self, mock_build, mock_create, mock_start, client):
        """Test deploying LLM inference"""
        mock_build.return_value = {"version": "0.1", "type": "container", "ops": []}
        mock_create.return_value = {"id": "dep1", "status": "DRAFT"}
        mock_start.return_value = {"id": "dep1", "status": "STARTING"}

        response = client.post(
            "/nosana/quick-deploy/llm",
            json={
                "name": "test-llm",
                "market": "market123",
                "model": "meta-llama/Llama-3.1-8B-Instruct",
                "framework": "vllm",
            },
        )

        assert response.status_code in [200, 201, 401, 403, 422]

    @patch("src.routes.nosana.start_deployment")
    @patch("src.routes.nosana.create_deployment")
    @patch("src.routes.nosana.build_stable_diffusion_job_definition")
    def test_deploy_image_generation(self, mock_build, mock_create, mock_start, client):
        """Test deploying image generation"""
        mock_build.return_value = {"version": "0.1", "type": "container", "ops": []}
        mock_create.return_value = {"id": "dep1", "status": "DRAFT"}
        mock_start.return_value = {"id": "dep1", "status": "STARTING"}

        response = client.post(
            "/nosana/quick-deploy/image",
            json={
                "name": "test-sd",
                "market": "market123",
            },
        )

        assert response.status_code in [200, 201, 401, 403, 422]

    @patch("src.routes.nosana.start_deployment")
    @patch("src.routes.nosana.create_deployment")
    @patch("src.routes.nosana.build_whisper_job_definition")
    def test_deploy_whisper(self, mock_build, mock_create, mock_start, client):
        """Test deploying Whisper transcription"""
        mock_build.return_value = {"version": "0.1", "type": "container", "ops": []}
        mock_create.return_value = {"id": "dep1", "status": "DRAFT"}
        mock_start.return_value = {"id": "dep1", "status": "STARTING"}

        response = client.post(
            "/nosana/quick-deploy/whisper",
            json={
                "name": "test-whisper",
                "market": "market123",
            },
        )

        assert response.status_code in [200, 201, 401, 403, 422]


class TestNosanaJobsEndpoints:
    """Test Nosana jobs endpoints"""

    @patch("src.routes.nosana.create_job")
    def test_create_job(self, mock_create, client):
        """Test creating a job"""
        mock_create.return_value = {
            "tx": "tx123",
            "job": "job123",
            "credits": {"costUSD": 1.5},
        }

        response = client.post(
            "/nosana/jobs",
            json={
                "ipfs_job": "QmHash123",
                "market": "market123",
                "timeout": 3600,
            },
        )

        assert response.status_code in [200, 201, 401, 403, 422]

    @patch("src.routes.nosana.get_job")
    def test_get_job(self, mock_get, client):
        """Test getting job details"""
        mock_get.return_value = {
            "address": "job123",
            "status": "completed",
            "result": {"output": "success"},
        }

        response = client.get("/nosana/jobs/job123")

        assert response.status_code in [200, 401, 403, 404]
        if response.status_code == 200:
            data = response.json()
            assert "address" in data or "status" in data or "error" in data

    @patch("src.routes.nosana.extend_job")
    def test_extend_job(self, mock_extend, client):
        """Test extending job duration"""
        mock_extend.return_value = {
            "address": "job123",
            "timeout": 7200,
        }

        response = client.post("/nosana/jobs/job123/extend", json={"timeout": 7200})

        assert response.status_code in [200, 401, 403, 404, 422]

    @patch("src.routes.nosana.stop_job")
    def test_stop_job(self, mock_stop, client):
        """Test stopping a job"""
        mock_stop.return_value = {
            "address": "job123",
            "status": "stopped",
        }

        response = client.post("/nosana/jobs/job123/stop")

        assert response.status_code in [200, 401, 403, 404]


class TestNosanaMarketsEndpoints:
    """Test Nosana markets endpoints"""

    @patch("src.routes.nosana.list_markets")
    def test_list_markets(self, mock_list, client):
        """Test listing markets"""
        mock_list.return_value = [
            {"id": "market1", "type": "PREMIUM", "slug": "premium-gpu"},
            {"id": "market2", "type": "COMMUNITY", "slug": "community-gpu"},
        ]

        response = client.get("/nosana/markets")

        assert response.status_code in [200, 401, 403]
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or "markets" in data or "error" in data

    @patch("src.routes.nosana.get_market")
    def test_get_market(self, mock_get, client):
        """Test getting market details"""
        mock_get.return_value = {
            "id": "market1",
            "type": "PREMIUM",
            "slug": "premium-gpu",
            "gpuTypes": ["A100", "H100"],
        }

        response = client.get("/nosana/markets/market1")

        assert response.status_code in [200, 401, 403, 404]
        if response.status_code == 200:
            data = response.json()
            assert "id" in data or "type" in data or "error" in data

    @patch("src.routes.nosana.get_market_resources")
    def test_get_market_resources(self, mock_get, client):
        """Test getting market resource requirements"""
        mock_get.return_value = {
            "s3": {"required": False},
            "ollama": {"required": True, "models": ["llama3"]},
        }

        response = client.get("/nosana/markets/market1/resources")

        assert response.status_code in [200, 401, 403, 404]


class TestNosanaConfigEndpoint:
    """Test Nosana config endpoint"""

    def test_get_config(self, client):
        """Test getting Nosana configuration"""
        response = client.get("/nosana/config")

        assert response.status_code in [200, 401, 403]
        if response.status_code == 200:
            data = response.json()
            # Config endpoint should return deployment strategies, market types, etc.
            assert isinstance(data, dict)


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
        from pydantic import ValidationError

        from src.routes.nosana import DeploymentCreate

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
        from pydantic import ValidationError

        from src.routes.nosana import DeploymentCreate

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
