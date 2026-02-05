"""
Pydantic schemas for model catalog management
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ModelBase(BaseModel):
    """Base model schema"""
    provider_id: int = Field(..., description="Provider ID (foreign key)")
    model_name: str = Field(..., description="Model display name")
    provider_model_id: str = Field(..., description="Provider-specific model identifier")
    description: str | None = Field(None, description="Model description")
    context_length: int | None = Field(None, description="Maximum context length")
    modality: str | None = Field("text->text", description="Model modality (e.g., 'text->text', 'text->image')")

    # Pricing
    pricing_prompt: Decimal | None = Field(None, description="Prompt pricing per token")
    pricing_completion: Decimal | None = Field(None, description="Completion pricing per token")
    pricing_image: Decimal | None = Field(None, description="Image pricing")
    pricing_request: Decimal | None = Field(None, description="Request pricing")

    # Capabilities
    supports_streaming: bool = Field(False, description="Supports streaming responses")
    supports_function_calling: bool = Field(False, description="Supports function calling")
    supports_vision: bool = Field(False, description="Supports vision/image inputs")

    # Status
    is_active: bool = Field(True, description="Whether model is active")
    metadata: dict[str, Any] | None = Field(default_factory=dict, description="Additional metadata")


class ModelCreate(ModelBase):
    """Schema for creating a model"""
    pass


class ModelBulkCreate(BaseModel):
    """Schema for bulk creating models"""
    models: list[ModelCreate] = Field(..., description="List of models to create")


class ModelUpdate(BaseModel):
    """Schema for updating a model (all fields optional)"""
    provider_id: int | None = None
    model_name: str | None = None
    provider_model_id: str | None = None
    description: str | None = None
    context_length: int | None = None
    modality: str | None = None
    pricing_prompt: Decimal | None = None
    pricing_completion: Decimal | None = None
    pricing_image: Decimal | None = None
    pricing_request: Decimal | None = None
    supports_streaming: bool | None = None
    supports_function_calling: bool | None = None
    supports_vision: bool | None = None
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None


class ModelHealthUpdate(BaseModel):
    """Schema for updating model health"""
    health_status: str = Field(..., description="Health status: 'healthy', 'degraded', 'down', 'unknown'")
    response_time_ms: int | None = Field(None, description="Response time in milliseconds")
    error_message: str | None = Field(None, description="Error message if health check failed")


class ModelResponse(ModelBase):
    """Schema for model response"""
    id: int = Field(..., description="Model ID")
    average_response_time_ms: int | None = Field(None, description="Average response time in milliseconds")
    health_status: str = Field(..., description="Health status")
    last_health_check_at: datetime | None = Field(None, description="Last health check timestamp")
    success_rate: Decimal | None = Field(None, description="Success rate percentage")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class ModelWithProvider(ModelResponse):
    """Schema for model with provider information"""
    provider: dict[str, Any] = Field(..., description="Provider information")


class ModelHealthHistoryResponse(BaseModel):
    """Schema for model health history response"""
    id: int = Field(..., description="History record ID")
    model_id: int = Field(..., description="Model ID")
    health_status: str = Field(..., description="Health status")
    response_time_ms: int | None = Field(None, description="Response time in milliseconds")
    error_message: str | None = Field(None, description="Error message")
    checked_at: datetime = Field(..., description="Check timestamp")

    class Config:
        from_attributes = True


class ModelStats(BaseModel):
    """Schema for model statistics"""
    total: int = Field(..., description="Total number of models")
    active: int = Field(..., description="Number of active models")
    inactive: int = Field(..., description="Number of inactive models")
    healthy: int = Field(..., description="Number of healthy models")
    degraded: int = Field(..., description="Number of degraded models")
    down: int = Field(..., description="Number of down models")
    unknown: int = Field(..., description="Number of models with unknown status")
    by_modality: dict[str, int] = Field(default_factory=dict, description="Model count by modality")


class ModelSearchQuery(BaseModel):
    """Schema for model search query"""
    query: str = Field(..., description="Search query")
    provider_id: int | None = Field(None, description="Optional provider filter")
    limit: int = Field(100, description="Maximum results", ge=1, le=1000)
    offset: int = Field(0, description="Offset for pagination", ge=0)


class ModelListQuery(BaseModel):
    """Schema for model list query parameters"""
    provider_id: int | None = Field(None, description="Filter by provider ID")
    provider_slug: str | None = Field(None, description="Filter by provider slug")
    is_active_only: bool = Field(True, description="Only return active models")
    health_status: str | None = Field(None, description="Filter by health status")
    modality: str | None = Field(None, description="Filter by modality")
    limit: int = Field(100, description="Maximum results", ge=1, le=1000)
    offset: int = Field(0, description="Offset for pagination", ge=0)
