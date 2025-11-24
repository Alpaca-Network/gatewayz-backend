"""
HuggingFace Task API Schemas

These schemas implement the standard HuggingFace task APIs for integration
as an inference provider on the HuggingFace Hub.

Reference: https://huggingface.co/docs/inference-providers/
"""

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# ============================================================================
# TEXT GENERATION (LLM) - Supported by HuggingFace Inference API
# ============================================================================

class TextGenerationInput(BaseModel):
    """Input for text generation tasks"""
    inputs: str = Field(..., description="The input text to generate from")
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional generation parameters (temperature, max_new_tokens, etc)"
    )


class TextGenerationOutput(BaseModel):
    """Output for text generation tasks"""
    generated_text: str = Field(..., description="The generated text")


class TextGenerationResponse(BaseModel):
    """Response for text generation tasks"""
    output: List[TextGenerationOutput] = Field(
        ...,
        description="List of generated outputs"
    )


# ============================================================================
# CONVERSATIONAL (Chat) - Supported by HuggingFace Inference API
# ============================================================================

class ConversationMessage(BaseModel):
    """Message in a conversation"""
    role: str = Field(..., description="Role of the speaker (user, assistant, system)")
    content: str = Field(..., description="Content of the message")


class ConversationalInput(BaseModel):
    """Input for conversational tasks"""
    past_user_inputs: Optional[List[str]] = Field(
        None,
        description="List of previous user inputs"
    )
    generated_responses: Optional[List[str]] = Field(
        None,
        description="List of previously generated responses"
    )
    text: str = Field(..., description="The current user input")


class ConversationalOutput(BaseModel):
    """Output for conversational tasks"""
    conversation: Dict[str, List[str]] = Field(
        ...,
        description="Dictionary with 'past_user_inputs' and 'generated_responses'"
    )
    warnings: Optional[List[str]] = Field(
        None,
        description="Any warnings during generation"
    )


# ============================================================================
# SUMMARIZATION
# ============================================================================

class SummarizationInput(BaseModel):
    """Input for summarization tasks"""
    inputs: str = Field(..., description="Text to summarize")
    parameters: Optional[Dict[str, Any]] = Field(None)


class SummarizationOutput(BaseModel):
    """Output for summarization tasks"""
    summary_text: str = Field(..., description="The generated summary")


# ============================================================================
# TRANSLATION
# ============================================================================

class TranslationInput(BaseModel):
    """Input for translation tasks"""
    inputs: str = Field(..., description="Text to translate")


class TranslationOutput(BaseModel):
    """Output for translation tasks"""
    translation_text: str = Field(..., description="The translated text")


# ============================================================================
# QUESTION ANSWERING
# ============================================================================

class QuestionAnsweringInput(BaseModel):
    """Input for question answering tasks"""
    question: str = Field(..., description="The question to answer")
    context: str = Field(..., description="The context to answer from")


class QuestionAnsweringOutput(BaseModel):
    """Output for question answering tasks"""
    answer: str = Field(..., description="The answer to the question")
    score: Optional[float] = Field(None, description="Confidence score")
    start: Optional[int] = Field(None, description="Start position in context")
    end: Optional[int] = Field(None, description="End position in context")


# ============================================================================
# TEXT CLASSIFICATION / SENTIMENT ANALYSIS
# ============================================================================

class TextClassificationOutput(BaseModel):
    """Output for text classification tasks"""
    label: str = Field(..., description="The predicted label")
    score: float = Field(..., description="Confidence score for the label")


class TextClassificationResponse(BaseModel):
    """Response for text classification tasks"""
    output: List[List[TextClassificationOutput]] = Field(
        ...,
        description="List of predictions per input"
    )


# ============================================================================
# TOKEN CLASSIFICATION / NER
# ============================================================================

class TokenClassificationOutput(BaseModel):
    """Output for token classification tasks"""
    entity: str = Field(..., description="The entity type")
    score: float = Field(..., description="Confidence score")
    index: int = Field(..., description="Token index")
    word: str = Field(..., description="The token/word")
    start: Optional[int] = Field(None, description="Start position")
    end: Optional[int] = Field(None, description="End position")


class TokenClassificationResponse(BaseModel):
    """Response for token classification tasks"""
    output: List[TokenClassificationOutput] = Field(
        ...,
        description="List of entity predictions"
    )


# ============================================================================
# IMAGE GENERATION
# ============================================================================

class ImageGenerationInput(BaseModel):
    """Input for image generation tasks"""
    inputs: str = Field(..., description="Text prompt for image generation")
    parameters: Optional[Dict[str, Any]] = Field(None)


