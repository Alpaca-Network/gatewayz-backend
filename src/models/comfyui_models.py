"""
ComfyUI models for workflow execution and management
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowType(str, Enum):
    """Types of ComfyUI workflows"""
    TEXT_TO_IMAGE = "text-to-image"
    IMAGE_TO_IMAGE = "image-to-image"
    TEXT_TO_VIDEO = "text-to-video"
    IMAGE_TO_VIDEO = "image-to-video"
    UPSCALE = "upscale"
    INPAINT = "inpaint"
    OUTPAINT = "outpaint"
    CUSTOM = "custom"


class ExecutionStatus(str, Enum):
    """Status of workflow execution"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ComfyUIWorkflowTemplate(BaseModel):
    """Template for a ComfyUI workflow"""
    model_config = ConfigDict(protected_namespaces=())

    id: str
    name: str
    description: str
    type: WorkflowType
    workflow_json: dict[str, Any]  # The actual ComfyUI workflow API JSON
    thumbnail_url: str | None = None
    default_params: dict[str, Any] = Field(default_factory=dict)
    param_schema: dict[str, Any] = Field(default_factory=dict)  # JSON Schema for parameters
    credits_per_run: int = 100  # Default credits cost
    estimated_time_seconds: int = 30  # Estimated generation time
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ComfyUIExecutionRequest(BaseModel):
    """Request to execute a ComfyUI workflow"""
    model_config = ConfigDict(protected_namespaces=(), extra="allow")

    workflow_id: str | None = None  # Use a pre-defined workflow template
    workflow_json: dict[str, Any] | None = None  # Or provide custom workflow JSON
    params: dict[str, Any] = Field(default_factory=dict)  # Parameters to inject into workflow

    # Common parameters for image/video generation
    prompt: str | None = None
    negative_prompt: str | None = None
    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg_scale: float = 7.0
    seed: int | None = None  # None = random seed

    # For image-to-image / video workflows
    input_image: str | None = None  # Base64 encoded image
    input_video: str | None = None  # Base64 encoded video or URL
    denoise_strength: float = 0.75

    # Video specific
    frames: int = 16
    fps: int = 8


class ComfyUIExecutionResponse(BaseModel):
    """Response from workflow execution"""
    model_config = ConfigDict(protected_namespaces=())

    execution_id: str
    status: ExecutionStatus
    workflow_type: WorkflowType | None = None
    progress: float = 0.0  # 0-100
    current_node: str | None = None  # Currently executing node
    queue_position: int | None = None
    estimated_time_remaining: int | None = None  # seconds
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Results (populated when completed)
    outputs: list[dict[str, Any]] = Field(default_factory=list)  # Generated images/videos
    error: str | None = None

    # Usage info
    credits_charged: int | None = None
    execution_time_ms: int | None = None


class ComfyUIOutput(BaseModel):
    """Individual output from ComfyUI execution"""
    type: Literal["image", "video", "audio", "text"]
    url: str | None = None
    b64_data: str | None = None
    filename: str | None = None
    content_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None  # For video/audio
    metadata: dict[str, Any] = Field(default_factory=dict)


class ComfyUIProgressUpdate(BaseModel):
    """WebSocket progress update during execution"""
    execution_id: str
    status: ExecutionStatus
    progress: float
    current_node: str | None = None
    node_progress: float | None = None  # Progress within current node
    preview_image: str | None = None  # Base64 preview during generation
    message: str | None = None


class ComfyUIHistoryItem(BaseModel):
    """History item for past executions"""
    model_config = ConfigDict(protected_namespaces=())

    execution_id: str
    user_id: int
    workflow_id: str | None = None
    workflow_type: WorkflowType
    status: ExecutionStatus
    prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: list[ComfyUIOutput] = Field(default_factory=list)
    credits_charged: int
    execution_time_ms: int | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class ComfyUIServerStatus(BaseModel):
    """Status of connected ComfyUI server"""
    connected: bool
    server_url: str | None = None
    queue_size: int = 0
    running_jobs: int = 0
    available_models: list[str] = Field(default_factory=list)
    system_stats: dict[str, Any] = Field(default_factory=dict)
    last_ping: datetime | None = None


class WorkflowListResponse(BaseModel):
    """Response for listing available workflows"""
    workflows: list[ComfyUIWorkflowTemplate]
    total: int


class ExecutionHistoryResponse(BaseModel):
    """Response for execution history"""
    history: list[ComfyUIHistoryItem]
    total: int
    page: int
    page_size: int
