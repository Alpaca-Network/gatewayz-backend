"""
ComfyUI API Client

Provides async HTTP and WebSocket communication with ComfyUI servers.
Supports workflow execution, progress monitoring, and result retrieval.
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from src.config import Config
from src.models.comfyui_models import (
    ComfyUIExecutionRequest,
    ComfyUIExecutionResponse,
    ComfyUIServerStatus,
    ExecutionStatus,
)

logger = logging.getLogger(__name__)

# ComfyUI API configuration
COMFYUI_REQUEST_TIMEOUT = 30.0  # seconds for regular requests
COMFYUI_GENERATION_TIMEOUT = 600.0  # 10 minutes for generation
COMFYUI_WS_TIMEOUT = 300.0  # 5 minutes for WebSocket
COMFYUI_POLL_INTERVAL = 1.0  # seconds between status polls


class ComfyUIClient:
    """
    Async client for ComfyUI API communication.

    Supports both HTTP REST endpoints and WebSocket for real-time progress.
    """

    def __init__(self, server_url: str | None = None):
        """
        Initialize ComfyUI client.

        Args:
            server_url: ComfyUI server URL (e.g., http://localhost:8188)
                       If not provided, uses COMFYUI_SERVER_URL from config
        """
        self.server_url = server_url or getattr(Config, 'COMFYUI_SERVER_URL', None)
        if not self.server_url:
            logger.warning("COMFYUI_SERVER_URL not configured")

        self.client_id = str(uuid.uuid4())
        self._http_client: httpx.AsyncClient | None = None
        self._ws_connection = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=COMFYUI_GENERATION_TIMEOUT,
                    write=30.0,
                    pool=10.0
                )
            )
        return self._http_client

    async def close(self):
        """Close HTTP client and WebSocket connections"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        if self._ws_connection:
            await self._ws_connection.close()

    async def get_server_status(self) -> ComfyUIServerStatus:
        """
        Get ComfyUI server status and system stats.

        Returns:
            ComfyUIServerStatus with connection info and queue status
        """
        if not self.server_url:
            return ComfyUIServerStatus(connected=False)

        try:
            client = await self._get_http_client()

            # Get system stats
            response = await client.get(f"{self.server_url}/system_stats")
            response.raise_for_status()
            system_stats = response.json()

            # Get queue status
            queue_response = await client.get(f"{self.server_url}/queue")
            queue_response.raise_for_status()
            queue_data = queue_response.json()

            queue_size = len(queue_data.get("queue_pending", []))
            running_jobs = len(queue_data.get("queue_running", []))

            # Get available models (object_info endpoint)
            models = []
            try:
                models_response = await client.get(f"{self.server_url}/object_info/CheckpointLoaderSimple")
                if models_response.status_code == 200:
                    models_data = models_response.json()
                    checkpoint_info = models_data.get("CheckpointLoaderSimple", {})
                    input_info = checkpoint_info.get("input", {}).get("required", {})
                    ckpt_names = input_info.get("ckpt_name", [[]])[0]
                    models = ckpt_names if isinstance(ckpt_names, list) else []
            except Exception as e:
                logger.warning(f"Failed to get available models: {e}")

            return ComfyUIServerStatus(
                connected=True,
                server_url=self.server_url,
                queue_size=queue_size,
                running_jobs=running_jobs,
                available_models=models,
                system_stats=system_stats,
                last_ping=datetime.now(timezone.utc)
            )

        except Exception as e:
            logger.error(f"Failed to get ComfyUI server status: {e}")
            return ComfyUIServerStatus(
                connected=False,
                server_url=self.server_url
            )

    async def upload_image(self, image_data: str, filename: str = "input.png") -> dict[str, Any]:
        """
        Upload an image to ComfyUI server.

        Args:
            image_data: Base64 encoded image data
            filename: Filename for the uploaded image

        Returns:
            Upload response with filename and subfolder info
        """
        if not self.server_url:
            raise ValueError("ComfyUI server URL not configured")

        client = await self._get_http_client()

        # Decode base64 image
        if image_data.startswith("data:"):
            # Remove data URL prefix
            parts = image_data.split(",", 1)
            if len(parts) != 2:
                raise ValueError("Invalid data URL format: missing comma separator")
            image_data = parts[1]

        image_bytes = base64.b64decode(image_data)

        # Upload to ComfyUI
        files = {"image": (filename, image_bytes, "image/png")}
        data = {"overwrite": "true"}

        response = await client.post(
            f"{self.server_url}/upload/image",
            files=files,
            data=data
        )
        response.raise_for_status()

        return response.json()

    def _inject_params_into_workflow(
        self,
        workflow: dict[str, Any],
        request: ComfyUIExecutionRequest
    ) -> dict[str, Any]:
        """
        Inject parameters from request into workflow JSON.

        Searches for common node types and updates their inputs.
        """
        workflow = json.loads(json.dumps(workflow))  # Deep copy

        # First pass: identify which CLIPTextEncode nodes are for negative prompts
        # by checking if they connect to KSampler's "negative" input
        negative_prompt_nodes: set[str] = set()
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type", "")
            if class_type in ["KSampler", "KSamplerAdvanced"]:
                inputs = node.get("inputs", {})
                # Check negative input - it's usually a list like ["node_id", output_index]
                negative_input = inputs.get("negative")
                if isinstance(negative_input, list) and len(negative_input) >= 1:
                    negative_prompt_nodes.add(str(negative_input[0]))

        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue

            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            # Text prompt nodes - inject positive or negative based on connection analysis
            if class_type in ["CLIPTextEncode", "CLIPTextEncodeSDXL"]:
                if node_id in negative_prompt_nodes:
                    # This is a negative prompt node
                    if request.negative_prompt and "text" in inputs:
                        inputs["text"] = request.negative_prompt
                else:
                    # This is a positive prompt node
                    if request.prompt and "text" in inputs:
                        inputs["text"] = request.prompt

            # KSampler nodes
            if class_type in ["KSampler", "KSamplerAdvanced"]:
                if request.seed is not None:
                    inputs["seed"] = request.seed
                if request.steps:
                    inputs["steps"] = request.steps
                if request.cfg_scale:
                    inputs["cfg"] = request.cfg_scale
                if request.denoise_strength and "denoise" in inputs:
                    inputs["denoise"] = request.denoise_strength

            # Empty latent image (for dimensions)
            if class_type == "EmptyLatentImage":
                if request.width:
                    inputs["width"] = request.width
                if request.height:
                    inputs["height"] = request.height

            # Video nodes
            if class_type in ["AnimateDiffLoader", "SVD_img2vid_Conditioning"]:
                if request.frames:
                    if "frame_count" in inputs:
                        inputs["frame_count"] = request.frames
                    if "video_frames" in inputs:
                        inputs["video_frames"] = request.frames

            node["inputs"] = inputs

        return workflow

    async def queue_prompt(
        self,
        workflow: dict[str, Any],
        request: ComfyUIExecutionRequest | None = None
    ) -> str:
        """
        Queue a workflow for execution.

        Args:
            workflow: ComfyUI workflow JSON (API format)
            request: Optional request with parameters to inject

        Returns:
            prompt_id for tracking execution
        """
        if not self.server_url:
            raise ValueError("ComfyUI server URL not configured")

        # Inject parameters if request provided
        if request:
            workflow = self._inject_params_into_workflow(workflow, request)

        client = await self._get_http_client()

        payload = {
            "prompt": workflow,
            "client_id": self.client_id
        }

        response = await client.post(
            f"{self.server_url}/prompt",
            json=payload
        )
        response.raise_for_status()

        result = response.json()
        prompt_id = result.get("prompt_id")

        if not prompt_id:
            raise ValueError(f"No prompt_id in response: {result}")

        logger.info(f"Queued ComfyUI prompt: {prompt_id}")
        return prompt_id

    async def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        """
        Get execution history/results for a prompt.

        Args:
            prompt_id: The prompt ID to check

        Returns:
            History data with outputs if completed, None if not found
        """
        if not self.server_url:
            raise ValueError("ComfyUI server URL not configured")

        client = await self._get_http_client()

        response = await client.get(f"{self.server_url}/history/{prompt_id}")

        if response.status_code == 404:
            return None

        response.raise_for_status()
        history = response.json()

        return history.get(prompt_id)

    async def get_image(
        self,
        filename: str,
        subfolder: str = "",
        folder_type: str = "output"
    ) -> bytes:
        """
        Retrieve a generated image from ComfyUI.

        Args:
            filename: Image filename
            subfolder: Subfolder path
            folder_type: "output", "input", or "temp"

        Returns:
            Image bytes
        """
        if not self.server_url:
            raise ValueError("ComfyUI server URL not configured")

        client = await self._get_http_client()

        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type
        }

        response = await client.get(f"{self.server_url}/view", params=params)
        response.raise_for_status()

        return response.content

    async def stream_progress(
        self,
        prompt_id: str
    ) -> AsyncGenerator[ComfyUIProgressUpdate, None]:
        """
        Stream progress updates via WebSocket.

        Args:
            prompt_id: The prompt ID to monitor

        Yields:
            ComfyUIProgressUpdate objects with progress info
        """
        if not self.server_url:
            raise ValueError("ComfyUI server URL not configured")

        # Convert HTTP URL to WebSocket URL
        ws_url = self.server_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws?clientId={self.client_id}"

        try:
            async with websockets.connect(ws_url) as ws:
                while True:
                    try:
                        message = await asyncio.wait_for(
                            ws.recv(),
                            timeout=COMFYUI_WS_TIMEOUT
                        )

                        data = json.loads(message)
                        msg_type = data.get("type")
                        msg_data = data.get("data", {})

                        # Check if this message is for our prompt
                        if msg_data.get("prompt_id") != prompt_id:
                            continue

                        if msg_type == "status":
                            queue_remaining = msg_data.get("status", {}).get("exec_info", {}).get("queue_remaining", 0)
                            yield ComfyUIProgressUpdate(
                                execution_id=prompt_id,
                                status=ExecutionStatus.QUEUED if queue_remaining > 0 else ExecutionStatus.RUNNING,
                                progress=0,
                                message=f"Queue position: {queue_remaining}"
                            )

                        elif msg_type == "execution_start":
                            yield ComfyUIProgressUpdate(
                                execution_id=prompt_id,
                                status=ExecutionStatus.RUNNING,
                                progress=0,
                                message="Execution started"
                            )

                        elif msg_type == "executing":
                            node = msg_data.get("node")
                            if node is None:
                                # Execution complete
                                yield ComfyUIProgressUpdate(
                                    execution_id=prompt_id,
                                    status=ExecutionStatus.COMPLETED,
                                    progress=100,
                                    message="Execution complete"
                                )
                                return
                            else:
                                yield ComfyUIProgressUpdate(
                                    execution_id=prompt_id,
                                    status=ExecutionStatus.RUNNING,
                                    progress=50,  # Approximate progress
                                    current_node=node,
                                    message=f"Executing node: {node}"
                                )

                        elif msg_type == "progress":
                            value = msg_data.get("value", 0)
                            max_value = msg_data.get("max", 100)
                            progress = (value / max_value) * 100 if max_value > 0 else 0

                            yield ComfyUIProgressUpdate(
                                execution_id=prompt_id,
                                status=ExecutionStatus.RUNNING,
                                progress=progress,
                                node_progress=progress,
                                message=f"Progress: {value}/{max_value}"
                            )

                        elif msg_type == "executed":
                            # Node execution complete, may have preview
                            output = msg_data.get("output", {})
                            images = output.get("images", [])
                            if images:
                                # Could encode preview image here
                                pass

                        elif msg_type == "execution_error":
                            error_msg = msg_data.get("exception_message", "Unknown error")
                            yield ComfyUIProgressUpdate(
                                execution_id=prompt_id,
                                status=ExecutionStatus.FAILED,
                                progress=0,
                                message=f"Error: {error_msg}"
                            )
                            return

                    except asyncio.TimeoutError:
                        logger.warning(f"WebSocket timeout for prompt {prompt_id}")
                        yield ComfyUIProgressUpdate(
                            execution_id=prompt_id,
                            status=ExecutionStatus.FAILED,
                            progress=0,
                            message="WebSocket timeout"
                        )
                        return

        except ConnectionClosed as e:
            logger.error(f"WebSocket connection closed: {e}")
            yield ComfyUIProgressUpdate(
                execution_id=prompt_id,
                status=ExecutionStatus.FAILED,
                progress=0,
                message=f"Connection closed: {e}"
            )
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            yield ComfyUIProgressUpdate(
                execution_id=prompt_id,
                status=ExecutionStatus.FAILED,
                progress=0,
                message=f"WebSocket error: {e}"
            )

    async def execute_workflow(
        self,
        workflow: dict[str, Any],
        request: ComfyUIExecutionRequest | None = None,
        workflow_type: WorkflowType = WorkflowType.TEXT_TO_IMAGE
    ) -> ComfyUIExecutionResponse:
        """
        Execute a workflow and wait for completion.

        This is a convenience method that queues the prompt, monitors progress,
        and retrieves results.

        Args:
            workflow: ComfyUI workflow JSON
            request: Optional request with parameters
            workflow_type: Type of workflow for metadata

        Returns:
            ComfyUIExecutionResponse with results
        """
        start_time = time.monotonic()
        created_at = datetime.now(timezone.utc)

        # Queue the prompt
        prompt_id = await self.queue_prompt(workflow, request)

        response = ComfyUIExecutionResponse(
            execution_id=prompt_id,
            status=ExecutionStatus.QUEUED,
            workflow_type=workflow_type,
            created_at=created_at
        )

        # Poll for completion
        max_wait = COMFYUI_GENERATION_TIMEOUT
        poll_start = time.monotonic()

        while (time.monotonic() - poll_start) < max_wait:
            history = await self.get_history(prompt_id)

            if history:
                # Check for outputs
                outputs = history.get("outputs", {})
                status_data = history.get("status", {})

                if status_data.get("status_str") == "error":
                    response.status = ExecutionStatus.FAILED
                    messages = status_data.get("messages", [])
                    response.error = messages[0].get("message", "Unknown error") if messages else "Unknown error"
                    response.completed_at = datetime.now(timezone.utc)
                    response.execution_time_ms = int((time.monotonic() - start_time) * 1000)
                    return response

                if outputs:
                    # Extract output images/videos
                    output_list = []

                    for node_id, node_output in outputs.items():
                        # Handle image outputs
                        images = node_output.get("images", [])
                        for img in images:
                            filename = img.get("filename")
                            subfolder = img.get("subfolder", "")

                            # Fetch the image and encode as base64
                            try:
                                image_bytes = await self.get_image(filename, subfolder)
                                b64_data = base64.b64encode(image_bytes).decode("utf-8")

                                output_list.append({
                                    "type": "image",
                                    "filename": filename,
                                    "b64_data": f"data:image/png;base64,{b64_data}",
                                    "url": f"{self.server_url}/view?filename={filename}&subfolder={subfolder}&type=output"
                                })
                            except Exception as e:
                                logger.error(f"Failed to fetch image {filename}: {e}")
                                output_list.append({
                                    "type": "image",
                                    "filename": filename,
                                    "url": f"{self.server_url}/view?filename={filename}&subfolder={subfolder}&type=output",
                                    "error": str(e)
                                })

                        # Handle video outputs (gifs, mp4s)
                        gifs = node_output.get("gifs", [])
                        for gif in gifs:
                            filename = gif.get("filename")
                            subfolder = gif.get("subfolder", "")

                            output_list.append({
                                "type": "video",
                                "filename": filename,
                                "url": f"{self.server_url}/view?filename={filename}&subfolder={subfolder}&type=output",
                                "content_type": "image/gif" if filename.endswith(".gif") else "video/mp4"
                            })

                    response.status = ExecutionStatus.COMPLETED
                    response.outputs = output_list
                    response.completed_at = datetime.now(timezone.utc)
                    response.execution_time_ms = int((time.monotonic() - start_time) * 1000)
                    return response

            # Still processing, wait and poll again
            await asyncio.sleep(COMFYUI_POLL_INTERVAL)
            response.status = ExecutionStatus.RUNNING

        # Timeout
        response.status = ExecutionStatus.FAILED
        response.error = "Execution timeout"
        response.completed_at = datetime.now(timezone.utc)
        response.execution_time_ms = int((time.monotonic() - start_time) * 1000)

        return response

    async def cancel_execution(self, prompt_id: str) -> bool:
        """
        Cancel a queued or running execution.

        Args:
            prompt_id: The prompt ID to cancel

        Returns:
            True if cancelled successfully
        """
        if not self.server_url:
            raise ValueError("ComfyUI server URL not configured")

        client = await self._get_http_client()

        # Delete from queue
        response = await client.post(
            f"{self.server_url}/queue",
            json={"delete": [prompt_id]}
        )

        # Also try to interrupt current execution
        await client.post(f"{self.server_url}/interrupt")

        return response.status_code == 200


# Global client instance
_comfyui_client: ComfyUIClient | None = None


def get_comfyui_client() -> ComfyUIClient:
    """Get the global ComfyUI client instance"""
    global _comfyui_client
    if _comfyui_client is None:
        _comfyui_client = ComfyUIClient()
    return _comfyui_client


async def close_comfyui_client():
    """Close the global ComfyUI client"""
    global _comfyui_client
    if _comfyui_client:
        await _comfyui_client.close()
        _comfyui_client = None
