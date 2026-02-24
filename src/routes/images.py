import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.config import Config
from src.db.api_keys import increment_api_key_usage
from src.db.model_health import record_model_call
from src.db.users import deduct_credits, get_user, record_usage
from src.models import ImageGenerationRequest, ImageGenerationResponse
from src.security.deps import get_api_key
from src.services.fal_image_client import make_fal_image_request
from src.services.image_generation_client import (
    make_deepinfra_image_request,
    make_google_vertex_image_request,
    process_image_generation_response,
)
from src.utils.ai_tracing import AIRequestType, AITracer
from src.utils.performance_tracker import PerformanceTracker

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Provider-specific image generation pricing (cost per image in USD)
# Based on provider pricing pages as of Jan 2025
# TODO: Move to database-driven pricing for easier updates
IMAGE_COST_PER_IMAGE = {
    "deepinfra": {
        "stable-diffusion-3.5-large": 0.035,
        "stable-diffusion-3.5-medium": 0.02,
        "stabilityai/sd3.5": 0.035,
        "stabilityai/sd3.5-large": 0.035,
        "stabilityai/sd3.5-medium": 0.02,
        "default": 0.025,
    },
    "fal": {
        "flux/schnell": 0.003,
        "flux/dev": 0.025,
        "flux-pro": 0.05,
        "fal-ai/flux/schnell": 0.003,
        "fal-ai/flux/dev": 0.025,
        "fal-ai/flux-pro": 0.05,
        "default": 0.025,
    },
    "google-vertex": {
        "imagegeneration@006": 0.02,
        "imagen-3.0-generate-001": 0.04,
        "default": 0.03,
    },
}

# Default fallback cost when provider is unknown - set conservatively high
# to avoid revenue loss on new expensive models
UNKNOWN_PROVIDER_DEFAULT_COST = 0.05


def get_image_cost(provider: str, model: str, num_images: int = 1) -> tuple[float, float, bool]:
    """
    Calculate the cost for image generation.

    Args:
        provider: Image generation provider (deepinfra, fal, google-vertex)
        model: Model name
        num_images: Number of images to generate

    Returns:
        Tuple of (total_cost, cost_per_image, is_fallback_pricing)
        is_fallback_pricing is True when using default/unknown pricing
    """
    provider_pricing = IMAGE_COST_PER_IMAGE.get(provider, {})
    is_fallback = False

    if model in provider_pricing:
        cost_per_image = provider_pricing[model]
    elif "default" in provider_pricing:
        cost_per_image = provider_pricing["default"]
        is_fallback = True
        logger.warning(
            f"Using default pricing for unknown model: provider={provider}, model={model}, "
            f"cost_per_image={cost_per_image}"
        )
    else:
        # Unknown provider - use conservative high default to avoid revenue loss
        cost_per_image = UNKNOWN_PROVIDER_DEFAULT_COST
        is_fallback = True
        logger.warning(
            f"Using fallback pricing for unknown provider: provider={provider}, model={model}, "
            f"cost_per_image={cost_per_image}"
        )

    total_cost = cost_per_image * num_images
    return total_cost, cost_per_image, is_fallback


