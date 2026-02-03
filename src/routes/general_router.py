"""
General Router API Endpoints

Provides:
- Router settings and options
- Model availability
- Routing test endpoint
- Router statistics
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from src.schemas.general_router import (
    ModelMappingInfo,
    RouteTestRequest,
    RouteTestResponse,
    RouterStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/general-router", tags=["general-router"])


@router.get("/settings/options")
async def get_general_router_settings_options() -> dict[str, Any]:
    """
    Get available settings options for general router.

    Returns configuration schema for UI builders and client applications.
    """
    return {
        "success": True,
        "options": {
            "use_general_router": {
                "type": "boolean",
                "default": True,
                "label": "Use General Router",
                "description": "Enable NotDiamond-powered model selection for general tasks",
            },
            "optimization_mode": {
                "type": "select",
                "default": "balanced",
                "label": "Optimization Mode",
                "options": [
                    {
                        "value": "balanced",
                        "label": "Balanced",
                        "description": "Balance quality, cost, and latency",
                    },
                    {
                        "value": "quality",
                        "label": "Quality",
                        "description": "Optimize for response quality",
                    },
                    {
                        "value": "cost",
                        "label": "Cost",
                        "description": "Optimize for lowest cost",
                    },
                    {
                        "value": "latency",
                        "label": "Latency",
                        "description": "Optimize for fastest response",
                    },
                ],
            },
        },
        "modes": [
            {"value": "balanced", "label": "Balanced"},
            {"value": "quality", "label": "Quality"},
            {"value": "cost", "label": "Cost"},
            {"value": "latency", "label": "Latency"},
        ],
        "syntax": {
            "primary": "router:general:<mode>",
            "examples": [
                "router:general",
                "router:general:quality",
                "router:general:cost",
                "router:general:latency",
            ],
            "aliases": [
                "gatewayz-general",
                "gatewayz-general-quality",
                "gatewayz-general-cost",
                "gatewayz-general-latency",
            ],
        },
    }


@router.get("/models", response_model=dict[str, Any])
async def get_available_models() -> dict[str, Any]:
    """
    Get models available for NotDiamond routing.

    Returns list of NotDiamond candidate models with Gatewayz mappings.
    """
    from src.services.notdiamond_client import get_notdiamond_client

    client = get_notdiamond_client()
    if not client.enabled:
        return {
            "success": False,
            "error": "NotDiamond client not enabled",
            "models": [],
        }

    candidate_models = client.model_mappings.get("candidate_models", [])
    mappings = client.model_mappings.get("mappings", {})

    models = []
    for nd_model in candidate_models:
        if nd_model in mappings:
            mapping = mappings[nd_model]
            models.append(
                {
                    "notdiamond_id": nd_model,
                    "gatewayz_id": mapping["gatewayz_id"],
                    "provider": mapping["provider"],
                    "available_on": mapping.get("available_on", []),
                }
            )

    return {
        "success": True,
        "models": models,
        "total": len(models),
    }


@router.post("/test", response_model=RouteTestResponse)
async def test_general_routing(request: RouteTestRequest) -> RouteTestResponse:
    """
    Test routing without making inference call.

    Useful for debugging and validating routing decisions before actual inference.
    """
    try:
        from src.services.general_router import route_general_prompt

        result = await route_general_prompt(
            messages=request.messages,
            mode=request.mode,  # type: ignore
        )

        return RouteTestResponse(
            model_id=result["model_id"],
            provider=result["provider"],
            mode=result["mode"],
            routing_latency_ms=result["routing_latency_ms"],
            confidence=result.get("confidence"),
            fallback_used=result.get("fallback_used", False),
            fallback_reason=result.get("fallback_reason"),
        )
    except Exception as e:
        logger.error(f"General routing test failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats", response_model=dict[str, Any])
async def get_general_router_stats() -> dict[str, Any]:
    """
    Get general router statistics.

    Returns configuration and status information about the general router.
    """
    try:
        from src.services.general_router import get_router

        router_instance = get_router()

        return {
            "success": True,
            "stats": {
                "notdiamond_enabled": router_instance.enabled,
                "fallback_models": router_instance.fallback_models,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get stats: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "stats": {},
        }


@router.get("/fallback-models")
async def get_fallback_models() -> dict[str, Any]:
    """
    Get fallback models configuration.

    Returns the fallback model for each routing mode.
    """
    from src.services.general_router_fallback import get_fallback_models

    return {
        "success": True,
        "fallback_models": get_fallback_models(),
    }
