"""
Nosana client for GPU deployment and inference services.

Nosana provides a distributed GPU computing network for AI workloads.
- REST API Base: https://dashboard.k8s.prd.nos.ci/api
- TypeScript SDK: @nosana/sdk
- Authentication: Bearer token (nos_xxx_your_api_key)

Key Features:
- Deployment management (create, start, stop, archive)
- Job submission and monitoring
- GPU marketplace access
- Credit-based billing

API Documentation: https://learn.nosana.com/api
Swagger UI: https://dashboard.k8s.prd.nos.ci/api/swagger
"""

import logging
from typing import Any

import httpx

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_nosana_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)

# Nosana API Base URL
NOSANA_API_BASE_URL = "https://dashboard.k8s.prd.nos.ci/api"

# Deployment statuses
DEPLOYMENT_STATUSES = [
    "DRAFT",
    "ERROR",
    "STARTING",
    "RUNNING",
    "STOPPING",
    "STOPPED",
    "INSUFFICIENT_FUNDS",
    "ARCHIVED",
]

# Deployment strategies
DEPLOYMENT_STRATEGIES = ["SIMPLE", "SIMPLE-EXTEND", "INFINITE", "SCHEDULED"]


def get_nosana_client():
    """Get Nosana client with connection pooling for better performance

    Nosana provides OpenAI-compatible API endpoints for various AI models
    deployed on their distributed GPU network.
    """
    try:
        if not Config.NOSANA_API_KEY:
            raise ValueError("Nosana API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_nosana_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Nosana client: {e}")
        raise


