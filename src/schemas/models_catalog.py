"""
Pydantic schemas for model catalog management
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field


class ModelBase(BaseModel):
    """Base model schema"""
    provider_id: int = Field(..., description="Provider ID (foreign key)")
    model_id: str = Field(..., description="Standardized model ID")
    model_name: str = Field(..., description="Model display name")
    provider_model_id: str = Field(..., description="Provider-specific model identifier")
    description: Optional[str] = Field(None, description="Model description")
    context_length: Optional[int] = Field(None, description="Maximum context length")
    modality: Optional[str] = Field("text->text", description="Model modality (e.g., 'text->text', 'text->image')")
    architecture: Optional[str] = Field(None, description="Model architecture")
    top_provider: Optional[str] = Field(None, description="Top provider for this model")
    per_request_limits: Optional[Dict[str, Any]] = Field(None, description="Per-request limits")

    # Pricing
    pricing_prompt: Optional[Decimal] = Field(None, description="Prompt pricing per token")
    pricing_completion: Optional[Decimal] = Field(None, description="Completion pricing per token")
    pricing_image: Optional[Decimal] = Field(None, description="Image pricing")
    pricing_request: Optional[Decimal] = Field(None, description="Request pricing")

    # Capabilities
    supports_streaming: bool = Field(False, description="Supports streaming responses")
    supports_function_calling: bool = Field(False, description="Supports function calling")
    supports_vision: bool = Field(False, description="Supports vision/image inputs")

    # Status
    is_active: bool = Field(True, description="Whether model is active")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class ModelCreate(ModelBase):
    """Schema for creating a model"""
    pass


class ModelBulkCreate(BaseModel):
    """Schema for bulk creating models"""
    models: List[ModelCreate] = Field(..., description="List of models to create")


class ModelUpdate(BaseModel):
    """Schema for updating a model (all fields optional)"""
    provider_id: Optional[int] = None
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    provider_model_id: Optional[str] = None
    description: Optional[str] = None
    context_length: Optional[int] = None
    modality: Optional[str] = None
    architecture: Optional[str] = None
    top_provider: Optional[str] = None
    per_request_limits: Optional[Dict[str, Any]] = None
    pricing_prompt: Optional[Decimal] = None
    pricing_completion: Optional[Decimal] = None
    pricing_image: Optional[Decimal] = None
    pricing_request: Optional[Decimal] = None
    supports_streaming: Optional[bool] = None
    supports_function_calling: Optional[bool] = None
    supports_vision: Optional[bool] = None
    is_active: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class ModelHealthUpdate(BaseModel):
    """Schema for updating model health"""
    health_status: str = Field(..., description="Health status: 'healthy', 'degraded', 'down', 'unknown'")
    response_time_ms: Optional[int] = Field(None, description="Response time in milliseconds")
    error_message: Optional[str] = Field(None, description="Error message if health check failed")


class ModelResponse(ModelBase):
    """Schema for model response"""
    id: int = Field(..., description="Model ID")
    average_response_time_ms: Optional[int] = Field(None, description="Average response time in milliseconds")
    health_status: str = Field(..., description="Health status")
    last_health_check_at: Optional[datetime] = Field(None, description="Last health check timestamp")
    success_rate: Optional[Decimal] = Field(None, description="Success rate percentage")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class ModelWithProvider(ModelResponse):
    """Schema for model with provider information"""
    provider: Dict[str, Any] = Field(..., description="Provider information")


class ModelHealthHistoryResponse(BaseModel):
    """Schema for model health history response"""
    id: int = Field(..., description="History record ID")
    model_id: int = Field(..., description="Model ID")
    health_status: str = Field(..., description="Health status")
    response_time_ms: Optional[int] = Field(None, description="Response time in milliseconds")
    error_message: Optional[str] = Field(None, description="Error message")
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
    by_modality: Dict[str, int] = Field(default_factory=dict, description="Model count by modality")


class ModelSearchQuery(BaseModel):
    """Schema for model search query"""
    query: str = Field(..., description="Search query")
    provider_id: Optional[int] = Field(None, description="Optional provider filter")
    limit: int = Field(100, description="Maximum results", ge=1, le=1000)
    offset: int = Field(0, description="Offset for pagination", ge=0)


class ModelListQuery(BaseModel):
    """Schema for model list query parameters"""
    provider_id: Optional[int] = Field(None, description="Filter by provider ID")
    provider_slug: Optional[str] = Field(None, description="Filter by provider slug")
    is_active_only: bool = Field(True, description="Only return active models")
    health_status: Optional[str] = Field(None, description="Filter by health status")
    modality: Optional[str] = Field(None, description="Filter by modality")
    limit: int = Field(100, description="Maximum results", ge=1, le=1000)
    offset: int = Field(0, description="Offset for pagination", ge=0)
