"""
HuggingFace Task API Routes

These endpoints implement the HuggingFace task API standard for inference provider integration.
This enables Gatewayz to be used as an inference provider on the HuggingFace Hub.

Reference: https://huggingface.co/docs/inference-providers/
"""

import logging
import uuid
import time
import json
from typing import Optional, Any, Dict, Union
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Body
from fastapi.responses import JSONResponse, StreamingResponse

from src.schemas.huggingface_tasks import (
    TextGenerationInput,
    TextGenerationResponse,
    TextGenerationOutput,
    ConversationalInput,
    ConversationalOutput,
    SummarizationInput,
    SummarizationOutput,
    TranslationInput,
    TranslationOutput,
    QuestionAnsweringInput,
    QuestionAnsweringOutput,
    TextClassificationResponse,
    TokenClassificationResponse,
    ImageGenerationInput,
    ImageGenerationOutput,
    ObjectDetectionResponse,
    EmbeddingResponse,
    TaskRequest,
    TaskResponse,
    ModelMapping,
    ModelMappingResponse,
    CostItem,
    BillingResponse,
    UsageRecord,
    UsageResponse,
)
from src.security.deps import get_api_key
from src.db import api_keys as api_keys_module
from src.db import users as users_module
from src.db import credit_transactions as credit_tx_module
from src.services import models as models_service
from src.services import pricing as pricing_service
from src.config import Config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hf/tasks", tags=["huggingface_tasks"])

# ============================================================================
# TEXT GENERATION ENDPOINTS
# ============================================================================

