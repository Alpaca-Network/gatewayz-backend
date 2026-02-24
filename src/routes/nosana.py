"""
Nosana GPU Computing Network Routes

Provides REST API endpoints for managing Nosana deployments, jobs, and GPU marketplace.

Endpoints:
- /nosana/credits - Credit balance management
- /nosana/deployments - Deployment CRUD operations
- /nosana/jobs - Job submission and monitoring
- /nosana/markets - GPU marketplace access
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.security.deps import get_current_user
from src.services.nosana_client import (
    DEPLOYMENT_STATUSES,
    DEPLOYMENT_STRATEGIES,
    archive_deployment,
    build_llm_inference_job_definition,
    build_stable_diffusion_job_definition,
    build_whisper_job_definition,
    create_deployment,
    create_deployment_revision,
    create_job,
    extend_job,
    get_credits_balance,
    get_deployment,
    get_job,
    get_market,
    get_market_resources,
    list_deployments,
    list_markets,
    start_deployment,
    stop_deployment,
    stop_job,
    update_deployment_replicas,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nosana", tags=["nosana"])


# =============================================================================
# PYDANTIC MODELS
# =============================================================================


class CreditsBalanceResponse(BaseModel):
    """Response model for credit balance"""

    assigned_credits: float = Field(..., alias="assignedCredits")
    reserved_credits: float = Field(..., alias="reservedCredits")
    settled_credits: float = Field(..., alias="settledCredits")

    class Config:
        populate_by_name = True


class DeploymentCreate(BaseModel):
    """Request model for creating a deployment"""

    name: str = Field(..., description="Unique identifier for the deployment")
    market: str = Field(..., description="GPU market address")
    job_definition: dict[str, Any] = Field(..., description="Container workload specification")
    timeout: int = Field(3600, ge=60, le=360000, description="Duration in seconds")
    replicas: int = Field(1, ge=1, description="Number of instances")
    strategy: str = Field("SIMPLE", description="Deployment strategy")


class DeploymentReplicaUpdate(BaseModel):
    """Request model for updating deployment replicas"""

    replica_count: int = Field(..., ge=1, description="New number of replicas")


class DeploymentRevisionCreate(BaseModel):
    """Request model for creating a deployment revision"""

    job_definition: dict[str, Any] = Field(..., description="New container workload specification")


class JobCreate(BaseModel):
    """Request model for creating a job"""

    ipfs_job: str = Field(..., description="IPFS hash of the job definition")
    market: str = Field(..., description="Market address")
    timeout: int = Field(3600, ge=60, le=360000, description="Job timeout in seconds")


class JobExtend(BaseModel):
    """Request model for extending a job"""

    timeout: int = Field(..., ge=1, description="Additional time in seconds")


class LLMInferenceJobCreate(BaseModel):
    """Request model for creating an LLM inference deployment"""

    name: str = Field(..., description="Deployment name")
    market: str = Field(..., description="GPU market address")
    model: str = Field(..., description="HuggingFace model ID or path")
    framework: str = Field("vllm", description="Inference framework (vllm, ollama, lmdeploy)")
    port: int = Field(8000, description="API port to expose")
    max_model_len: int | None = Field(None, description="Maximum model context length")
    tensor_parallel_size: int = Field(1, ge=1, description="Number of GPUs for tensor parallelism")
    timeout: int = Field(3600, ge=60, le=360000, description="Deployment timeout in seconds")
    replicas: int = Field(1, ge=1, description="Number of instances")


class ImageGenerationJobCreate(BaseModel):
    """Request model for creating an image generation deployment"""

    name: str = Field(..., description="Deployment name")
    market: str = Field(..., description="GPU market address")
    model: str = Field("stabilityai/stable-diffusion-xl-base-1.0", description="Model checkpoint")
    port: int = Field(7860, description="WebUI port to expose")
    timeout: int = Field(3600, ge=60, le=360000, description="Deployment timeout in seconds")
    replicas: int = Field(1, ge=1, description="Number of instances")


class WhisperJobCreate(BaseModel):
    """Request model for creating a Whisper transcription deployment"""

    name: str = Field(..., description="Deployment name")
    market: str = Field(..., description="GPU market address")
    model: str = Field("large-v3", description="Whisper model size")
    port: int = Field(9000, description="API port to expose")
    timeout: int = Field(3600, ge=60, le=360000, description="Deployment timeout in seconds")
    replicas: int = Field(1, ge=1, description="Number of instances")


# =============================================================================
# CREDITS ENDPOINTS
# =============================================================================


@router.get("/credits/balance", response_model=CreditsBalanceResponse)
async def get_credit_balance(current_user: dict = Depends(get_current_user)):
    """Get the current credit balance for the Nosana account."""
    try:
        balance = await get_credits_balance()
        return balance
    except Exception as e:
        logger.error(f"Failed to get Nosana credit balance: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get credit balance: {str(e)}",
        )


# =============================================================================
# DEPLOYMENT ENDPOINTS
# =============================================================================


@router.get("/deployments")
async def list_all_deployments(current_user: dict = Depends(get_current_user)):
    """List all deployments for the authenticated user."""
    try:
        deployments = await list_deployments()
        return {"deployments": deployments}
    except Exception as e:
        logger.error(f"Failed to list Nosana deployments: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list deployments: {str(e)}",
        )


@router.get("/deployments/{deployment_id}")
async def get_deployment_details(
    deployment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get details for a specific deployment."""
    try:
        deployment = await get_deployment(deployment_id)
        return deployment
    except Exception as e:
        logger.error(f"Failed to get Nosana deployment {deployment_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get deployment: {str(e)}",
        )


