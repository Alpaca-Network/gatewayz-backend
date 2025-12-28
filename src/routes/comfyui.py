"""
ComfyUI API Routes

Provides endpoints for:
- Workflow template listing and selection
- Workflow execution (image and video generation)
- Execution progress monitoring via SSE
- Execution history and results retrieval
- Server status checking
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from src.config import Config
from src.db.api_keys import increment_api_key_usage
from src.db.users import deduct_credits, get_user, record_usage
from src.models.comfyui_models import (
    ComfyUIExecutionRequest,
    ComfyUIExecutionResponse,
    ComfyUIHistoryItem,
    ComfyUIProgressUpdate,
    ComfyUIServerStatus,
    ComfyUIWorkflowTemplate,
    ExecutionHistoryResponse,
    ExecutionStatus,
    WorkflowListResponse,
    WorkflowType,
)
from src.security.deps import get_api_key
from src.services.comfyui_client import get_comfyui_client
from src.services.comfyui_workflows import (
    get_workflow_template,
    get_workflow_templates_by_type,
    list_workflow_templates,
)
from src.utils.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/comfyui", tags=["comfyui"])


# =============================================================================
# SERVER STATUS
# =============================================================================

@router.get("/status", response_model=ComfyUIServerStatus)
async def get_server_status():
    """
    Get ComfyUI server connection status and system info.

    Returns server connection status, queue size, available models,
    and system statistics.
    """
    client = get_comfyui_client()
    return await client.get_server_status()


# =============================================================================
# WORKFLOW TEMPLATES
# =============================================================================

@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    workflow_type: WorkflowType | None = Query(None, description="Filter by workflow type")
):
    """
    List available workflow templates.

    Optionally filter by workflow type (text-to-image, image-to-video, etc.)
    """
    if workflow_type:
        workflows = get_workflow_templates_by_type(workflow_type)
    else:
        workflows = list_workflow_templates()

    return WorkflowListResponse(
        workflows=workflows,
        total=len(workflows)
    )


@router.get("/workflows/{workflow_id}", response_model=ComfyUIWorkflowTemplate)
async def get_workflow(workflow_id: str):
    """
    Get a specific workflow template by ID.

    Returns the full workflow template including the ComfyUI workflow JSON.
    """
    template = get_workflow_template(workflow_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return template


# =============================================================================
# WORKFLOW EXECUTION
# =============================================================================

@router.post("/execute", response_model=ComfyUIExecutionResponse)
async def execute_workflow(
    request: ComfyUIExecutionRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
):
    """
    Execute a ComfyUI workflow.

    You can either:
    - Specify a `workflow_id` to use a pre-defined template
    - Provide a custom `workflow_json` directly

    Parameters in the request (prompt, width, height, etc.) will be injected
    into the workflow automatically.

    Returns execution details including the execution_id for tracking progress.
    """
    tracker = PerformanceTracker(endpoint="/comfyui/execute")

    try:
        # Get running event loop for async operations
        loop = asyncio.get_running_loop()

        # Authenticate user
        with tracker.stage("auth_validation"):
            user = await loop.run_in_executor(None, get_user, api_key)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Determine workflow to use
        workflow_json: dict[str, Any] | None = None
        workflow_type = WorkflowType.CUSTOM
        credits_cost = 100  # Default cost

        if request.workflow_id:
            # Use pre-defined template
            template = get_workflow_template(request.workflow_id)
            if not template:
                raise HTTPException(
                    status_code=404,
                    detail=f"Workflow template '{request.workflow_id}' not found"
                )
            workflow_json = template.workflow_json
            workflow_type = template.type
            credits_cost = template.credits_per_run

        elif request.workflow_json:
            # Use custom workflow
            workflow_json = request.workflow_json
            workflow_type = WorkflowType.CUSTOM

        else:
            raise HTTPException(
                status_code=400,
                detail="Either workflow_id or workflow_json must be provided"
            )

        # Check credits
        if user["credits"] < credits_cost:
            raise HTTPException(
                status_code=402,
                detail=f"Insufficient credits. This workflow requires {credits_cost} credits. Available: {user['credits']}"
            )

        # Handle input image upload if provided
        client = get_comfyui_client()

        if request.input_image:
            try:
                upload_result = await client.upload_image(
                    request.input_image,
                    filename="input.png"
                )
                logger.info(f"Uploaded input image: {upload_result}")
                # The workflow should reference "input.png" for LoadImage nodes
            except Exception as e:
                logger.error(f"Failed to upload input image: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to upload input image: {str(e)}"
                )

        # Execute workflow
        start_time = time.monotonic()

        try:
            response = await client.execute_workflow(
                workflow=workflow_json,
                request=request,
                workflow_type=workflow_type
            )
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Workflow execution failed: {str(e)}"
            )

        elapsed = time.monotonic() - start_time

        # Deduct credits and record usage on success
        if response.status == ExecutionStatus.COMPLETED:
            try:
                await loop.run_in_executor(None, deduct_credits, api_key, credits_cost)
                response.credits_charged = credits_cost

                # Record usage
                cost = credits_cost * 0.02 / 1000  # Approximate cost
                await loop.run_in_executor(
                    None,
                    record_usage,
                    user["id"],
                    api_key,
                    f"comfyui:{workflow_type.value}",
                    credits_cost,
                    cost,
                    int(elapsed * 1000),
                )

                # Increment API key usage
                await loop.run_in_executor(None, increment_api_key_usage, api_key)

            except Exception as e:
                logger.error(f"Failed to process billing: {e}")
                # Don't fail the request if billing fails

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in workflow execution: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/execute/stream")
async def execute_workflow_stream(
    request: ComfyUIExecutionRequest,
    api_key: str = Depends(get_api_key),
):
    """
    Execute a ComfyUI workflow with real-time progress streaming via SSE.

    Returns a Server-Sent Events stream with progress updates during execution.
    The final event will contain the complete results.
    """
    # Authenticate user first
    loop = asyncio.get_running_loop()
    user = await loop.run_in_executor(None, get_user, api_key)

    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Determine workflow
    workflow_json: dict[str, Any] | None = None
    workflow_type = WorkflowType.CUSTOM
    credits_cost = 100

    if request.workflow_id:
        template = get_workflow_template(request.workflow_id)
        if not template:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow template '{request.workflow_id}' not found"
            )
        workflow_json = template.workflow_json
        workflow_type = template.type
        credits_cost = template.credits_per_run
    elif request.workflow_json:
        workflow_json = request.workflow_json
    else:
        raise HTTPException(
            status_code=400,
            detail="Either workflow_id or workflow_json must be provided"
        )

    # Check credits
    if user["credits"] < credits_cost:
        raise HTTPException(
            status_code=402,
            detail=f"Insufficient credits. Required: {credits_cost}, Available: {user['credits']}"
        )

    async def event_generator():
        """Generate SSE events for workflow progress"""
        client = get_comfyui_client()
        start_time = time.monotonic()

        try:
            # Upload input image if provided
            if request.input_image:
                yield {
                    "event": "progress",
                    "data": json.dumps({
                        "status": "uploading",
                        "message": "Uploading input image..."
                    })
                }
                await client.upload_image(request.input_image, "input.png")

            # Queue the workflow
            yield {
                "event": "progress",
                "data": json.dumps({
                    "status": "queued",
                    "message": "Workflow queued for execution"
                })
            }

            prompt_id = await client.queue_prompt(workflow_json, request)

            # Stream progress updates
            async for update in client.stream_progress(prompt_id):
                yield {
                    "event": "progress",
                    "data": json.dumps({
                        "execution_id": update.execution_id,
                        "status": update.status.value,
                        "progress": update.progress,
                        "current_node": update.current_node,
                        "message": update.message,
                        "preview_image": update.preview_image
                    })
                }

                if update.status in [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED]:
                    break

            # Get final results
            history = await client.get_history(prompt_id)
            elapsed = time.monotonic() - start_time

            if history:
                outputs = history.get("outputs", {})
                output_list = []

                for node_id, node_output in outputs.items():
                    images = node_output.get("images", [])
                    for img in images:
                        filename = img.get("filename")
                        subfolder = img.get("subfolder", "")

                        try:
                            import base64
                            image_bytes = await client.get_image(filename, subfolder)
                            b64_data = base64.b64encode(image_bytes).decode("utf-8")
                            output_list.append({
                                "type": "image",
                                "filename": filename,
                                "b64_data": f"data:image/png;base64,{b64_data}"
                            })
                        except Exception as e:
                            output_list.append({
                                "type": "image",
                                "filename": filename,
                                "error": str(e)
                            })

                    # Handle video/gif outputs
                    gifs = node_output.get("gifs", [])
                    for gif in gifs:
                        output_list.append({
                            "type": "video",
                            "filename": gif.get("filename"),
                            "url": f"{client.server_url}/view?filename={gif.get('filename')}&type=output"
                        })

                # Deduct credits
                try:
                    await loop.run_in_executor(None, deduct_credits, api_key, credits_cost)
                except Exception as e:
                    logger.error(f"Failed to deduct credits: {e}")

                yield {
                    "event": "complete",
                    "data": json.dumps({
                        "status": "completed",
                        "outputs": output_list,
                        "credits_charged": credits_cost,
                        "execution_time_ms": int(elapsed * 1000)
                    })
                }
            else:
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "status": "failed",
                        "error": "No results returned from ComfyUI"
                    })
                }

        except Exception as e:
            logger.error(f"Streaming execution error: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({
                    "status": "failed",
                    "error": str(e)
                })
            }

    return EventSourceResponse(event_generator())


# =============================================================================
# EXECUTION MANAGEMENT
# =============================================================================

@router.get("/executions/{execution_id}", response_model=ComfyUIExecutionResponse)
async def get_execution_status(
    execution_id: str,
    api_key: str = Depends(get_api_key),
):
    """
    Get the status and results of a workflow execution.

    Use this to poll for completion if not using the streaming endpoint.
    """
    client = get_comfyui_client()

    history = await client.get_history(execution_id)

    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"Execution '{execution_id}' not found"
        )

    # Determine status from history
    status = ExecutionStatus.COMPLETED
    outputs = []
    error = None

    status_data = history.get("status", {})
    if status_data.get("status_str") == "error":
        status = ExecutionStatus.FAILED
        messages = status_data.get("messages", [])
        if messages:
            error = messages[0].get("message", "Unknown error")

    # Extract outputs
    output_data = history.get("outputs", {})
    for node_id, node_output in output_data.items():
        images = node_output.get("images", [])
        for img in images:
            outputs.append({
                "type": "image",
                "filename": img.get("filename"),
                "url": f"{client.server_url}/view?filename={img.get('filename')}&subfolder={img.get('subfolder', '')}&type=output"
            })

        gifs = node_output.get("gifs", [])
        for gif in gifs:
            outputs.append({
                "type": "video",
                "filename": gif.get("filename"),
                "url": f"{client.server_url}/view?filename={gif.get('filename')}&type=output"
            })

    return ComfyUIExecutionResponse(
        execution_id=execution_id,
        status=status,
        progress=100 if status == ExecutionStatus.COMPLETED else 0,
        outputs=outputs,
        error=error,
        created_at=datetime.now(timezone.utc)  # Would come from DB in full implementation
    )


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    api_key: str = Depends(get_api_key),
):
    """
    Cancel a queued or running workflow execution.
    """
    client = get_comfyui_client()

    success = await client.cancel_execution(execution_id)

    if success:
        return {"status": "cancelled", "execution_id": execution_id}
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to cancel execution '{execution_id}'"
        )


# =============================================================================
# HISTORY
# =============================================================================

@router.get("/history", response_model=ExecutionHistoryResponse)
async def get_execution_history(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    workflow_type: WorkflowType | None = Query(None, description="Filter by workflow type"),
    api_key: str = Depends(get_api_key),
):
    """
    Get execution history for the authenticated user.

    Note: This is a placeholder that would integrate with database storage.
    Currently returns an empty list as executions are not persisted.
    """
    # In a full implementation, this would query the database for user's execution history
    # For now, return empty list

    return ExecutionHistoryResponse(
        history=[],
        total=0,
        page=page,
        page_size=page_size
    )


# =============================================================================
# UTILITY ENDPOINTS
# =============================================================================

@router.get("/models")
async def list_available_models():
    """
    List available checkpoint models on the ComfyUI server.
    """
    client = get_comfyui_client()
    status = await client.get_server_status()

    return {
        "models": status.available_models,
        "connected": status.connected
    }


@router.get("/queue")
async def get_queue_status():
    """
    Get the current ComfyUI queue status.
    """
    client = get_comfyui_client()
    status = await client.get_server_status()

    return {
        "queue_size": status.queue_size,
        "running_jobs": status.running_jobs,
        "connected": status.connected
    }
