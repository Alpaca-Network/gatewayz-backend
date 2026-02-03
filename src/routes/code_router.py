"""
Code Router API Endpoints

Provides endpoints for:
- Getting code router settings options
- Validating router configurations
- Getting router statistics
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.services.code_router import (
    get_baselines,
    get_fallback_model,
    get_model_tiers,
    get_router,
    route_code_prompt,
)
from src.services.code_router_client import (
    CodeRouterMode,
    CodeRouterSettings,
    get_settings_options,
)

logger = logging.getLogger(__name__)

# Note: These endpoints expose router configuration and testing functionality.
# Authentication is intentionally not required for the read-only endpoints
# (settings/options, tiers, stats) as they expose non-sensitive configuration.
# The /test endpoint may want authentication in production to prevent abuse.
# Consider adding rate limiting or authentication if these endpoints are
# exposed publicly and receive significant traffic.
router = APIRouter(prefix="/code-router", tags=["code-router"])


# ==================== Request/Response Models ====================


# Valid routing modes
VALID_ROUTING_MODES = ("auto", "price", "quality", "agentic")
RoutingMode = Literal["auto", "price", "quality", "agentic"]


class RouteTestRequest(BaseModel):
    """Request to test code routing without making an actual inference call."""

    prompt: str = Field(..., description="The prompt to classify and route")
    mode: RoutingMode = Field(default="auto", description="Routing mode: auto, price, quality, agentic")
    context: dict[str, Any] | None = Field(default=None, description="Optional context")

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate and normalize routing mode."""
        if isinstance(v, str):
            v = v.lower()
        if v not in VALID_ROUTING_MODES:
            raise ValueError(f"Invalid mode '{v}'. Must be one of: {', '.join(VALID_ROUTING_MODES)}")
        return v


class RouteTestResponse(BaseModel):
    """Response from route testing."""

    model_id: str
    provider: str
    tier: int
    task_category: str
    complexity: str
    confidence: float
    mode: str
    routing_latency_ms: float
    savings_estimate: dict[str, Any]
    model_info: dict[str, Any]


class SettingsValidationRequest(BaseModel):
    """Request to validate code router settings."""

    use_code_router: bool = True
    optimization_mode: str = "balanced"
    manual_model: str | None = None


class SettingsValidationResponse(BaseModel):
    """Response from settings validation."""

    valid: bool
    model_string: str
    errors: list[str] = []
    warnings: list[str] = []


# ==================== Endpoints ====================


@router.get("/settings/options")
async def get_code_router_settings_options() -> dict[str, Any]:
    """
    Get available settings options for the code router.

    Returns a schema describing all configurable options that can be
    used to build a settings UI.
    """
    return {
        "success": True,
        "options": get_settings_options(),
        "modes": [
            {
                "value": mode.value,
                "label": mode.value.title(),
                "description": _get_mode_description(mode),
            }
            for mode in CodeRouterMode
        ],
    }


@router.get("/tiers")
async def get_code_router_tiers() -> dict[str, Any]:
    """
    Get information about model tiers.

    Returns the tier configuration including models, pricing, and benchmarks.
    """
    tiers = get_model_tiers()
    return {
        "success": True,
        "tiers": tiers,
        "fallback_model": get_fallback_model(),
        "baselines": get_baselines(),
    }


@router.post("/test")
async def test_code_routing(request: RouteTestRequest) -> RouteTestResponse:
    """
    Test code routing without making an actual inference call.

    This endpoint allows you to see what model would be selected for a
    given prompt without actually running the inference.
    """
    try:
        result = route_code_prompt(
            prompt=request.prompt,
            mode=request.mode,
            context=request.context,
        )

        return RouteTestResponse(
            model_id=result["model_id"],
            provider=result["provider"],
            tier=result["tier"],
            task_category=result["task_category"],
            complexity=result["complexity"],
            confidence=result["confidence"],
            mode=result["mode"],
            routing_latency_ms=result["routing_latency_ms"],
            savings_estimate=result["savings_estimate"],
            model_info=result.get("selected_model_info", {}),
        )
    except Exception as e:
        logger.error(f"Code routing test failed: {e}")
        raise HTTPException(status_code=500, detail=f"Routing test failed: {str(e)}")