def get_nosana_http_client() -> httpx.Client:
    """Get HTTP client for Nosana REST API calls (non-OpenAI compatible endpoints)"""
    if not Config.NOSANA_API_KEY:
        raise ValueError("Nosana API key not configured")

    return httpx.Client(
        base_url=NOSANA_API_BASE_URL,
        headers={
            "Authorization": f"Bearer {Config.NOSANA_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
    )


async def get_nosana_async_http_client() -> httpx.AsyncClient:
    """Get async HTTP client for Nosana REST API calls"""
    if not Config.NOSANA_API_KEY:
        raise ValueError("Nosana API key not configured")

    return httpx.AsyncClient(
        base_url=NOSANA_API_BASE_URL,
        headers={
            "Authorization": f"Bearer {Config.NOSANA_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
    )


def make_nosana_request_openai(messages, model, **kwargs):
    """Make request to Nosana using OpenAI-compatible client

    Args:
        messages: List of message objects
        model: Model name to use (e.g., "meta-llama/Llama-3.3-70B-Instruct")
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Nosana request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_nosana_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)

        logger.info(f"Nosana request successful for model: {model}")
        return response
    except Exception as e:
        try:
            logger.error(f"Nosana request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Nosana request failed (encoding error in logging)")
        raise


def make_nosana_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Nosana using OpenAI-compatible client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        logger.info(f"Making Nosana streaming request with model: {model}")
        logger.debug(f"Request params: message_count={len(messages)}, kwargs={list(kwargs.keys())}")

        client = get_nosana_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )

        logger.info(f"Nosana streaming request initiated for model: {model}")
        return stream
    except Exception as e:
        try:
            logger.error(f"Nosana streaming request failed for model '{model}': {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, "response"):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'N/A')}")
        except UnicodeEncodeError:
            logger.error("Nosana streaming request failed (encoding error in logging)")
        raise


def process_nosana_response(response):
    """Process Nosana response to extract relevant data"""
    try:
        choices = []
        for choice in response.choices:
            msg = extract_message_with_tools(choice.message)

            choices.append(
                {
                    "index": choice.index,
                    "message": msg,
                    "finish_reason": choice.finish_reason,
                }
            )

        return {
            "id": response.id,
            "object": response.object,
            "created": response.created,
            "model": response.model,
            "choices": choices,
            "usage": (
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else {}
            ),
        }
    except Exception as e:
        logger.error(f"Failed to process Nosana response: {e}")
        raise


# =============================================================================
# NOSANA DEPLOYMENT API
# =============================================================================


async def get_credits_balance() -> dict[str, Any]:
    """Get the current credit balance for the authenticated user.

    Returns:
        dict: Credit balance with assignedCredits, reservedCredits, settledCredits (USD-denominated)
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.get("/credits/balance")
        response.raise_for_status()
        return response.json()


async def list_deployments() -> list[dict[str, Any]]:
    """List all deployments for the authenticated user.

    Returns:
        list: List of deployment objects
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.get("/deployments")
        response.raise_for_status()
        return response.json()


async def get_deployment(deployment_id: str) -> dict[str, Any]:
    """Get a specific deployment by ID.

    Args:
        deployment_id: The deployment ID

    Returns:
        dict: Deployment details including status, jobs, endpoints
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.get(f"/deployments/{deployment_id}")
        response.raise_for_status()
        return response.json()


async def create_deployment(
    name: str,
    market: str,
    job_definition: dict[str, Any],
    timeout: int = 3600,
    replicas: int = 1,
    strategy: str = "SIMPLE",
) -> dict[str, Any]:
    """Create a new deployment.

    Args:
        name: Unique identifier for the deployment
        market: GPU market address
        job_definition: Container workload specification
        timeout: Duration in seconds (60-360000)
        replicas: Number of instances
        strategy: Deployment strategy (SIMPLE, SIMPLE-EXTEND, INFINITE, SCHEDULED)

    Returns:
        dict: Created deployment object with ID
    """
    if strategy not in DEPLOYMENT_STRATEGIES:
        raise ValueError(f"Invalid strategy: {strategy}. Must be one of {DEPLOYMENT_STRATEGIES}")

    if not 60 <= timeout <= 360000:
        raise ValueError("Timeout must be between 60 and 360000 seconds")

    if replicas < 1:
        raise ValueError("Replicas must be at least 1")

    payload = {
        "name": name,
        "market": market,
        "timeout": timeout,
        "replicas": replicas,
        "strategy": strategy,
        "job_definition": job_definition,
    }

    async with await get_nosana_async_http_client() as client:
        response = await client.post("/deployments/create", json=payload)
        response.raise_for_status()
        return response.json()


async def start_deployment(deployment_id: str) -> dict[str, Any]:
    """Start a deployment (activate from draft or stopped state).

    Args:
        deployment_id: The deployment ID to start

    Returns:
        dict: Updated deployment object
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.post(f"/deployments/{deployment_id}/start")
        response.raise_for_status()
        return response.json()


async def stop_deployment(deployment_id: str) -> dict[str, Any]:
    """Stop a running deployment.

    Args:
        deployment_id: The deployment ID to stop

    Returns:
        dict: Updated deployment object
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.post(f"/deployments/{deployment_id}/stop")
        response.raise_for_status()
        return response.json()


async def archive_deployment(deployment_id: str) -> dict[str, Any]:
    """Archive a deployment (preserve history, remove from active list).

    Args:
        deployment_id: The deployment ID to archive

    Returns:
        dict: Updated deployment object
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.post(f"/deployments/{deployment_id}/archive")
        response.raise_for_status()
        return response.json()


async def update_deployment_replicas(deployment_id: str, replica_count: int) -> dict[str, Any]:
    """Update the replica count for a deployment.

    Args:
        deployment_id: The deployment ID
        replica_count: New number of replicas

    Returns:
        dict: Updated deployment object
    """
    if replica_count < 1:
        raise ValueError("Replica count must be at least 1")

    async with await get_nosana_async_http_client() as client:
        response = await client.patch(
            f"/deployments/{deployment_id}/update-replica-count",
            json={"replicaCount": replica_count},
        )
        response.raise_for_status()
        return response.json()


async def create_deployment_revision(
    deployment_id: str, job_definition: dict[str, Any]
) -> dict[str, Any]:
    """Create a new revision for a deployment with updated job definition.

    Args:
        deployment_id: The deployment ID
        job_definition: New container workload specification

    Returns:
        dict: Updated deployment object with new revision
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.post(
            f"/deployments/{deployment_id}/create-revision",
            json={"jobDefinition": job_definition},
        )
        response.raise_for_status()
        return response.json()


# =============================================================================
# NOSANA JOBS API
# =============================================================================


async def create_job(
    ipfs_job: str,
    market: str,
    timeout: int = 3600,
) -> dict[str, Any]:
    """Create a job using credits.

    Args:
        ipfs_job: IPFS hash of the job definition
        market: Market address to submit the job to
        timeout: Job timeout in seconds (60-360000)

    Returns:
        dict: Job creation response with tx, job, run, and credits info
    """
    if not 60 <= timeout <= 360000:
        raise ValueError("Timeout must be between 60 and 360000 seconds")

    payload = {
        "ipfsJob": ipfs_job,
        "market": market,
        "timeout": timeout,
    }

    async with await get_nosana_async_http_client() as client:
        response = await client.post("/jobs/create-with-credits", json=payload)
        response.raise_for_status()
        return response.json()


async def get_job(job_address: str) -> dict[str, Any]:
    """Get job details by address.

    Args:
        job_address: The job address

    Returns:
        dict: Job details including definition, results, and status
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.get(f"/jobs/{job_address}")
        response.raise_for_status()
        return response.json()


async def extend_job(job_address: str, timeout: int) -> dict[str, Any]:
    """Extend a job's duration.

    Args:
        job_address: The job address
        timeout: Additional time in seconds (minimum 1)

    Returns:
        dict: Updated job object
    """
    if timeout < 1:
        raise ValueError("Timeout extension must be at least 1 second")

    async with await get_nosana_async_http_client() as client:
        response = await client.post(
            f"/jobs/{job_address}/extend",
            json={"timeout": timeout},
        )
        response.raise_for_status()
        return response.json()


async def stop_job(job_address: str) -> dict[str, Any]:
    """Stop a credit-based job.

    Args:
        job_address: The job address to stop

    Returns:
        dict: Updated job object
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.post(f"/jobs/{job_address}/stop")
        response.raise_for_status()
        return response.json()


# =============================================================================
# NOSANA MARKETS API
# =============================================================================


async def list_markets(market_type: str | None = None) -> list[dict[str, Any]]:
    """List available GPU markets.

    Args:
        market_type: Optional filter by type (PREMIUM, COMMUNITY, OTHER)

    Returns:
        list: List of market objects with GPU types, reward rates, etc.
    """
    params = {}
    if market_type:
        if market_type not in ["PREMIUM", "COMMUNITY", "OTHER"]:
            raise ValueError("Invalid market type. Must be PREMIUM, COMMUNITY, or OTHER")
        params["type"] = market_type

    async with await get_nosana_async_http_client() as client:
        response = await client.get("/markets/", params=params)
        response.raise_for_status()
        return response.json()


async def get_market(market_id: str) -> dict[str, Any]:
    """Get specific market details.

    Args:
        market_id: The market ID

    Returns:
        dict: Market details including address, slug, GPU types, reward rates
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.get(f"/markets/{market_id}")
        response.raise_for_status()
        return response.json()


async def get_market_resources(market_id: str) -> dict[str, Any]:
    """Get required resources for a market.

    Args:
        market_id: The market ID

    Returns:
        dict: Required resources including S3, Ollama, HuggingFace configs
    """
    async with await get_nosana_async_http_client() as client:
        response = await client.get(f"/markets/{market_id}/required-resources")
        response.raise_for_status()
        return response.json()


# =============================================================================
# JOB DEFINITION BUILDERS
# =============================================================================


def build_container_job_definition(
    image: str,
    cmd: list[str] | None = None,
    env: dict[str, str] | None = None,
    gpu: bool = True,
    expose: list[dict[str, Any]] | None = None,
    volumes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a container job definition for Nosana.

    Args:
        image: Docker image to run
        cmd: Command to execute
        env: Environment variables
        gpu: Whether to enable GPU access
        expose: Port exposures (type: web|api|websocket|webapi|none)
        volumes: Volume mounts

    Returns:
        dict: Job definition ready for deployment
    """
    container_op = {
        "type": "container/run",
        "id": "main",
        "args": {
            "image": image,
            "gpu": gpu,
        },
    }

    if cmd:
        container_op["args"]["cmd"] = cmd

    if env:
        container_op["args"]["env"] = env

    if expose:
        container_op["args"]["expose"] = expose

    if volumes:
        container_op["args"]["volumes"] = volumes

    return {
        "version": "0.1",
        "type": "container",
        "meta": {
            "trigger": "cli",
        },
        "ops": [container_op],
    }


def build_llm_inference_job_definition(
    model: str,
    framework: str = "vllm",
    port: int = 8000,
    max_model_len: int | None = None,
    tensor_parallel_size: int = 1,
) -> dict[str, Any]:
    """Build a job definition for LLM inference.

    Args:
        model: HuggingFace model ID or path
        framework: Inference framework (vllm, ollama, lmdeploy)
        port: API port to expose
        max_model_len: Maximum model context length
        tensor_parallel_size: Number of GPUs for tensor parallelism

    Returns:
        dict: Job definition for LLM inference
    """
    if framework == "vllm":
        image = "vllm/vllm-openai:latest"
        cmd = [
            "--model",
            model,
            "--port",
            str(port),
            "--tensor-parallel-size",
            str(tensor_parallel_size),
        ]
        if max_model_len:
            cmd.extend(["--max-model-len", str(max_model_len)])

    elif framework == "ollama":
        image = "ollama/ollama:latest"
        cmd = ["serve"]
        # Ollama uses default port 11434

    elif framework == "lmdeploy":
        image = "openmmlab/lmdeploy:latest"
        cmd = [
            "lmdeploy",
            "serve",
            "api_server",
            model,
            "--server-port",
            str(port),
            "--tp",
            str(tensor_parallel_size),
        ]

    else:
        raise ValueError(f"Unsupported framework: {framework}. Use vllm, ollama, or lmdeploy")

    return build_container_job_definition(
        image=image,
        cmd=cmd,
        gpu=True,
        expose=[{"port": port, "type": "api"}],
    )


def build_stable_diffusion_job_definition(
    model: str = "stabilityai/stable-diffusion-xl-base-1.0",
    port: int = 7860,
) -> dict[str, Any]:
    """Build a job definition for Stable Diffusion WebUI.

    Args:
        model: Model checkpoint to use
        port: WebUI port to expose

    Returns:
        dict: Job definition for Stable Diffusion
    """
    return build_container_job_definition(
        image="sd-webui/stable-diffusion-webui:latest",
        cmd=[
            "--api",
            "--listen",
            "--port",
            str(port),
            "--ckpt",
            model,
        ],
        gpu=True,
        expose=[{"port": port, "type": "webapi"}],
    )


def build_whisper_job_definition(
    model: str = "large-v3",
    port: int = 9000,
) -> dict[str, Any]:
    """Build a job definition for Whisper transcription.

    Args:
        model: Whisper model size (tiny, base, small, medium, large, large-v2, large-v3)
        port: API port to expose

    Returns:
        dict: Job definition for Whisper
    """
    return build_container_job_definition(
        image="onerahmet/openai-whisper-asr-webservice:latest",
        cmd=["--model", model],
        env={"ASR_MODEL": model},
        gpu=True,
        expose=[{"port": port, "type": "api"}],
    )