@router.post(
    "/text-generation",
    response_model=TextGenerationResponse,
    summary="Text Generation Task",
    description="Generate text given an input prompt"
)
async def text_generation(
    request: TextGenerationInput,
    api_key: str = Depends(get_api_key),
    stream: bool = Query(False, description="Stream response")
) -> Union[TextGenerationResponse, StreamingResponse]:
    """
    Text generation endpoint compatible with HuggingFace task API.
    Generates text continuations from input prompts.
    """
    request_id = str(uuid.uuid4())

    try:
        # Validate API key and get user
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = key_data.get("user_id")

        # Use gpt-3.5-turbo as default model for text generation
        model = request.parameters.get("model", "gpt-3.5-turbo") if request.parameters else "gpt-3.5-turbo"

        # Check rate limits
        # Note: Implement rate limiting logic here

        # Prepare inference request
        inference_request = {
            "model": model,
            "messages": [{"role": "user", "content": request.inputs}],
            "stream": stream,
            **(request.parameters or {}),
        }

        # Log start time for billing
        start_time = time.time()

        if stream:
            async def generate():
                async for chunk in _stream_inference(user_id, model, inference_request):
                    yield chunk

            return StreamingResponse(
                generate(),
                media_type="text/event-stream"
            )

        # Non-streaming response
        response = await _call_inference_provider(user_id, model, inference_request)

        # Extract generated text
        generated_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Log usage and deduct credits
        prompt_tokens = response.get("usage", {}).get("prompt_tokens", 0)
        completion_tokens = response.get("usage", {}).get("completion_tokens", 0)

        await _log_and_bill_usage(
            request_id=request_id,
            user_id=user_id,
            task="text-generation",
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

        return TextGenerationResponse(
            output=[TextGenerationOutput(generated_text=generated_text)]
        )

    except Exception as e:
        logger.error(f"Text generation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# CONVERSATIONAL ENDPOINTS
# ============================================================================

@router.post(
    "/conversational",
    response_model=ConversationalOutput,
    summary="Conversational Task",
    description="Generate responses in a conversation"
)
async def conversational(
    request: ConversationalInput,
    api_key: str = Depends(get_api_key),
) -> ConversationalOutput:
    """
    Conversational endpoint for multi-turn conversations.
    Compatible with HuggingFace task API.
    """
    request_id = str(uuid.uuid4())

    try:
        # Validate API key and get user
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = key_data.get("user_id")
        model = "gpt-3.5-turbo"

        # Build message history from past inputs/responses
        messages = []

        if request.past_user_inputs and request.generated_responses:
            for user_input, response in zip(request.past_user_inputs, request.generated_responses):
                messages.append({"role": "user", "content": user_input})
                messages.append({"role": "assistant", "content": response})

        # Add current text
        messages.append({"role": "user", "content": request.text})

        # Call inference
        inference_request = {
            "model": model,
            "messages": messages,
        }

        response = await _call_inference_provider(user_id, model, inference_request)

        generated_response = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Log usage
        prompt_tokens = response.get("usage", {}).get("prompt_tokens", 0)
        completion_tokens = response.get("usage", {}).get("completion_tokens", 0)

        await _log_and_bill_usage(
            request_id=request_id,
            user_id=user_id,
            task="conversational",
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

        # Update conversation history
        new_past_inputs = (request.past_user_inputs or []) + [request.text]
        new_responses = (request.generated_responses or []) + [generated_response]

        return ConversationalOutput(
            conversation={
                "past_user_inputs": new_past_inputs,
                "generated_responses": new_responses,
            }
        )

    except Exception as e:
        logger.error(f"Conversational error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# SUMMARIZATION ENDPOINTS
# ============================================================================

@router.post(
    "/summarization",
    response_model=SummarizationOutput,
    summary="Summarization Task",
    description="Summarize text"
)
async def summarization(
    request: SummarizationInput,
    api_key: str = Depends(get_api_key),
) -> SummarizationOutput:
    """Summarization endpoint"""
    request_id = str(uuid.uuid4())

    try:
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = key_data.get("user_id")
        model = "gpt-3.5-turbo"

        prompt = f"Summarize the following text:\n\n{request.inputs}"

        inference_request = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }

        response = await _call_inference_provider(user_id, model, inference_request)
        summary_text = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        prompt_tokens = response.get("usage", {}).get("prompt_tokens", 0)
        completion_tokens = response.get("usage", {}).get("completion_tokens", 0)

        await _log_and_bill_usage(
            request_id=request_id,
            user_id=user_id,
            task="summarization",
            model=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

        return SummarizationOutput(summary_text=summary_text)

    except Exception as e:
        logger.error(f"Summarization error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# GENERIC TASK ENDPOINT
# ============================================================================

@router.post(
    "/run",
    response_model=TaskResponse,
    summary="Run Generic Task",
    description="Run any supported task type"
)
async def run_task(
    request: TaskRequest,
    api_key: str = Depends(get_api_key),
) -> TaskResponse:
    """
    Generic task execution endpoint.
    Supports any registered task type.
    """
    request_id = str(uuid.uuid4())

    try:
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = key_data.get("user_id")
        model = request.model or "gpt-3.5-turbo"

        # Route to appropriate handler based on task type
        task_handlers = {
            "text-generation": _handle_text_generation_task,
            "conversational": _handle_conversational_task,
            "summarization": _handle_summarization_task,
            "translation": _handle_translation_task,
            "question-answering": _handle_qa_task,
        }

        handler = task_handlers.get(request.task)
        if not handler:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported task type: {request.task}"
            )

        output = await handler(user_id, model, request.inputs, request.parameters)

        return TaskResponse(
            output=output,
            task=request.task,
            model=model,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Task execution error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# MODEL MAPPING ENDPOINTS
# ============================================================================

@router.post(
    "/models/map",
    response_model=ModelMappingResponse,
    summary="Register Model Mapping",
    description="Register a mapping between provider model and HuggingFace Hub model"
)
async def register_model_mapping(
    mapping: ModelMapping,
    api_key: str = Depends(get_api_key),
) -> ModelMappingResponse:
    """
    Register a model mapping for discovery on HuggingFace Hub.
    """
    try:
        # Verify admin access
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # In production, check for admin role
        # For now, we'll allow any authenticated user (update as needed)

        # Store mapping in database or cache
        # This would be implemented in a dedicated module
        logger.info(
            f"Registered model mapping: {mapping.provider_model_id} -> "
            f"{mapping.hub_model_id} (task: {mapping.task_type})"
        )

        return ModelMappingResponse(
            success=True,
            provider_model_id=mapping.provider_model_id,
            hub_model_id=mapping.hub_model_id,
            message="Model mapping registered successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Model mapping error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/models",
    summary="List Available Models",
    description="List all available models and their mappings"
)
async def list_models(
    api_key: str = Depends(get_api_key),
    task_type: Optional[str] = Query(None, description="Filter by task type")
) -> Dict[str, Any]:
    """
    List all available models with their task types and mappings.
    """
    try:
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Get model catalog
        models = await models_service.get_cached_models()

        # Filter by task type if specified
        if task_type:
            models = [m for m in models if m.get("task_type") == task_type]

        return {
            "models": models,
            "count": len(models),
            "task_types": list(set(m.get("task_type", "unknown") for m in models))
        }

    except Exception as e:
        logger.error(f"Error listing models: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# BILLING / COST ENDPOINTS
# ============================================================================

@router.post(
    "/billing/cost",
    response_model=BillingResponse,
    summary="Calculate Request Cost",
    description="Calculate the cost of inference requests in nano-USD"
)
async def calculate_cost(
    request_data: Dict[str, Any] = Body(...),
    api_key: str = Depends(get_api_key),
) -> BillingResponse:
    """
    Calculate the cost of inference requests.
    Returns cost in nano-USD (1 nano-USD = 1e-9 USD).

    Request format:
    {
        "requests": [
            {
                "task": "text-generation",
                "model": "gpt-3.5-turbo",
                "input_tokens": 100,
                "output_tokens": 50
            }
        ]
    }
    """
    try:
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = key_data.get("user_id")
        requests = request_data.get("requests", [])

        costs = []
        total_cost_nano_usd = 0

        for req in requests:
            task = req.get("task", "")
            model = req.get("model", "")
            input_tokens = req.get("input_tokens", 0)
            output_tokens = req.get("output_tokens", 0)

            # Get pricing for this model
            price_per_1m_input = await pricing_service.get_model_pricing(model, "input")
            price_per_1m_output = await pricing_service.get_model_pricing(model, "output")

            # Calculate cost in USD then convert to nano-USD
            cost_usd = (
                (input_tokens * price_per_1m_input / 1_000_000) +
                (output_tokens * price_per_1m_output / 1_000_000)
            )
            cost_nano_usd = int(cost_usd * 1e9)

            costs.append(CostItem(
                task=task,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_nano_usd=cost_nano_usd,
            ))

            total_cost_nano_usd += cost_nano_usd

        return BillingResponse(
            total_cost_nano_usd=total_cost_nano_usd,
            costs=costs,
            currency="USD"
        )

    except Exception as e:
        logger.error(f"Cost calculation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/billing/usage",
    response_model=UsageResponse,
    summary="Get Usage Records",
    description="Get usage records for billing purposes"
)
async def get_usage(
    api_key: str = Depends(get_api_key),
    limit: int = Query(100, description="Max records to return"),
    offset: int = Query(0, description="Offset for pagination"),
) -> UsageResponse:
    """
    Get usage records for the authenticated API key.
    Returns records in reverse chronological order.
    """
    try:
        key_data = await api_keys_module.get_key_by_key_hash(api_key)
        if not key_data:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_id = key_data.get("user_id")

        # Get usage records from database
        # This would be implemented in credit_transactions module
        records = []  # Placeholder
        total_records = len(records)
        total_cost_nano_usd = sum(r.cost_nano_usd for r in records)

        return UsageResponse(
            records=records,
            total_records=total_records,
            total_cost_nano_usd=total_cost_nano_usd,
        )

    except Exception as e:
        logger.error(f"Error getting usage: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _call_inference_provider(
    user_id: int,
    model: str,
    request: Dict[str, Any]
) -> Dict[str, Any]:
    """Call the inference provider and return response"""
    # This would use the existing provider routing logic
    # For now, this is a placeholder
    # In production, this would delegate to src/services/openrouter_client.py or similar
    raise NotImplementedError("Provider routing not yet implemented")


async def _stream_inference(
    user_id: int,
    model: str,
    request: Dict[str, Any]
):
    """Stream inference response"""
    # This would handle streaming responses
    raise NotImplementedError("Streaming not yet implemented")


async def _log_and_bill_usage(
    request_id: str,
    user_id: int,
    task: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Log usage and deduct credits"""
    try:
        # Calculate cost
        price_per_1m_input = await pricing_service.get_model_pricing(model, "input")
        price_per_1m_output = await pricing_service.get_model_pricing(model, "output")

        cost_usd = (
            (input_tokens * price_per_1m_input / 1_000_000) +
            (output_tokens * price_per_1m_output / 1_000_000)
        )

        # Deduct from user credits
        # This would use existing deduct_credits function
        # await users_module.deduct_credits(user_id, cost_usd)

        # Log transaction
        # This would use credit_transactions module
        logger.info(
            f"Logged usage - Request: {request_id}, User: {user_id}, "
            f"Task: {task}, Model: {model}, Cost: ${cost_usd:.6f}"
        )

    except Exception as e:
        logger.error(f"Error logging usage: {str(e)}")


async def _handle_text_generation_task(
    user_id: int,
    model: str,
    inputs: Union[str, Dict[str, Any]],
    parameters: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Handle text generation task"""
    if isinstance(inputs, str):
        prompt = inputs
    else:
        prompt = inputs.get("text", "")

    # Call inference
    response = await _call_inference_provider(user_id, model, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        **(parameters or {}),
    })

    return {
        "generated_text": response.get("choices", [{}])[0].get("message", {}).get("content", "")
    }


async def _handle_conversational_task(
    user_id: int,
    model: str,
    inputs: Union[str, Dict[str, Any]],
    parameters: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Handle conversational task"""
    if isinstance(inputs, dict):
        text = inputs.get("text", "")
    else:
        text = inputs

    response = await _call_inference_provider(user_id, model, {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        **(parameters or {}),
    })

    return {
        "response": response.get("choices", [{}])[0].get("message", {}).get("content", "")
    }


async def _handle_summarization_task(
    user_id: int,
    model: str,
    inputs: Union[str, Dict[str, Any]],
    parameters: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Handle summarization task"""
    if isinstance(inputs, str):
        text = inputs
    else:
        text = inputs.get("text", "")

    prompt = f"Summarize the following text:\n\n{text}"

    response = await _call_inference_provider(user_id, model, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        **(parameters or {}),
    })

    return {
        "summary": response.get("choices", [{}])[0].get("message", {}).get("content", "")
    }


async def _handle_translation_task(
    user_id: int,
    model: str,
    inputs: Union[str, Dict[str, Any]],
    parameters: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Handle translation task"""
    if isinstance(inputs, dict):
        text = inputs.get("text", "")
        target_lang = inputs.get("target_language", "English")
    else:
        text = inputs
        target_lang = "English"

    prompt = f"Translate the following text to {target_lang}:\n\n{text}"

    response = await _call_inference_provider(user_id, model, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        **(parameters or {}),
    })

    return {
        "translation": response.get("choices", [{}])[0].get("message", {}).get("content", "")
    }


async def _handle_qa_task(
    user_id: int,
    model: str,
    inputs: Union[str, Dict[str, Any]],
    parameters: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Handle question answering task"""
    if isinstance(inputs, dict):
        question = inputs.get("question", "")
        context = inputs.get("context", "")
    else:
        question = inputs
        context = ""

    prompt = f"Answer this question based on the context:\n\nContext: {context}\n\nQuestion: {question}"

    response = await _call_inference_provider(user_id, model, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        **(parameters or {}),
    })

    return {
        "answer": response.get("choices", [{}])[0].get("message", {}).get("content", "")
    }
