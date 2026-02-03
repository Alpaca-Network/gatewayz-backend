"""General Router schemas."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

GeneralRouterMode = Literal["balanced", "quality", "cost", "latency"]


class GeneralRouterSettings(BaseModel):
    """User-configurable settings for general router."""

    use_general_router: bool = Field(
        default=True, description="Use NotDiamond routing"
    )

    optimization_mode: GeneralRouterMode = Field(
        default="balanced", description="Optimization target"
    )

    manual_model: str = Field(
        default="anthropic/claude-sonnet-4", description="Model when router disabled"
    )

    def get_model_string(self) -> str:
        """
        Get model string for API request.

        Returns:
            Model string in router format or manual model ID
        """
        if not self.use_general_router:
            return self.manual_model

        if self.optimization_mode == "balanced":
            return "router:general"

        return f"router:general:{self.optimization_mode}"


class RouteTestRequest(BaseModel):
    """Request to test general routing."""

    messages: list[dict] = Field(..., description="Messages to route")
    mode: str = Field(default="balanced", description="Routing mode")

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate routing mode."""
        valid_modes = ("balanced", "quality", "cost", "latency")
        if v.lower() not in valid_modes:
            raise ValueError(f"Invalid mode. Must be one of: {valid_modes}")
        return v.lower()


class RouteTestResponse(BaseModel):
    """Response from route testing."""

    model_id: str = Field(..., description="Selected model ID")
    provider: str = Field(..., description="Provider name")
    mode: str = Field(..., description="Routing mode used")
    routing_latency_ms: float = Field(..., description="Routing decision latency")
    confidence: float | None = Field(
        None, description="NotDiamond confidence score (0-1)"
    )
    fallback_used: bool = Field(
        default=False, description="Whether fallback was used"
    )
    fallback_reason: str | None = Field(
        None, description="Reason for fallback if used"
    )


class ModelMappingInfo(BaseModel):
    """Information about a NotDiamond to Gatewayz model mapping."""

    notdiamond_id: str = Field(..., description="NotDiamond model identifier")
    gatewayz_id: str = Field(..., description="Gatewayz model ID")
    provider: str = Field(..., description="Primary provider")
    available_on: list[str] = Field(
        default_factory=list, description="Providers offering this model"
    )


class RouterStats(BaseModel):
    """General router statistics."""

    notdiamond_enabled: bool = Field(
        ..., description="Whether NotDiamond client is enabled"
    )
    fallback_models: dict[str, str] = Field(
        default_factory=dict, description="Fallback models per mode"
    )


class RoutingMetadata(BaseModel):
    """Metadata about a routing decision (for API responses)."""

    router: str = Field(default="general", description="Router type")
    router_mode: str = Field(..., description="Routing mode used")
    selected_model: str = Field(..., description="Selected model ID")
    routing_latency_ms: float = Field(..., description="Routing latency")
    fallback_used: bool = Field(default=False, description="Whether fallback was used")
    notdiamond_session_id: str | None = Field(
        None, description="NotDiamond session ID"
    )
    confidence: float | None = Field(None, description="Routing confidence")
    fallback_reason: str | None = Field(None, description="Fallback reason if used")
