"""
Code Router Client Configuration

Helper module for client applications (like Terry/Claude Code agents) to configure
and use the code-optimized prompt router via Gatewayz API.

Usage:
    from src.services.code_router_client import CodeRouterConfig, get_router_model_string

    # Create a configuration
    config = CodeRouterConfig(
        mode="balanced",  # or "price", "quality", "agentic"
        enabled=True,
    )

    # Get the model string to use in API requests
    model = get_router_model_string(config)
    # Returns: "router:code" for balanced, "router:code:price" for price, etc.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CodeRouterMode(str, Enum):
    """
    Code router optimization modes.

    - BALANCED: Auto-select best price/performance balance (default)
    - PRICE: Optimize for lowest cost while respecting quality gates
    - QUALITY: Optimize for highest quality (bumps up tier)
    - AGENTIC: Force premium tier for complex multi-step tasks
    """

    BALANCED = "balanced"
    PRICE = "price"
    QUALITY = "quality"
    AGENTIC = "agentic"


class CodeRouterConfig(BaseModel):
    """
    Configuration for code-optimized prompt routing.

    This configuration determines how the Gatewayz API routes code-related
    prompts to the most appropriate model based on task complexity and
    optimization preferences.
    """

    enabled: bool = Field(
        default=True,
        description="Enable code-optimized routing. If False, uses default model.",
    )
    mode: CodeRouterMode = Field(
        default=CodeRouterMode.BALANCED,
        description="Optimization mode for model selection.",
    )
    fallback_model: str | None = Field(
        default=None,
        description="Model to use if routing fails or is disabled. If None, uses system default.",
    )

    class Config:
        use_enum_values = True


# Preset configurations for common use cases
PRESETS: dict[str, CodeRouterConfig] = {
    "default": CodeRouterConfig(
        enabled=True,
        mode=CodeRouterMode.BALANCED,
    ),
    "cost_optimized": CodeRouterConfig(
        enabled=True,
        mode=CodeRouterMode.PRICE,
    ),
    "quality_optimized": CodeRouterConfig(
        enabled=True,
        mode=CodeRouterMode.QUALITY,
    ),
    "agentic": CodeRouterConfig(
        enabled=True,
        mode=CodeRouterMode.AGENTIC,
    ),
    "disabled": CodeRouterConfig(
        enabled=False,
        fallback_model="anthropic/claude-sonnet-4",
    ),
}


def get_router_model_string(config: CodeRouterConfig | None = None) -> str:
    """
    Get the model string to use in API requests based on configuration.

    Args:
        config: Code router configuration. If None, uses default (balanced).

    Returns:
        Model string for API request (e.g., "router:code:price")

    Examples:
        >>> get_router_model_string()
        'router:code'

        >>> get_router_model_string(CodeRouterConfig(mode=CodeRouterMode.PRICE))
        'router:code:price'

        >>> get_router_model_string(CodeRouterConfig(enabled=False, fallback_model="gpt-4"))
        'gpt-4'
    """
    if config is None:
        config = PRESETS["default"]

    if not config.enabled:
        return config.fallback_model or "anthropic/claude-sonnet-4"

    mode = config.mode
    if isinstance(mode, CodeRouterMode):
        mode = mode.value

    if mode == "balanced":
        return "router:code"
    else:
        return f"router:code:{mode}"


def get_preset(name: str) -> CodeRouterConfig:
    """
    Get a preset configuration by name.

    Available presets:
    - "default": Balanced price/quality optimization
    - "cost_optimized": Prioritize lower costs
    - "quality_optimized": Prioritize higher quality
    - "agentic": Use premium models for complex tasks
    - "disabled": Disable routing, use fallback model

    Args:
        name: Preset name

    Returns:
        CodeRouterConfig for the preset

    Raises:
        ValueError: If preset name is not recognized
    """
    if name not in PRESETS:
        available = ", ".join(PRESETS.keys())
        raise ValueError(f"Unknown preset '{name}'. Available: {available}")
    return PRESETS[name].model_copy()


def create_api_request_config(
    mode: str = "balanced",
    enabled: bool = True,
    fallback_model: str | None = None,
) -> dict[str, Any]:
    """
    Create API request configuration for code routing.

    This is a convenience function for creating the model field
    in API requests.

    Args:
        mode: Optimization mode ("balanced", "price", "quality", "agentic")
        enabled: Whether to enable code routing
        fallback_model: Model to use if routing is disabled

    Returns:
        Dict with 'model' key set to appropriate value

    Example:
        >>> config = create_api_request_config(mode="price")
        >>> requests.post(
        ...     "https://api.gatewayz.ai/v1/chat/completions",
        ...     json={
        ...         **config,
        ...         "messages": [{"role": "user", "content": "Debug this..."}]
        ...     }
        ... )
    """
    router_config = CodeRouterConfig(
        enabled=enabled,
        mode=CodeRouterMode(mode) if enabled else CodeRouterMode.BALANCED,
        fallback_model=fallback_model,
    )
    return {"model": get_router_model_string(router_config)}


# ==================== Settings Schema for Client Applications ====================


class CodeRouterSettings(BaseModel):
    """
    User-configurable settings for code router in client applications.

    This schema can be used by client applications (like Terry/Claude Code)
    to store and manage user preferences for code routing.
    """

    # Main toggle
    use_code_router: bool = Field(
        default=True,
        description="Use intelligent code routing instead of a fixed model",
    )

    # Mode selection (only used if use_code_router is True)
    optimization_mode: CodeRouterMode = Field(
        default=CodeRouterMode.BALANCED,
        description="How to optimize model selection",
    )

    # Manual model override (only used if use_code_router is False)
    manual_model: str = Field(
        default="anthropic/claude-sonnet-4",
        description="Model to use when code router is disabled",
    )

    # Advanced settings
    show_routing_info: bool = Field(
        default=True,
        description="Show routing decision info in responses",
    )
    show_savings: bool = Field(
        default=True,
        description="Show cost savings information",
    )

    def get_model_string(self) -> str:
        """Get the model string based on current settings."""
        if not self.use_code_router:
            return self.manual_model

        if self.optimization_mode == CodeRouterMode.BALANCED:
            return "router:code"
        else:
            return f"router:code:{self.optimization_mode.value}"

    def to_display_dict(self) -> dict[str, Any]:
        """Get settings in a format suitable for UI display."""
        return {
            "Code Router": "Enabled" if self.use_code_router else "Disabled",
            "Mode": self.optimization_mode.value.title() if self.use_code_router else "N/A",
            "Model": self.get_model_string(),
            "Show Routing Info": self.show_routing_info,
            "Show Savings": self.show_savings,
        }


# Default settings instance
DEFAULT_SETTINGS = CodeRouterSettings()


def get_settings_options() -> dict[str, Any]:
    """
    Get available options for settings UI.

    Returns a dict describing all configurable options with their
    types, defaults, and descriptions.
    """
    return {
        "use_code_router": {
            "type": "boolean",
            "default": True,
            "label": "Use Code Router",
            "description": "Enable intelligent model selection based on task complexity",
        },
        "optimization_mode": {
            "type": "select",
            "default": "balanced",
            "label": "Optimization Mode",
            "description": "How to balance cost and quality",
            "options": [
                {
                    "value": "balanced",
                    "label": "Balanced",
                    "description": "Auto-select best price/performance balance",
                },
                {
                    "value": "price",
                    "label": "Price Optimized",
                    "description": "Minimize costs while maintaining quality gates",
                },
                {
                    "value": "quality",
                    "label": "Quality Optimized",
                    "description": "Maximize quality, use higher-tier models",
                },
                {
                    "value": "agentic",
                    "label": "Agentic Mode",
                    "description": "Always use premium models for complex tasks",
                },
            ],
            "depends_on": {"use_code_router": True},
        },
        "manual_model": {
            "type": "model_select",
            "default": "anthropic/claude-sonnet-4",
            "label": "Manual Model",
            "description": "Model to use when code router is disabled",
            "depends_on": {"use_code_router": False},
        },
        "show_routing_info": {
            "type": "boolean",
            "default": True,
            "label": "Show Routing Info",
            "description": "Display which model was selected and why",
        },
        "show_savings": {
            "type": "boolean",
            "default": True,
            "label": "Show Savings",
            "description": "Display cost savings compared to premium models",
        },
    }
