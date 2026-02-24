import asyncio
import logging
import os
import time
import uuid
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
from src.services.pricing_lookup import get_image_pricing
from src.utils.ai_tracing import AIRequestType, AITracer
from src.utils.performance_tracker import PerformanceTracker

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()

# DEPRECATED: Hardcoded image pricing fallback.
# Canonical image pricing now lives in src/data/manual_pricing.json under the
# "image_pricing" key.  This dict is kept only as a last-resort fallback during
# the transition period.  It will be removed once all pricing is confirmed to be
# served from manual_pricing.json.  Do NOT add new entries here -- update
# manual_pricing.json instead.
_HARDCODED_IMAGE_COST_PER_IMAGE = {
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

# Resolution-based cost multipliers relative to 1024x1024 base rate
# Higher resolutions require more compute and should cost more
RESOLUTION_MULTIPLIERS = {
    "256x256": 0.5,
    "512x512": 0.75,
    "1024x1024": 1.0,  # Base rate
    "1024x1792": 1.5,  # HD portrait
    "1792x1024": 1.5,  # HD landscape
    "2048x2048": 2.0,  # Ultra HD
}
# Default multiplier for unknown/unrecognized sizes (backwards compatible)
DEFAULT_RESOLUTION_MULTIPLIER = 1.0


def get_image_cost(
    provider: str, model: str, num_images: int = 1, size: str = None
) -> tuple[float, float, bool, float]:
    """
    Calculate the cost for image generation with resolution-aware pricing.

    Pricing lookup order:
      1. manual_pricing.json  (``image_pricing`` section, via ``get_image_pricing()``)
      2. Hardcoded fallback   (``_HARDCODED_IMAGE_COST_PER_IMAGE`` -- deprecated)
      3. Provider default     (``"default"`` key in hardcoded dict)
      4. Unknown-provider     (``UNKNOWN_PROVIDER_DEFAULT_COST``)

    Args:
        provider: Image generation provider (deepinfra, fal, google-vertex)
        model: Model name
        num_images: Number of images to generate
        size: Image resolution string (e.g. "1024x1024"). If None, uses default multiplier of 1.0.

    Returns:
        Tuple of (total_cost, cost_per_image, is_fallback_pricing, resolution_multiplier)
        is_fallback_pricing is True when using default/unknown pricing
        resolution_multiplier is the multiplier applied based on the requested size
    """
    is_fallback = False

    # --- Tier 1: config-driven pricing from manual_pricing.json ---
    config_result = get_image_pricing(provider, model)
    if config_result is not None:
        config_price, config_is_fallback = config_result
        total_cost = config_price * num_images
        return total_cost, config_price, config_is_fallback

    # --- Tier 2+: hardcoded fallback (deprecated) ---
    logger.warning(
        f"Image pricing not found in manual_pricing.json, falling back to hardcoded dict: "
        f"provider={provider}, model={model}"
    )

    provider_pricing = _HARDCODED_IMAGE_COST_PER_IMAGE.get(provider, {})

    if model in provider_pricing:
        base_cost_per_image = provider_pricing[model]
    elif "default" in provider_pricing:
        base_cost_per_image = provider_pricing["default"]
        is_fallback = True
        logger.warning(
            f"Using default pricing for unknown model: provider={provider}, model={model}, "
            f"base_cost_per_image={base_cost_per_image}"
        )
    else:
        # Unknown provider - use conservative high default to avoid revenue loss
        base_cost_per_image = UNKNOWN_PROVIDER_DEFAULT_COST
        is_fallback = True
        logger.warning(
            f"Using fallback pricing for unknown provider: provider={provider}, model={model}, "
            f"base_cost_per_image={base_cost_per_image}"
        )

    # Apply resolution-based multiplier
    if size is not None:
        resolution_multiplier = RESOLUTION_MULTIPLIERS.get(
            size.lower().strip(), DEFAULT_RESOLUTION_MULTIPLIER
        )
    else:
        resolution_multiplier = DEFAULT_RESOLUTION_MULTIPLIER

    cost_per_image = base_cost_per_image * resolution_multiplier
    total_cost = cost_per_image * num_images
    return total_cost, cost_per_image, is_fallback, resolution_multiplier


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

    # Generate request_id for correlation with billing transactions
    request_id = str(uuid.uuid4())

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

            # Calculate estimated cost for pre-flight check (resolution-aware)
            estimated_cost, cost_per_image, _, resolution_multiplier = get_image_cost(
                provider, model, req.n, size=req.size
            )

            # Check if user has enough credits
            # Note: This is a pre-flight check. Actual deduction happens after generation.
            # For concurrent request safety, we add a small buffer (10%) to account for
            # potential race conditions where balance could change between check and deduction.
            required_credits = estimated_cost * 1.1  # 10% buffer for safety
            if user["credits"] < required_credits:
                raise HTTPException(
                    status_code=402,
                    detail=(
                        f"Insufficient credits. Image generation costs ${estimated_cost:.4f} "
                        f"(${cost_per_image:.4f}/image x {req.n}), requires ${required_credits:.4f} "
                        f"with safety buffer. Available: ${user['credits']:.4f}"
                    ),
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

                # Calculate actual cost using provider pricing for tracing (resolution-aware)
                trace_total_cost, trace_cost_per_image, _, _ = get_image_cost(
                    actual_provider, model, req.n, size=req.size
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
            # Resolution multiplier is applied based on requested size
            total_cost, cost_per_image, used_fallback_pricing, resolution_multiplier = (
                get_image_cost(actual_provider, model, req.n, size=req.size)
            )

            # Audit log: resolution-adjusted pricing for billing transparency
            logger.info(
                f"Image pricing: provider={actual_provider}, model={model}, "
                f"size={req.size}, resolution_multiplier={resolution_multiplier}, "
                f"cost_per_image=${cost_per_image:.4f}, num_images={req.n}, "
                f"total_cost=${total_cost:.4f}, fallback_pricing={used_fallback_pricing}, "
                f"user_id={user.get('id')}"
            )

            # Token-equivalent for rate limiting: use 100 tokens per image as a standardized unit
            # This maintains compatibility with token-based rate limiting while using actual USD pricing
            tokens_equivalent = 100 * req.n

            # Deduct credits - CRITICAL: failures must prevent free images
            # The deduct_credits function handles atomic balance updates to prevent race conditions
            actual_balance_after = None
            try:
                await loop.run_in_executor(
                    executor,
                    partial(
                        deduct_credits,
                        api_key,
                        total_cost,
                        f"Image generation - {model}",
                        {
                            "model": model,
                            "provider": actual_provider,
                            "num_images": req.n,
                            "cost_per_image": cost_per_image,
                            "cost_usd": total_cost,
                            "endpoint": "/v1/images/generations",
                            "request_id": request_id,
                        },
                    ),
                )

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
                    detail=f"Payment required: {e}",
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
                "size": req.size,
                "resolution_multiplier": resolution_multiplier,
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
