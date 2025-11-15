"""
Gatewayz HuggingFace Inference Provider Client

Python client library for accessing Gatewayz models through HuggingFace's
inference provider network.

Usage:
    from gatewayz_py_hf import GatewayzClient

    client = GatewayzClient(api_key="your-api-key")
    response = await client.text_generation(
        inputs="Hello, world!",
        model="gpt-3.5-turbo"
    )
"""

__version__ = "0.1.0"
__author__ = "Terragon Labs"

from .client import GatewayzClient, AsyncGatewayzClient
from .types import (
    TextGenerationRequest,
    TextGenerationResponse,
    ConversationalRequest,
    ConversationalResponse,
    SummarizationRequest,
    SummarizationResponse,
    ModelInfo,
    BillingInfo,
)

__all__ = [
    "GatewayzClient",
    "AsyncGatewayzClient",
    "TextGenerationRequest",
    "TextGenerationResponse",
    "ConversationalRequest",
    "ConversationalResponse",
    "SummarizationRequest",
    "SummarizationResponse",
    "ModelInfo",
    "BillingInfo",
]