@router.post("/images/generations", response_model=ImageGenerationResponse, tags=["images"])
async def generate_images(
    req: ImageGenerationRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
):
    """
    OpenAI-compatible image generation endpoint.

    Generate images from text prompts using various AI models.
    Supports providers like Stability AI (Stable Diffusion), OpenAI (DALL-E), and Google Vertex AI.

    Example requests:

    DeepInfra:
    ```json
    {
        "prompt": "A serene mountain landscape at sunset",
        "model": "stabilityai/sd3.5",
        "size": "1024x1024",
        "n": 1,
        "provider": "deepinfra"
    }
    ```

    DeepInfra/Others:
    ```json
    {
        "prompt": "A serene mountain landscape at sunset",
        "model": "stable-diffusion-3.5-large",
        "size": "1024x1024",
        "n": 1,
        "quality": "standard"
    }
    ```

    Google Vertex AI:
    ```json
    {
        "prompt": "A serene mountain landscape at sunset",
        "model": "stable-diffusion-1.5",
        "size": "512x512",
        "n": 1,
        "provider": "google-vertex",
        "google_project_id": "gatewayz-468519",
        "google_location": "us-central1",
        "google_endpoint_id": "6072619212881264640"
    }
    ```

    Fal.ai:
    ```json
    {
        "prompt": "A serene mountain landscape at sunset",
        "model": "fal-ai/stable-diffusion-v15",
        "size": "1024x1024",
        "n": 1,
        "provider": "fal"
    }
    ```
    """
    # Initialize performance tracker
    tracker = PerformanceTracker(endpoint="/v1/images/generations")

    # Initialize variables for error handling
    actual_provider = None
    model = None
    start = None

    try:
        # Get running event loop for async operations
        loop = asyncio.get_running_loop()

        # Create thread pool executor for sync database operations
        executor = ThreadPoolExecutor()

        try:
            # Get user asynchronously
            with tracker.stage("auth_validation"):
                user = await loop.run_in_executor(executor, get_user, api_key)

            if not user:
                if (
                    Config.IS_TESTING
                    or os.environ.get("TESTING", "").lower() in {"1", "true", "yes"}
                ) and api_key.lower().startswith("test"):
                    user = {
                        "id": 0,
                        "credits": 1_000_000.0,
                        "api_key": api_key,
                    }
                else:
                    raise HTTPException(status_code=401, detail="Invalid API key")

            # Validate prompt
            if not isinstance(req.prompt, str) or not req.prompt.strip():
                raise HTTPException(status_code=422, detail="Prompt must be a non-empty string")

            # Validate requested image count
            if req.n <= 0:
                raise HTTPException(
                    status_code=422, detail="Parameter 'n' must be a positive integer"
                )

            # Validate size format (e.g., 512x512)
            if req.size:
                try:
                    width_str, height_str = req.size.lower().split("x")
                    width = int(width_str)
                    height = int(height_str)
                    if width <= 0 or height <= 0:
                        raise ValueError
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail="Image size must be formatted as WIDTHxHEIGHT with positive integers",
                    ) from None

            # Prepare request parameters (needed for cost estimation)
            prompt = req.prompt
            model = req.model if req.model else "stable-diffusion-3.5-large"
            provider = (
                req.provider if req.provider else "deepinfra"
            )  # Default to DeepInfra for images

            # Calculate estimated cost for pre-flight check
            estimated_cost, cost_per_image, _ = get_image_cost(provider, model, req.n)

            # Check if user has enough credits
            # Note: This is a pre-flight check. Actual deduction happens after generation.
            # For concurrent request safety, we add a small buffer (10%) to account for
            # potential race conditions where balance could change between check and deduction.
            required_credits = estimated_cost * 1.1  # 10% buffer for safety
            if user["credits"] < required_credits:
                logger.warning(
                    "Insufficient credits for image generation (user %s): "
                    "estimated_cost=%.4f, required_with_buffer=%.4f, available=%.4f, "
                    "cost_per_image=%.4f, n=%d",
                    user.get("id"),
                    estimated_cost,
                    required_credits,
                    user["credits"],
                    cost_per_image,
                    req.n,
                )
                raise HTTPException(
                    status_code=402,
                    detail="Insufficient credits. Please add credits to continue.",
                )
            actual_provider = provider  # Initialize for error handling

            # Make image generation request
            logger.info(f"Generating {req.n} image(s) with prompt: {prompt[:50]}...")

            # Start timing inference
            start = time.monotonic()

            if provider == "deepinfra":
                # Direct DeepInfra request
                make_request_func = partial(
                    make_deepinfra_image_request, prompt=prompt, model=model, size=req.size, n=req.n
                )
                actual_provider = "deepinfra"
            elif provider == "google-vertex":
                # Google Vertex AI request
                google_project_id = (
                    req.google_project_id if hasattr(req, "google_project_id") else None
                )
                google_location = req.google_location if hasattr(req, "google_location") else None
                google_endpoint_id = (
                    req.google_endpoint_id if hasattr(req, "google_endpoint_id") else None
                )

                make_request_func = partial(
                    make_google_vertex_image_request,
                    prompt=prompt,
                    model=model,
                    size=req.size,
                    n=req.n,
                    project_id=google_project_id,
                    location=google_location,
                    endpoint_id=google_endpoint_id,
                )
                actual_provider = "google-vertex"
            elif provider == "fal":
                # Fal.ai request
                make_request_func = partial(
                    make_fal_image_request, prompt=prompt, model=model, size=req.size, n=req.n
                )
                actual_provider = "fal"
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Provider '{provider}' is not supported for image generation. Use 'deepinfra', 'google-vertex', or 'fal'",
                )

            # Wrap image generation with distributed tracing for Tempo
            async with AITracer.trace_inference(
                provider=actual_provider,
                model=model,
                request_type=AIRequestType.IMAGE_GENERATION,
            ) as trace_ctx:
                response = await loop.run_in_executor(executor, make_request_func)
                processed_response = await loop.run_in_executor(
                    executor, process_image_generation_response, response, actual_provider, model
                )

                # Calculate inference latency
                elapsed = max(0.001, time.monotonic() - start)

                # Calculate actual cost using provider pricing for tracing
                trace_total_cost, trace_cost_per_image, _ = get_image_cost(
                    actual_provider, model, req.n
                )
                # Set cost and metadata on trace using actual USD cost
                trace_ctx.set_cost(trace_total_cost)
                trace_ctx.set_user_info(user_id=str(user.get("id")))
                trace_ctx.add_event(
                    "image_generated",
                    {
                        "num_images": req.n,
                        "size": req.size,
                        "prompt_length": len(prompt),
                    },
                )

            # Record successful model call
            background_tasks.add_task(
                record_model_call,
                provider=actual_provider,
                model=model,
                response_time_ms=elapsed * 1000,
                status="success",
            )

            # Calculate actual cost using the provider that was used (may differ from requested)
            total_cost, cost_per_image, used_fallback_pricing = get_image_cost(
                actual_provider, model, req.n
            )

            # Token-equivalent for rate limiting: use 100 tokens per image as a standardized unit
            # This maintains compatibility with token-based rate limiting while using actual USD pricing
            tokens_equivalent = 100 * req.n

            # Deduct credits - CRITICAL: failures must prevent free images
            # The deduct_credits function handles atomic balance updates to prevent race conditions
            actual_balance_after = None
            try:
                await loop.run_in_executor(executor, deduct_credits, api_key, total_cost)

                # Fetch fresh balance after deduction for accurate reporting
                # This avoids stale data from the pre-request user lookup
                updated_user = await loop.run_in_executor(executor, get_user, api_key)
                if updated_user:
                    actual_balance_after = updated_user.get("credits")

                await loop.run_in_executor(
                    executor,
                    record_usage,
                    user["id"],
                    api_key,
                    model,
                    tokens_equivalent,  # Token-equivalent for rate limiting compatibility
                    total_cost,
                    int(elapsed * 1000),
                )

                # Increment API key usage count
                await loop.run_in_executor(executor, increment_api_key_usage, api_key)

            except ValueError as e:
                # Insufficient credits or daily limit exceeded - user should NOT get free images
                logger.error(f"Credit deduction failed for image generation: {e}")
                raise HTTPException(
                    status_code=402,
                    detail="Insufficient credits. Please add credits to continue.",
                )
            except Exception as e:
                # Unexpected error in billing - fail safe, don't give away free images
                logger.error(f"Unexpected error in credit deduction: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail="Billing error occurred. Please try again or contact support.",
                )

            # Add gateway usage info with accurate balance after deduction
            processed_response["gateway_usage"] = {
                "tokens_charged": tokens_equivalent,  # Keep for backward compatibility
                "cost_usd": total_cost,
                "cost_per_image": cost_per_image,
                "request_ms": int(elapsed * 1000),
                "user_balance_after": (
                    actual_balance_after
                    if actual_balance_after is not None
                    else user["credits"] - total_cost  # Fallback to estimate if fetch failed
                ),
                "user_api_key": f"{api_key[:10]}...",
                "images_generated": req.n,
                "used_fallback_pricing": used_fallback_pricing,
            }

            return processed_response

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in image generation: {e}")

            # Record failed model call if we have provider and model info
            if actual_provider is not None and model is not None and start is not None:
                elapsed = max(0.001, time.monotonic() - start)
                background_tasks.add_task(
                    record_model_call,
                    provider=actual_provider,
                    model=model,
                    response_time_ms=elapsed * 1000,
                    status="error",
                    error_message=str(e)[:500],
                )

            raise HTTPException(status_code=500, detail=f"Image generation failed: {str(e)}") from e

        finally:
            # Clean up executor
            executor.shutdown(wait=False)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in image generation endpoint: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e