@router.post("/deployments", status_code=status.HTTP_201_CREATED)
async def create_new_deployment(
    deployment: DeploymentCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new deployment."""
    try:
        if deployment.strategy not in DEPLOYMENT_STRATEGIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid strategy. Must be one of: {DEPLOYMENT_STRATEGIES}",
            )

        result = await create_deployment(
            name=deployment.name,
            market=deployment.market,
            job_definition=deployment.job_definition,
            timeout=deployment.timeout,
            replicas=deployment.replicas,
            strategy=deployment.strategy,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create Nosana deployment: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create deployment: {str(e)}",
        )


@router.post("/deployments/{deployment_id}/start")
async def start_existing_deployment(
    deployment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Start a deployment (activate from draft or stopped state)."""
    try:
        result = await start_deployment(deployment_id)
        return result
    except Exception as e:
        logger.error(f"Failed to start Nosana deployment {deployment_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start deployment: {str(e)}",
        )


@router.post("/deployments/{deployment_id}/stop")
async def stop_running_deployment(
    deployment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Stop a running deployment."""
    try:
        result = await stop_deployment(deployment_id)
        return result
    except Exception as e:
        logger.error(f"Failed to stop Nosana deployment {deployment_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop deployment: {str(e)}",
        )


@router.post("/deployments/{deployment_id}/archive")
async def archive_existing_deployment(
    deployment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Archive a deployment."""
    try:
        result = await archive_deployment(deployment_id)
        return result
    except Exception as e:
        logger.error(f"Failed to archive Nosana deployment {deployment_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to archive deployment: {str(e)}",
        )


@router.patch("/deployments/{deployment_id}/replicas")
async def update_replicas(
    deployment_id: str,
    update: DeploymentReplicaUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update the replica count for a deployment."""
    try:
        result = await update_deployment_replicas(deployment_id, update.replica_count)
        return result
    except Exception as e:
        logger.error(f"Failed to update Nosana deployment replicas {deployment_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update replicas: {str(e)}",
        )


@router.post("/deployments/{deployment_id}/revisions")
async def create_revision(
    deployment_id: str,
    revision: DeploymentRevisionCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new revision for a deployment."""
    try:
        result = await create_deployment_revision(deployment_id, revision.job_definition)
        return result
    except Exception as e:
        logger.error(f"Failed to create Nosana deployment revision {deployment_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create revision: {str(e)}",
        )


# =============================================================================
# QUICK DEPLOY ENDPOINTS (Convenience methods)
# =============================================================================


@router.post("/deployments/llm", status_code=status.HTTP_201_CREATED)
async def deploy_llm_inference(
    config: LLMInferenceJobCreate,
    current_user: dict = Depends(get_current_user),
):
    """Quick deploy an LLM inference endpoint.

    Automatically configures the job definition for the specified framework
    (vLLM, Ollama, or LMDeploy).
    """
    try:
        job_definition = build_llm_inference_job_definition(
            model=config.model,
            framework=config.framework,
            port=config.port,
            max_model_len=config.max_model_len,
            tensor_parallel_size=config.tensor_parallel_size,
        )

        result = await create_deployment(
            name=config.name,
            market=config.market,
            job_definition=job_definition,
            timeout=config.timeout,
            replicas=config.replicas,
            strategy="SIMPLE",
        )

        # Auto-start the deployment
        deployment_id = result.get("id")
        if deployment_id:
            await start_deployment(deployment_id)

        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to deploy LLM inference: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deploy LLM inference: {str(e)}",
        )


@router.post("/deployments/image-generation", status_code=status.HTTP_201_CREATED)
async def deploy_image_generation(
    config: ImageGenerationJobCreate,
    current_user: dict = Depends(get_current_user),
):
    """Quick deploy a Stable Diffusion image generation endpoint."""
    try:
        job_definition = build_stable_diffusion_job_definition(
            model=config.model,
            port=config.port,
        )

        result = await create_deployment(
            name=config.name,
            market=config.market,
            job_definition=job_definition,
            timeout=config.timeout,
            replicas=config.replicas,
            strategy="SIMPLE",
        )

        # Auto-start the deployment
        deployment_id = result.get("id")
        if deployment_id:
            await start_deployment(deployment_id)

        return result
    except Exception as e:
        logger.error(f"Failed to deploy image generation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deploy image generation: {str(e)}",
        )


@router.post("/deployments/whisper", status_code=status.HTTP_201_CREATED)
async def deploy_whisper_transcription(
    config: WhisperJobCreate,
    current_user: dict = Depends(get_current_user),
):
    """Quick deploy a Whisper audio transcription endpoint."""
    try:
        job_definition = build_whisper_job_definition(
            model=config.model,
            port=config.port,
        )

        result = await create_deployment(
            name=config.name,
            market=config.market,
            job_definition=job_definition,
            timeout=config.timeout,
            replicas=config.replicas,
            strategy="SIMPLE",
        )

        # Auto-start the deployment
        deployment_id = result.get("id")
        if deployment_id:
            await start_deployment(deployment_id)

        return result
    except Exception as e:
        logger.error(f"Failed to deploy Whisper transcription: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to deploy Whisper transcription: {str(e)}",
        )


# =============================================================================
# JOBS ENDPOINTS
# =============================================================================


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def create_new_job(
    job: JobCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new job using credits."""
    try:
        result = await create_job(
            ipfs_job=job.ipfs_job,
            market=job.market,
            timeout=job.timeout,
        )
        return result
    except Exception as e:
        logger.error(f"Failed to create Nosana job: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create job: {str(e)}",
        )


@router.get("/jobs/{job_address}")
async def get_job_details(
    job_address: str,
    current_user: dict = Depends(get_current_user),
):
    """Get details for a specific job."""
    try:
        job = await get_job(job_address)
        return job
    except Exception as e:
        logger.error(f"Failed to get Nosana job {job_address}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job: {str(e)}",
        )


@router.post("/jobs/{job_address}/extend")
async def extend_job_duration(
    job_address: str,
    extend: JobExtend,
    current_user: dict = Depends(get_current_user),
):
    """Extend a job's duration."""
    try:
        result = await extend_job(job_address, extend.timeout)
        return result
    except Exception as e:
        logger.error(f"Failed to extend Nosana job {job_address}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extend job: {str(e)}",
        )


@router.post("/jobs/{job_address}/stop")
async def stop_running_job(
    job_address: str,
    current_user: dict = Depends(get_current_user),
):
    """Stop a credit-based job."""
    try:
        result = await stop_job(job_address)
        return result
    except Exception as e:
        logger.error(f"Failed to stop Nosana job {job_address}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop job: {str(e)}",
        )


# =============================================================================
# MARKETS ENDPOINTS
# =============================================================================


@router.get("/markets")
async def list_gpu_markets(
    market_type: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """List available GPU markets.

    Args:
        market_type: Optional filter by type (PREMIUM, COMMUNITY, OTHER)
    """
    try:
        markets = await list_markets(market_type)
        return {"markets": markets}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to list Nosana markets: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list markets: {str(e)}",
        )


@router.get("/markets/{market_id}")
async def get_market_details(
    market_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get details for a specific market."""
    try:
        market = await get_market(market_id)
        return market
    except Exception as e:
        logger.error(f"Failed to get Nosana market {market_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get market: {str(e)}",
        )


@router.get("/markets/{market_id}/resources")
async def get_market_resource_requirements(
    market_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get required resources for a specific market."""
    try:
        resources = await get_market_resources(market_id)
        return resources
    except Exception as e:
        logger.error(f"Failed to get Nosana market resources {market_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get market resources: {str(e)}",
        )


# =============================================================================
# UTILITY ENDPOINTS
# =============================================================================


@router.get("/config")
async def get_nosana_config(current_user: dict = Depends(get_current_user)):
    """Get Nosana configuration options."""
    return {
        "deployment_strategies": DEPLOYMENT_STRATEGIES,
        "deployment_statuses": DEPLOYMENT_STATUSES,
        "market_types": ["PREMIUM", "COMMUNITY", "OTHER"],
        "supported_frameworks": {
            "llm": ["vllm", "ollama", "lmdeploy"],
            "image": ["stable-diffusion-webui"],
            "audio": ["whisper"],
        },
        "api_docs": "https://learn.nosana.com/api",
        "swagger_ui": "https://dashboard.k8s.prd.nos.ci/api/swagger",
    }
