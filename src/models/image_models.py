"""
Image generation models
"""

from typing import List, Optional, Literal, Dict, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImageGenerationRequest(BaseModel):
    """Request model for image generation"""

    model_config = ConfigDict(
        protected_namespaces=(),
        extra="allow"
    )

    prompt: str = Field(..., min_length=1)
    model: str = "stabilityai/sd3.5"
    size: str = "1024x1024"
    n: int = Field(default=1, gt=0)
    quality: Optional[Literal["standard", "hd"]] = "standard"
    style: Optional[Literal["natural", "vivid"]] = "natural"
    provider: Optional[str] = "deepinfra"  # Provider selection: "deepinfra", "portkey", or "google-vertex"
    portkey_provider: Optional[str] = "stability-ai"  # Sub-provider for Portkey
    portkey_virtual_key: Optional[str] = None  # Virtual key for Portkey
    google_project_id: Optional[str] = None  # Google Cloud project ID for Vertex AI
    google_location: Optional[str] = None  # Google Cloud region for Vertex AI
    google_endpoint_id: Optional[str] = None  # Vertex AI endpoint ID

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        """Ensure size follows WIDTHxHEIGHT format."""
        if value is None:
            return value
        parts = value.lower().split("x")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("Size must be in the format WIDTHxHEIGHT, e.g., 1024x1024")
        width, height = map(int, parts)
        if width <= 0 or height <= 0:
            raise ValueError("Size dimensions must be positive integers")
        return value


class ImageData(BaseModel):
    """Individual image data in response"""

    url: str
    b64_json: Optional[str] = None


class ImageGenerationResponse(BaseModel):
    """Response model for image generation"""

    created: int
    data: List[ImageData]
    provider: Optional[str] = None
    model: Optional[str] = None
    gateway_usage: Optional[Dict[str, Any]] = None
