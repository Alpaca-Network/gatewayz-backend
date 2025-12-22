from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ModelRequestTimeSeriesBase(BaseModel):
    """Base schema for model request time series data"""
    request_id: UUID = Field(..., description="External request ID")
    user_id: Optional[UUID] = Field(None, description="User ID")
    provider_id: int = Field(..., description="Provider ID")
    model_id: int = Field(..., description="Model ID")
    timestamp: datetime = Field(..., description="Request completion timestamp")
    latency_ms: int = Field(..., description="Wall-clock elapsed time in milliseconds")
    input_tokens: int = Field(..., description="Number of input tokens")
    output_tokens: int = Field(..., description="Number of output tokens")
    status: str = Field(..., description="Request status (success, timeout, error)")
    cost_usd: Optional[float] = Field(None, description="Cost in USD")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class ModelRequestTimeSeriesCreate(ModelRequestTimeSeriesBase):
    """Schema for creating a time series record"""
    pass


class ModelRequestTimeSeries(ModelRequestTimeSeriesBase):
    """Schema for reading a time series record"""
    id: int = Field(..., description="Primary key")
    total_tokens: Optional[int] = Field(None, description="Total tokens (generated)")
    tokens_per_second: Optional[float] = Field(None, description="Tokens per second (calculated)")

    class Config:
        from_attributes = True


class ModelRequestMinuteRollupBase(BaseModel):
    """Base schema for minute rollup data"""
    bucket: datetime = Field(..., description="Time bucket (minute truncated)")
    model_id: int = Field(..., description="Model ID")
    provider_id: int = Field(..., description="Provider ID")
    request_count: int = Field(..., description="Number of requests")
    sum_input_tokens: int = Field(..., description="Total input tokens")
    sum_output_tokens: int = Field(..., description="Total output tokens")
    sum_total_tokens: int = Field(..., description="Total tokens")
    avg_latency_ms: float = Field(..., description="Average latency in ms")
    p95_latency_ms: Optional[float] = Field(None, description="95th percentile latency")
    p99_latency_ms: Optional[float] = Field(None, description="99th percentile latency")
    avg_tokens_per_second: Optional[float] = Field(None, description="Average tokens per second")


class ModelRequestMinuteRollupCreate(ModelRequestMinuteRollupBase):
    """Schema for creating a rollup record"""
    pass


class ModelRequestMinuteRollupUpdate(BaseModel):
    """Schema for updating a rollup record"""
    request_count: Optional[int] = None
    sum_input_tokens: Optional[int] = None
    sum_output_tokens: Optional[int] = None
    sum_total_tokens: Optional[int] = None
    avg_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None
    p99_latency_ms: Optional[float] = None
    avg_tokens_per_second: Optional[float] = None


class ModelRequestMinuteRollup(ModelRequestMinuteRollupBase):
    """Schema for reading a rollup record"""
    class Config:
        from_attributes = True
