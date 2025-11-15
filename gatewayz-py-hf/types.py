"""
Type definitions for Gatewayz HuggingFace client
"""

from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from enum import Enum


class TaskType(str, Enum):
    """Supported task types"""
    TEXT_GENERATION = "text-generation"
    CONVERSATIONAL = "conversational"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    QUESTION_ANSWERING = "question-answering"
    TEXT_CLASSIFICATION = "text-classification"
    TOKEN_CLASSIFICATION = "token-classification"
    IMAGE_GENERATION = "image-generation"
    EMBEDDING = "embedding"


@dataclass
class TextGenerationRequest:
    """Text generation request"""
    inputs: str
    parameters: Optional[Dict[str, Any]] = None
    model: Optional[str] = "gpt-3.5-turbo"


@dataclass
class TextGenerationOutput:
    """Text generation output"""
    generated_text: str


@dataclass
class TextGenerationResponse:
    """Text generation response"""
    output: List[TextGenerationOutput]


@dataclass
class ConversationalMessage:
    """Message in a conversation"""
    role: str  # "user" or "assistant"
    content: str


@dataclass
class ConversationalRequest:
    """Conversational request"""
    past_user_inputs: Optional[List[str]] = None
    generated_responses: Optional[List[str]] = None
    text: Optional[str] = None


@dataclass
class ConversationalResponse:
    """Conversational response"""
    conversation: Dict[str, List[str]]
    warnings: Optional[List[str]] = None


@dataclass
class SummarizationRequest:
    """Summarization request"""
    inputs: str
    parameters: Optional[Dict[str, Any]] = None


@dataclass
class SummarizationOutput:
    """Summarization output"""
    summary_text: str


@dataclass
class SummarizationResponse:
    """Summarization response"""
    output: SummarizationOutput


@dataclass
class TranslationRequest:
    """Translation request"""
    inputs: str
    target_language: Optional[str] = "English"


@dataclass
class TranslationOutput:
    """Translation output"""
    translation_text: str


@dataclass
class QuestionAnsweringRequest:
    """Question answering request"""
    question: str
    context: str


@dataclass
class QuestionAnsweringOutput:
    """Question answering output"""
    answer: str
    score: Optional[float] = None


@dataclass
class ModelInfo:
    """Information about a model"""
    model_id: str
    hub_model_id: str
    task_type: str
    provider: str = "Gatewayz"
    parameters: Optional[Dict[str, Any]] = None


@dataclass
class CostInfo:
    """Cost information for a request"""
    task: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_nano_usd: int  # 1 nano-USD = 1e-9 USD
    cost_usd: Optional[float] = None


@dataclass
class BillingInfo:
    """Billing information"""
    total_cost_nano_usd: int
    costs: List[CostInfo]
    currency: str = "USD"


@dataclass
class UsageRecord:
    """Usage record for billing"""
    request_id: str
    timestamp: str
    task: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_nano_usd: int


@dataclass
class UsageResponse:
    """Response containing usage records"""
    records: List[UsageRecord]
    total_records: int
    total_cost_nano_usd: int
    total_cost_usd: Optional[float] = None
