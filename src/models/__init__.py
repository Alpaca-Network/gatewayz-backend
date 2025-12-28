"""
Models package for health monitoring, availability, and media generation
"""

# Import new health models
from .health_models import (
    HealthCheckRequest,
    HealthDashboardResponse,
    HealthStatus,
    HealthSummaryResponse,
    ModelAvailabilityRequest,
    ModelHealthResponse,
    ModelStatusResponse,
    ProviderAvailabilityRequest,
    ProviderHealthResponse,
    ProviderStatus,
    ProviderStatusResponse,
    SystemHealthResponse,
    UptimeMetricsResponse,
)

# Import existing models
from .image_models import ImageData, ImageGenerationRequest, ImageGenerationResponse

# Import ComfyUI models
from .comfyui_models import (
    ComfyUIExecutionRequest,
    ComfyUIExecutionResponse,
    ComfyUIHistoryItem,
    ComfyUIOutput,
    ComfyUIProgressUpdate,
    ComfyUIServerStatus,
    ComfyUIWorkflowTemplate,
    ExecutionHistoryResponse,
    ExecutionStatus,
    WorkflowListResponse,
    WorkflowType,
)

__all__ = [
    # Existing models
    "ImageGenerationRequest",
    "ImageGenerationResponse",
    "ImageData",
    # Health models
    "HealthStatus",
    "ProviderStatus",
    "ModelHealthResponse",
    "ProviderHealthResponse",
    "SystemHealthResponse",
    "HealthSummaryResponse",
    "ModelAvailabilityRequest",
    "ProviderAvailabilityRequest",
    "HealthCheckRequest",
    "UptimeMetricsResponse",
    "ModelStatusResponse",
    "ProviderStatusResponse",
    "HealthDashboardResponse",
    # ComfyUI models
    "WorkflowType",
    "ExecutionStatus",
    "ComfyUIWorkflowTemplate",
    "ComfyUIExecutionRequest",
    "ComfyUIExecutionResponse",
    "ComfyUIOutput",
    "ComfyUIProgressUpdate",
    "ComfyUIHistoryItem",
    "ComfyUIServerStatus",
    "WorkflowListResponse",
    "ExecutionHistoryResponse",
]