class ImageGenerationOutput(BaseModel):
    """Output for image generation tasks"""
    image: str = Field(..., description="Base64 encoded image")
    content_type: str = Field(default="image/png", description="MIME type")


# ============================================================================
# OBJECT DETECTION / VISION
# ============================================================================

class DetectionOutput(BaseModel):
    """Output for object detection tasks"""
    label: str = Field(..., description="Object label")
    score: float = Field(..., description="Confidence score")
    box: Dict[str, float] = Field(
        ...,
        description="Bounding box with xmin, ymin, xmax, ymax"
    )


class ObjectDetectionResponse(BaseModel):
    """Response for object detection tasks"""
    output: List[DetectionOutput] = Field(
        ...,
        description="List of detected objects"
    )


# ============================================================================
# EMBEDDING / FEATURE EXTRACTION
# ============================================================================

class EmbeddingOutput(BaseModel):
    """Output for embedding/feature extraction tasks"""
    embedding: List[float] = Field(..., description="The embedding vector")


class EmbeddingResponse(BaseModel):
    """Response for embedding tasks"""
    output: List[EmbeddingOutput] = Field(
        ...,
        description="List of embeddings (one per input)"
    )


# ============================================================================
# GENERIC TASK ENDPOINT (Flexible)
# ============================================================================

class TaskRequest(BaseModel):
    """Generic task request for flexible input"""
    task: str = Field(..., description="The task type (e.g., 'text-generation')")
    model: Optional[str] = Field(None, description="Model to use (optional)")
    inputs: Union[str, List[str], Dict[str, Any]] = Field(
        ...,
        description="Task inputs (format depends on task type)"
    )
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Task-specific parameters"
    )
    options: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional options (e.g., wait_for_model, use_cache)"
    )


class TaskResponse(BaseModel):
    """Generic task response"""
    output: Any = Field(..., description="Task output")
    task: str = Field(..., description="The task that was executed")
    model: Optional[str] = Field(None, description="Model used")


# ============================================================================
# ERROR RESPONSES
# ============================================================================

class ErrorDetail(BaseModel):
    """Error detail information"""
    error: str = Field(..., description="Error message")
    error_type: Optional[str] = Field(None, description="Error type/code")
    message: Optional[str] = Field(None, description="Additional message")


# ============================================================================
# MODEL MAPPING API
# ============================================================================

class ModelMapping(BaseModel):
    """Mapping between provider model and HuggingFace Hub model"""
    provider_model_id: str = Field(
        ...,
        description="The model ID in the provider's catalog"
    )
    hub_model_id: str = Field(
        ...,
        description="Corresponding HuggingFace Hub model ID"
    )
    task_type: str = Field(
        ...,
        description="Task type (e.g., 'text-generation', 'image-generation')"
    )
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Default parameters for this model mapping"
    )


class ModelMappingResponse(BaseModel):
    """Response from model mapping registration"""
    success: bool = Field(...)
    provider_model_id: str = Field(...)
    hub_model_id: str = Field(...)
    message: Optional[str] = Field(None)


# ============================================================================
# BILLING / COST ENDPOINT
# ============================================================================

class CostItem(BaseModel):
    """Cost for a single request"""
    task: str = Field(..., description="Task type")
    model: str = Field(..., description="Model used")
    input_tokens: Optional[int] = Field(None, description="Number of input tokens")
    output_tokens: Optional[int] = Field(
        None,
        description="Number of output tokens generated"
    )
    cost_nano_usd: int = Field(
        ...,
        description="Cost in nano-USD (1 nano-USD = 1e-9 USD)"
    )


class BillingResponse(BaseModel):
    """Response containing billing information"""
    total_cost_nano_usd: int = Field(
        ...,
        description="Total cost in nano-USD"
    )
    costs: List[CostItem] = Field(
        ...,
        description="Breakdown of costs per request"
    )
    currency: str = Field(default="USD", description="Currency (always USD)")


class UsageRecord(BaseModel):
    """Record of API usage for billing"""
    request_id: str = Field(..., description="Unique request identifier")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    task: str = Field(..., description="Task type")
    model: str = Field(..., description="Model used")
    input_tokens: Optional[int] = Field(None)
    output_tokens: Optional[int] = Field(None)
    cost_nano_usd: int = Field(...)
    user_id: Optional[str] = Field(None, description="User identifier (from API key)")


class UsageResponse(BaseModel):
    """Response containing usage records"""
    records: List[UsageRecord] = Field(...)
    total_records: int = Field(...)
    total_cost_nano_usd: int = Field(...)