@router.post("/settings/validate")
async def validate_code_router_settings(
    request: SettingsValidationRequest,
) -> SettingsValidationResponse:
    """
    Validate code router settings and return the resulting model string.

    Use this to check if settings are valid before saving them.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Validate optimization mode
    valid_modes = [m.value for m in CodeRouterMode]
    if request.optimization_mode not in valid_modes:
        errors.append(f"Invalid optimization_mode: {request.optimization_mode}. Valid: {valid_modes}")

    # Validate manual model if router is disabled
    if not request.use_code_router:
        if not request.manual_model:
            errors.append("manual_model is required when use_code_router is False")
        elif not _is_valid_model_id(request.manual_model):
            warnings.append(f"Model '{request.manual_model}' may not be available")

    # Build the model string
    if errors:
        return SettingsValidationResponse(
            valid=False,
            model_string="",
            errors=errors,
            warnings=warnings,
        )

    try:
        settings = CodeRouterSettings(
            use_code_router=request.use_code_router,
            optimization_mode=CodeRouterMode(request.optimization_mode),
            manual_model=request.manual_model or "anthropic/claude-sonnet-4",
        )
        model_string = settings.get_model_string()

        return SettingsValidationResponse(
            valid=True,
            model_string=model_string,
            errors=[],
            warnings=warnings,
        )
    except Exception as e:
        return SettingsValidationResponse(
            valid=False,
            model_string="",
            errors=[str(e)],
            warnings=warnings,
        )


@router.get("/stats")
async def get_code_router_stats() -> dict[str, Any]:
    """
    Get code router statistics and performance metrics.

    Returns information about routing decisions, latency, and savings.
    """
    try:
        # Get router instance for any cached stats
        router_instance = get_router()

        # Basic stats from the router
        stats = {
            "tiers_loaded": len(router_instance.model_tiers),
            "models_available": sum(
                len(tier.get("models", []))
                for tier in router_instance.model_tiers.values()
            ),
            "fallback_model": router_instance.fallback_model.get("id"),
            "baselines": list(router_instance.baselines.keys()),
        }

        # Try to get Prometheus metrics if available
        try:
            from src.services.prometheus_metrics import (
                code_router_latency_seconds,
                code_router_requests_total,
            )

            # Note: Getting actual values from Prometheus requires more complex logic
            # This is just indicating that metrics are being tracked
            stats["metrics_enabled"] = True
        except ImportError:
            stats["metrics_enabled"] = False

        return {
            "success": True,
            "stats": stats,
        }
    except Exception as e:
        logger.error(f"Failed to get code router stats: {e}")
        return {
            "success": False,
            "error": str(e),
        }


# ==================== Helper Functions ====================


def _get_mode_description(mode: CodeRouterMode) -> str:
    """Get description for a routing mode."""
    descriptions = {
        CodeRouterMode.BALANCED: "Auto-select best price/performance balance",
        CodeRouterMode.PRICE: "Optimize for lowest cost while maintaining quality",
        CodeRouterMode.QUALITY: "Optimize for highest quality, use better models",
        CodeRouterMode.AGENTIC: "Always use premium models for complex tasks",
    }
    return descriptions.get(mode, "Unknown mode")


def _is_valid_model_id(model_id: str) -> bool:
    """Check if a model ID looks valid (basic validation)."""
    if not model_id:
        return False
    # Basic format check: should contain a slash (org/model) or be a known alias
    if "/" in model_id:
        return True
    # Check if it's a known short alias
    known_aliases = [
        "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo",
        "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
        "gemini-pro", "gemini-flash",
    ]
    return model_id.lower() in known_aliases
