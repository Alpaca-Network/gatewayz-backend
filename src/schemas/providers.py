"""
Pydantic schemas for provider management
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderBase(BaseModel):
    """Base provider schema"""

    name: str = Field(..., description="Provider name")
    slug: str = Field(..., description="Provider slug (unique identifier)")
    description: str | None = Field(None, description="Provider description")
    base_url: str | None = Field(None, description="Base API URL")
    api_key_env_var: str | None = Field(None, description="Environment variable name for API key")
    logo_url: str | None = Field(None, description="Provider logo URL")
    site_url: str | None = Field(None, description="Provider website URL")
    privacy_policy_url: str | None = Field(None, description="Privacy policy URL")
    terms_of_service_url: str | None = Field(None, description="Terms of service URL")
    status_page_url: str | None = Field(None, description="Status page URL")
    is_active: bool = Field(True, description="Whether provider is active")
    supports_streaming: bool = Field(False, description="Supports streaming responses")
    supports_function_calling: bool = Field(False, description="Supports function calling")
    supports_vision: bool = Field(False, description="Supports vision/image inputs")
    supports_image_generation: bool = Field(False, description="Supports image generation")
    metadata: dict[str, Any] | None = Field(default_factory=dict, description="Additional metadata")


class ProviderCreate(ProviderBase):
    """Schema for creating a provider"""

    pass


class ProviderUpdate(BaseModel):
    """Schema for updating a provider (all fields optional)"""

    name: str | None = None
    slug: str | None = None
    description: str | None = None
    base_url: str | None = None
    api_key_env_var: str | None = None
    logo_url: str | None = None
    site_url: str | None = None
    privacy_policy_url: str | None = None
    terms_of_service_url: str | None = None
    status_page_url: str | None = None
    is_active: bool | None = None
    supports_streaming: bool | None = None
    supports_function_calling: bool | None = None
    supports_vision: bool | None = None
    supports_image_generation: bool | None = None
    metadata: dict[str, Any] | None = None


class ProviderHealthUpdate(BaseModel):
    """Schema for updating provider health"""

    health_status: str = Field(
        ..., description="Health status: 'healthy', 'degraded', 'down', 'unknown'"
    )
    average_response_time_ms: int | None = Field(
        None, description="Average response time in milliseconds"
    )


class ProviderResponse(ProviderBase):
    """Schema for provider response"""

    id: int = Field(..., description="Provider ID")
    average_response_time_ms: int | None = Field(
        None, description="Average response time in milliseconds"
    )
    health_status: str = Field(..., description="Health status")
    last_health_check_at: datetime | None = Field(None, description="Last health check timestamp")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class ProviderStats(BaseModel):
    """Schema for provider statistics"""

    total: int = Field(..., description="Total number of providers")
    active: int = Field(..., description="Number of active providers")
    inactive: int = Field(..., description="Number of inactive providers")
    healthy: int = Field(..., description="Number of healthy providers")
    degraded: int = Field(..., description="Number of degraded providers")
    down: int = Field(..., description="Number of down providers")
    unknown: int = Field(..., description="Number of providers with unknown status")


class ProviderWithModelCount(ProviderResponse):
    """Schema for provider with model count"""

    model_count: int = Field(0, description="Number of models from this provider")
