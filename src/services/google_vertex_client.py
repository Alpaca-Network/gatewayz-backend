"""Google Vertex AI API client for chat completions

This module provides integration with Google Vertex AI generative models
using the official google-cloud-aiplatform SDK.
"""

import logging
from typing import Any, Iterator, Optional
import google.auth
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google.cloud.aiplatform_v1.services.prediction_service import PredictionServiceClient
from google.cloud.aiplatform_v1.types import PredictRequest
from google.protobuf.json_format import MessageToDict
import json
import time

from src.config import Config

# Initialize logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)


def get_google_vertex_credentials():
    """Get Google Cloud credentials for Vertex AI

    Uses service account credentials if GOOGLE_APPLICATION_CREDENTIALS is set,
    otherwise falls back to default application credentials.
    """
    try:
        if Config.GOOGLE_APPLICATION_CREDENTIALS:
            credentials = Credentials.from_service_account_file(
                Config.GOOGLE_APPLICATION_CREDENTIALS
            )
            credentials.refresh(Request())
        else:
            credentials, _ = google.auth.default()
            if not credentials.valid:
                credentials.refresh(Request())
        return credentials
    except Exception as e:
        logger.error(f"Failed to get Google Cloud credentials: {e}")
        raise


def get_google_vertex_client():
    """Get Google Vertex AI prediction client

    Returns a PredictionServiceClient configured with proper credentials
    and endpoint.
    """
    try:
        credentials = get_google_vertex_credentials()

        # Construct endpoint URL
        endpoint_url = (
            f"https://{Config.GOOGLE_VERTEX_LOCATION}-aiplatform.googleapis.com"
        )

        client = PredictionServiceClient(
            credentials=credentials,
            client_options={"api_endpoint": endpoint_url}
        )
        return client
    except Exception as e:
        logger.error(f"Failed to initialize Google Vertex client: {e}")
        raise


def transform_google_vertex_model_id(model_id: str) -> str:
    """Transform model ID to Google Vertex AI format

    Converts model IDs like 'gemini-2.0-flash' to full resource name format:
    'projects/{project}/locations/{location}/publishers/google/models/{model}'

    Args:
        model_id: Model identifier (e.g., 'gemini-2.0-flash', 'gemini-1.5-pro')

    Returns:
        Full Google Vertex AI model resource name
    """
    # If already in full format, return as-is
    if model_id.startswith("projects/"):
        return model_id

    # Otherwise, construct the full resource name
    return (
        f"projects/{Config.GOOGLE_PROJECT_ID}/"
        f"locations/{Config.GOOGLE_VERTEX_LOCATION}/"
        f"publishers/google/models/{model_id}"
    )


def make_google_vertex_request_openai(
    messages: list,
    model: str,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    **kwargs
) -> dict:
    """Make request to Google Vertex AI generative models

    Converts OpenAI-compatible parameters to Google Vertex AI format and
    returns a normalized response.

    Args:
        messages: List of message objects in OpenAI format
        model: Model name to use
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature (0-2)
        top_p: Nucleus sampling parameter
        **kwargs: Additional parameters (ignored for compatibility)

    Returns:
        OpenAI-compatible response object
    """
    try:
        client = get_google_vertex_client()
        model_resource = transform_google_vertex_model_id(model)

        # Build request content
        content = _build_vertex_content(messages)

        # Build generation config
        generation_config = {}
        if max_tokens is not None:
            generation_config["max_output_tokens"] = max_tokens
        if temperature is not None:
            generation_config["temperature"] = temperature
        if top_p is not None:
            generation_config["top_p"] = top_p

        # Prepare the predict request
        request_body = {
            "contents": content,
            "generation_config": generation_config if generation_config else None,
        }

        # Remove None values
        request_body = {k: v for k, v in request_body.items() if v is not None}

        request = PredictRequest(
            endpoint=model_resource,
            instances=[request_body]
        )

        # Make the request
        response = client.predict(request=request)

        # Process and normalize response
        return _process_google_vertex_response(response, model)

    except Exception as e:
        logger.error(f"Google Vertex AI request failed: {e}")
        raise


