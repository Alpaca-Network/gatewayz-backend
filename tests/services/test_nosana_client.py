"""Tests for Nosana GPU Computing Network client"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.services.nosana_client import (
    DEPLOYMENT_STATUSES,
    DEPLOYMENT_STRATEGIES,
    build_container_job_definition,
    build_llm_inference_job_definition,
    build_stable_diffusion_job_definition,
    build_whisper_job_definition,
    create_deployment,
    create_job,
    extend_job,
    get_credits_balance,
    get_market,
    get_nosana_client,
    list_deployments,
    list_markets,
    make_nosana_request_openai,
    make_nosana_request_openai_stream,
    process_nosana_response,
)


class TestNosanaClient:
    """Test Nosana client functionality"""

    @patch("src.services.nosana_client.Config.NOSANA_API_KEY", "test_key")
    def test_get_nosana_client(self):
        """Test getting Nosana client"""
        client = get_nosana_client()
        assert client is not None
        assert "nos" in str(client.base_url).lower() or "dashboard" in str(client.base_url).lower()

    @patch("src.services.nosana_client.Config.NOSANA_API_KEY", None)
    def test_get_nosana_client_no_key(self):
        """Test getting Nosana client without API key"""
        with pytest.raises(ValueError, match="Nosana API key not configured"):
            get_nosana_client()

    @patch("src.services.nosana_client.get_nosana_client")
    def test_make_nosana_request_openai(self, mock_get_client):
        """Test making request to Nosana"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "meta-llama/Llama-3.3-70B-Instruct"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_nosana_request_openai(messages, "meta-llama/Llama-3.3-70B-Instruct")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.nosana_client.get_nosana_client")
    def test_make_nosana_request_openai_stream(self, mock_get_client):
        """Test making streaming request to Nosana"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_nosana_request_openai_stream(messages, "meta-llama/Llama-3.3-70B-Instruct")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="meta-llama/Llama-3.3-70B-Instruct",
            messages=messages,
            stream=True,
        )

    def test_process_nosana_response(self):
        """Test processing Nosana response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "meta-llama/Llama-3.3-70B-Instruct"

        # Mock choice
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test response"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        # Mock usage
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30

        processed = process_nosana_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "meta-llama/Llama-3.3-70B-Instruct"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30

    def test_process_nosana_response_no_usage(self):
        """Test processing Nosana response without usage data"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "meta-llama/Llama-3.3-70B-Instruct"

        # Mock choice
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test response"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        # No usage data
        mock_response.usage = None

        processed = process_nosana_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["usage"] == {}

    @patch("src.services.nosana_client.get_nosana_client")
    def test_make_nosana_request_with_kwargs(self, mock_get_client):
        """Test making request to Nosana with additional parameters"""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_nosana_request_openai(
            messages,
            "meta-llama/Llama-3.3-70B-Instruct",
            temperature=0.7,
            max_tokens=1024,
        )

        assert response is not None
        mock_client.chat.completions.create.assert_called_once_with(
            model="meta-llama/Llama-3.3-70B-Instruct",
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
        )


class TestNosanaJobDefinitionBuilders:
    """Test Nosana job definition builders"""

    def test_build_container_job_definition_basic(self):
        """Test building a basic container job definition"""
        job_def = build_container_job_definition(
            image="vllm/vllm-openai:latest",
            gpu=True,
        )

        assert job_def["version"] == "0.1"
        assert job_def["type"] == "container"
        assert job_def["meta"]["trigger"] == "cli"
        assert len(job_def["ops"]) == 1
        assert job_def["ops"][0]["type"] == "container/run"
        assert job_def["ops"][0]["args"]["image"] == "vllm/vllm-openai:latest"
        assert job_def["ops"][0]["args"]["gpu"] is True

    def test_build_container_job_definition_with_all_options(self):
        """Test building a container job definition with all options"""
        job_def = build_container_job_definition(
            image="custom/image:v1",
            cmd=["python", "app.py"],
            env={"API_KEY": "test"},
            gpu=True,
            expose=[{"port": 8000, "type": "api"}],
            volumes=[{"name": "data", "path": "/data"}],
        )

        assert job_def["ops"][0]["args"]["cmd"] == ["python", "app.py"]
        assert job_def["ops"][0]["args"]["env"] == {"API_KEY": "test"}
        assert job_def["ops"][0]["args"]["expose"] == [{"port": 8000, "type": "api"}]
        assert job_def["ops"][0]["args"]["volumes"] == [{"name": "data", "path": "/data"}]

    def test_build_llm_inference_job_definition_vllm(self):
        """Test building vLLM inference job definition"""
        job_def = build_llm_inference_job_definition(
            model="meta-llama/Llama-3.1-8B-Instruct",
            framework="vllm",
            port=8000,
            tensor_parallel_size=1,
        )

        assert job_def["ops"][0]["args"]["image"] == "vllm/vllm-openai:latest"
        assert "--model" in job_def["ops"][0]["args"]["cmd"]
        assert "meta-llama/Llama-3.1-8B-Instruct" in job_def["ops"][0]["args"]["cmd"]
        assert job_def["ops"][0]["args"]["gpu"] is True

    def test_build_llm_inference_job_definition_ollama(self):
        """Test building Ollama inference job definition"""
        job_def = build_llm_inference_job_definition(
            model="llama3",
            framework="ollama",
        )

        assert job_def["ops"][0]["args"]["image"] == "ollama/ollama:latest"
        assert job_def["ops"][0]["args"]["cmd"] == ["serve"]

    def test_build_llm_inference_job_definition_lmdeploy(self):
        """Test building LMDeploy inference job definition"""
        job_def = build_llm_inference_job_definition(
            model="meta-llama/Llama-3.1-8B-Instruct",
            framework="lmdeploy",
            port=8080,
            tensor_parallel_size=2,
        )

        assert job_def["ops"][0]["args"]["image"] == "openmmlab/lmdeploy:latest"
        assert "lmdeploy" in job_def["ops"][0]["args"]["cmd"]
        assert "--tp" in job_def["ops"][0]["args"]["cmd"]
        assert "2" in job_def["ops"][0]["args"]["cmd"]

    def test_build_llm_inference_job_definition_invalid_framework(self):
        """Test building LLM inference with invalid framework"""
        with pytest.raises(ValueError, match="Unsupported framework"):
            build_llm_inference_job_definition(
                model="test/model",
                framework="invalid",
            )

    def test_build_stable_diffusion_job_definition(self):
        """Test building Stable Diffusion job definition"""
        job_def = build_stable_diffusion_job_definition(
            model="stabilityai/stable-diffusion-xl-base-1.0",
            port=7860,
        )

        assert job_def["ops"][0]["args"]["image"] == "sd-webui/stable-diffusion-webui:latest"
        assert "--api" in job_def["ops"][0]["args"]["cmd"]
        assert job_def["ops"][0]["args"]["gpu"] is True
        assert job_def["ops"][0]["args"]["expose"][0]["port"] == 7860
        assert job_def["ops"][0]["args"]["expose"][0]["type"] == "webapi"

    def test_build_whisper_job_definition(self):
        """Test building Whisper job definition"""
        job_def = build_whisper_job_definition(
            model="large-v3",
            port=9000,
        )

        assert "whisper" in job_def["ops"][0]["args"]["image"].lower()
        assert job_def["ops"][0]["args"]["env"]["ASR_MODEL"] == "large-v3"
        assert job_def["ops"][0]["args"]["gpu"] is True


class TestNosanaDeploymentAPI:
    """Test Nosana deployment API functions"""

    @pytest.mark.asyncio
    @patch("src.services.nosana_client.get_nosana_async_http_client")
    async def test_get_credits_balance(self, mock_get_client):
        """Test getting credit balance"""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "assignedCredits": 100.0,
            "reservedCredits": 10.0,
            "settledCredits": 90.0,
        }
        mock_response.raise_for_status = Mock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_get_client.return_value = mock_client

        balance = await get_credits_balance()

        assert balance["assignedCredits"] == 100.0
        assert balance["reservedCredits"] == 10.0
        assert balance["settledCredits"] == 90.0

    @pytest.mark.asyncio
    @patch("src.services.nosana_client.get_nosana_async_http_client")
    async def test_list_deployments(self, mock_get_client):
        """Test listing deployments"""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": "dep1", "name": "test-deployment", "status": "RUNNING"},
        ]
        mock_response.raise_for_status = Mock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_get_client.return_value = mock_client

        deployments = await list_deployments()

        assert len(deployments) == 1
        assert deployments[0]["id"] == "dep1"
        assert deployments[0]["status"] == "RUNNING"

    @pytest.mark.asyncio
    @patch("src.services.nosana_client.get_nosana_async_http_client")
    async def test_create_deployment(self, mock_get_client):
        """Test creating a deployment"""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "dep1",
            "name": "test-deployment",
            "status": "DRAFT",
        }
        mock_response.raise_for_status = Mock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_get_client.return_value = mock_client

        deployment = await create_deployment(
            name="test-deployment",
            market="market123",
            job_definition={"version": "0.1", "type": "container", "ops": []},
            timeout=3600,
            replicas=1,
            strategy="SIMPLE",
        )

        assert deployment["id"] == "dep1"
        assert deployment["status"] == "DRAFT"

    @pytest.mark.asyncio
    async def test_create_deployment_invalid_strategy(self):
        """Test creating deployment with invalid strategy"""
        with pytest.raises(ValueError, match="Invalid strategy"):
            await create_deployment(
                name="test",
                market="market123",
                job_definition={},
                strategy="INVALID",
            )

    @pytest.mark.asyncio
    async def test_create_deployment_invalid_timeout(self):
        """Test creating deployment with invalid timeout"""
        with pytest.raises(ValueError, match="Timeout must be between"):
            await create_deployment(
                name="test",
                market="market123",
                job_definition={},
                timeout=10,  # Too short
            )

    @pytest.mark.asyncio
    async def test_create_deployment_invalid_replicas(self):
        """Test creating deployment with invalid replicas"""
        with pytest.raises(ValueError, match="Replicas must be at least"):
            await create_deployment(
                name="test",
                market="market123",
                job_definition={},
                replicas=0,
            )


class TestNosanaJobsAPI:
    """Test Nosana jobs API functions"""

    @pytest.mark.asyncio
    @patch("src.services.nosana_client.get_nosana_async_http_client")
    async def test_create_job(self, mock_get_client):
        """Test creating a job"""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "tx": "tx123",
            "job": "job123",
            "credits": {"costUSD": 1.5},
        }
        mock_response.raise_for_status = Mock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_get_client.return_value = mock_client

        result = await create_job(
            ipfs_job="QmHash123",
            market="market123",
            timeout=3600,
        )

        assert result["job"] == "job123"
        assert result["credits"]["costUSD"] == 1.5

    @pytest.mark.asyncio
    async def test_create_job_invalid_timeout(self):
        """Test creating job with invalid timeout"""
        with pytest.raises(ValueError, match="Timeout must be between"):
            await create_job(
                ipfs_job="QmHash123",
                market="market123",
                timeout=30,  # Too short
            )

    @pytest.mark.asyncio
    async def test_extend_job_invalid_timeout(self):
        """Test extending job with invalid timeout"""
        with pytest.raises(ValueError, match="Timeout extension must be"):
            await extend_job(
                job_address="job123",
                timeout=0,
            )


class TestNosanaMarketsAPI:
    """Test Nosana markets API functions"""

    @pytest.mark.asyncio
    @patch("src.services.nosana_client.get_nosana_async_http_client")
    async def test_list_markets(self, mock_get_client):
        """Test listing markets"""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": "market1", "type": "PREMIUM", "slug": "premium-gpu"},
            {"id": "market2", "type": "COMMUNITY", "slug": "community-gpu"},
        ]
        mock_response.raise_for_status = Mock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_get_client.return_value = mock_client

        markets = await list_markets()

        assert len(markets) == 2
        assert markets[0]["type"] == "PREMIUM"

    @pytest.mark.asyncio
    @patch("src.services.nosana_client.get_nosana_async_http_client")
    async def test_list_markets_with_filter(self, mock_get_client):
        """Test listing markets with type filter"""
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": "market1", "type": "PREMIUM", "slug": "premium-gpu"},
        ]
        mock_response.raise_for_status = Mock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_get_client.return_value = mock_client

        markets = await list_markets(market_type="PREMIUM")

        assert len(markets) == 1
        assert markets[0]["type"] == "PREMIUM"
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_markets_invalid_type(self):
        """Test listing markets with invalid type"""
        with pytest.raises(ValueError, match="Invalid market type"):
            await list_markets(market_type="INVALID")


class TestNosanaConstants:
    """Test Nosana constants"""

    def test_deployment_strategies(self):
        """Test deployment strategies are defined"""
        assert "SIMPLE" in DEPLOYMENT_STRATEGIES
        assert "SIMPLE-EXTEND" in DEPLOYMENT_STRATEGIES
        assert "INFINITE" in DEPLOYMENT_STRATEGIES
        assert "SCHEDULED" in DEPLOYMENT_STRATEGIES

    def test_deployment_statuses(self):
        """Test deployment statuses are defined"""
        assert "DRAFT" in DEPLOYMENT_STATUSES
        assert "RUNNING" in DEPLOYMENT_STATUSES
        assert "STOPPED" in DEPLOYMENT_STATUSES
        assert "ARCHIVED" in DEPLOYMENT_STATUSES
        assert "ERROR" in DEPLOYMENT_STATUSES