def make_google_vertex_request_openai_stream(
    messages: list,
    model: str,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    **kwargs
) -> Iterator[str]:
    """Make streaming request to Google Vertex AI

    NOTE: Google Vertex AI's Python SDK does not natively support streaming
    for non-Claude models. This implementation returns a non-streaming response
    in OpenAI SSE format for compatibility.

    Args:
        messages: List of message objects in OpenAI format
        model: Model name to use
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        top_p: Nucleus sampling parameter
        **kwargs: Additional parameters

    Yields:
        SSE-formatted stream chunks
    """
    try:
        # Get non-streaming response
        response = make_google_vertex_request_openai(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            **kwargs
        )

        # Convert to streaming format by yielding complete response as single chunk
        # This maintains compatibility with streaming clients
        chunk = {
            "id": response.get("id"),
            "object": "text_completion.chunk",
            "created": response.get("created"),
            "model": response.get("model"),
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": response["choices"][0]["message"]["content"]
                    },
                    "finish_reason": None
                }
            ]
        }

        yield f"data: {json.dumps(chunk)}\n\n"

        # Final chunk with finish_reason
        finish_chunk = {
            "id": response.get("id"),
            "object": "text_completion.chunk",
            "created": response.get("created"),
            "model": response.get("model"),
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": None},
                    "finish_reason": response["choices"][0].get("finish_reason", "stop")
                }
            ]
        }

        yield f"data: {json.dumps(finish_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"Google Vertex AI streaming request failed: {e}")
        raise


def _build_vertex_content(messages: list) -> list:
    """Convert OpenAI message format to Google Vertex AI content format

    Args:
        messages: List of OpenAI-format messages

    Returns:
        List of content objects in Vertex AI format
    """
    contents = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        # Map OpenAI roles to Vertex AI roles
        vertex_role = "user" if role == "user" else "model"

        # Handle content as string or list (for multimodal)
        if isinstance(content, str):
            parts = [{"text": content}]
        elif isinstance(content, list):
            parts = []
            for item in content:
                if item.get("type") == "text":
                    parts.append({"text": item.get("text", "")})
                elif item.get("type") == "image_url":
                    # Vertex AI supports inline base64 or URLs
                    image_url = item.get("image_url", {}).get("url", "")
                    if image_url.startswith("data:"):
                        # Base64 encoded image
                        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_url}})
                    else:
                        # URL reference
                        parts.append({"file_data": {"mime_type": "image/jpeg", "file_uri": image_url}})
        else:
            parts = [{"text": str(content)}]

        contents.append({
            "role": vertex_role,
            "parts": parts
        })

    return contents


def _process_google_vertex_response(response: Any, model: str) -> dict:
    """Process Google Vertex AI response to OpenAI-compatible format

    Args:
        response: Raw response from Vertex AI API
        model: Model name used

    Returns:
        OpenAI-compatible response dictionary
    """
    try:
        # Convert protobuf response to dictionary
        response_dict = MessageToDict(response)

        # Extract predictions
        predictions = response_dict.get("predictions", [])

        if not predictions:
            raise ValueError("No predictions in Vertex AI response")

        # Get the first prediction
        prediction = predictions[0]

        # Extract content from candidates
        candidates = prediction.get("candidates", [])
        if not candidates:
            raise ValueError("No candidates in Vertex AI prediction")

        candidate = candidates[0]
        content_parts = candidate.get("content", {}).get("parts", [])

        # Extract text from parts
        text_content = ""
        for part in content_parts:
            if "text" in part:
                text_content += part["text"]

        # Extract usage information
        usage_metadata = candidate.get("usageMetadata", {})
        prompt_tokens = int(usage_metadata.get("promptTokenCount", 0))
        completion_tokens = int(usage_metadata.get("candidatesTokenCount", 0))

        finish_reason = candidate.get("finishReason", "STOP")
        finish_reason_map = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "stop",
            "FINISH_REASON_UNSPECIFIED": "unknown"
        }

        return {
            "id": f"vertex-{int(time.time() * 1000)}",
            "object": "text_completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": text_content
                    },
                    "finish_reason": finish_reason_map.get(finish_reason, "stop")
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }
        }

    except Exception as e:
        logger.error(f"Failed to process Google Vertex AI response: {e}")
        raise


def process_google_vertex_response(response: Any) -> dict:
    """Alias for backward compatibility with existing patterns"""
    return _process_google_vertex_response(response, "google-vertex")
