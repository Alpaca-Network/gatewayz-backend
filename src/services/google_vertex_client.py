"""Google Vertex AI API client for chat completions

This module provides integration with Google Vertex AI generative models
using the Vertex AI SDK with Application Default Credentials (ADC).

Authentication uses Google Application Default Credentials (ADC):
- Automatically discovers credentials from environment (GOOGLE_APPLICATION_CREDENTIALS)
- No manual JWT exchange required
- Supports service account JSON files
- Works in serverless environments (Vercel, Railway)
- Recommended by Google for production use

The library will automatically find credentials from:
1. GOOGLE_APPLICATION_CREDENTIALS environment variable (path to JSON file)
2. GOOGLE_VERTEX_CREDENTIALS_JSON environment variable (raw JSON, written to temp file)
3. Application Default Credentials (gcloud auth, GCE metadata server, etc.)

Deployment Note: This implementation was updated to use ADC in PR #252.
"""

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx

from src.config import Config
from datetime import UTC
from src.utils.model_name_validator import clean_model_name

# Initialize logging
logger = logging.getLogger(__name__)

# Lazy imports for Google Vertex AI SDK to prevent import errors in environments
# where libstdc++.so.6 is not available. These will be imported only when needed.
_vertexai = None
_GenerativeModel = None
_MessageToDict = None

_VERTEX_API_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_TOKEN_CACHE = {"token": None, "expiry": 0.0}
_TOKEN_LOCK = threading.Lock()
_TEMP_CREDENTIALS_FILE: str | None = None
_TEMP_CREDENTIALS_LOCK = threading.Lock()
_DEFAULT_TRANSPORT = "rest"

# Vertex AI maxOutputTokens valid range
# See: https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini
VERTEX_MIN_OUTPUT_TOKENS = 16
VERTEX_MAX_OUTPUT_TOKENS = 65536


def _ensure_vertex_imports():
    """Ensure Vertex AI SDK is imported. Raises ImportError if SDK not available."""
    global _vertexai, _GenerativeModel
    if _vertexai is None:
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel

            _vertexai = vertexai
            _GenerativeModel = GenerativeModel
            logger.debug("Successfully imported Vertex AI SDK")
        except ImportError as e:
            raise ImportError(
                f"Google Vertex AI SDK is not available: {e}. "
                "This is typically due to missing system dependencies (libstdc++.so.6). "
                "Ensure the environment has the required C++ runtime libraries."
            ) from e
    return _vertexai, _GenerativeModel


def _get_model_location(model_name: str, try_regional_fallback: bool = False) -> str:
    """
    Determine the appropriate GCP location for a given model.

    Preview models (e.g., Gemini 3, models with 'preview' in name) are only available
    on global endpoints. Generally available models use regional endpoints by default
    for better performance (44-45% faster TTFC based on testing).

    Args:
        model_name: The model name to check
        try_regional_fallback: If True, attempts regional endpoint even for preview models
                               (useful for performance testing and fallback scenarios)

    Returns:
        The location string to use ('global' or the configured regional location)
    """
    model_lower = model_name.lower()

    # Preview models require global endpoint:
    # 1. Gemini 3 models (e.g., gemini-3-flash-preview, gemini-3-pro-preview)
    # 2. Any model with 'preview' in the name
    # Regional endpoints for these show ~20% failure rate (404 Model Not Found)
    is_preview_model = "gemini-3" in model_lower or "preview" in model_lower

    if is_preview_model:
        if try_regional_fallback:
            logger.info(
                f"Model {model_name} normally requires global endpoint, but trying regional "
                f"fallback ({Config.GOOGLE_VERTEX_LOCATION}) for performance optimization"
            )
            return Config.GOOGLE_VERTEX_LOCATION
        logger.debug(f"Model {model_name} requires global endpoint (preview model)")
        return "global"

    # Generally available models (e.g., gemini-2.5-flash-lite, gemini-2.0-flash-exp)
    # use regional endpoints by default for better performance
    logger.debug(f"Model {model_name} using regional endpoint ({Config.GOOGLE_VERTEX_LOCATION}) for optimal performance")
    return Config.GOOGLE_VERTEX_LOCATION


def _prepare_vertex_environment():
    """Validate config and prepare credential files for Vertex AI access."""
    if not Config.GOOGLE_PROJECT_ID:
        raise ValueError(
            "GOOGLE_PROJECT_ID is not configured. Set this environment variable to your GCP project ID. "
            "For example: GOOGLE_PROJECT_ID=my-project-123"
        )

    if not Config.GOOGLE_VERTEX_LOCATION:
        raise ValueError(
            "GOOGLE_VERTEX_LOCATION is not configured. Set this to a valid GCP region. "
            "For example: GOOGLE_VERTEX_LOCATION=us-central1"
        )

    # If raw credentials JSON is provided but GOOGLE_APPLICATION_CREDENTIALS is not set,
    # write the JSON to a temp file (once) and reuse it for the process lifetime.
    if os.environ.get("GOOGLE_VERTEX_CREDENTIALS_JSON") and not os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS"
    ):
        global _TEMP_CREDENTIALS_FILE
        with _TEMP_CREDENTIALS_LOCK:
            if _TEMP_CREDENTIALS_FILE and os.path.exists(_TEMP_CREDENTIALS_FILE):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _TEMP_CREDENTIALS_FILE
                return

            logger.info("GOOGLE_VERTEX_CREDENTIALS_JSON detected - writing to temp file for ADC")
            creds_json = os.environ.get("GOOGLE_VERTEX_CREDENTIALS_JSON")

            temp_creds_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            temp_creds_file.write(creds_json or "")
            temp_creds_file.close()

            _TEMP_CREDENTIALS_FILE = temp_creds_file.name
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_creds_file.name
            logger.info(f"Wrote credentials to temp file: {temp_creds_file.name}")


def _ensure_protobuf_imports():
    """Ensure protobuf utilities are imported. Raises ImportError if not available."""
    global _MessageToDict
    if _MessageToDict is None:
        try:
            from google.protobuf.json_format import MessageToDict

            _MessageToDict = MessageToDict
            logger.debug("Successfully imported MessageToDict from protobuf")
        except ImportError as e:
            raise ImportError(
                f"protobuf utilities are not available: {e}. "
                "This is typically due to missing system dependencies."
            ) from e
    return _MessageToDict


def _get_google_vertex_access_token(force_refresh: bool = False) -> str:
    """Fetch (and cache) an OAuth2 access token for Vertex REST API calls."""
    try:
        import google.auth
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as e:
        raise ImportError(
            "google-auth is required for Google Vertex REST transport. "
            "Install google-auth>=2.0 to enable Vertex AI access without the SDK."
        ) from e

    _prepare_vertex_environment()

    with _TOKEN_LOCK:
        if (
            not force_refresh
            and _TOKEN_CACHE["token"]
            and _TOKEN_CACHE["expiry"] - 60 > time.time()
        ):
            return _TOKEN_CACHE["token"]  # type: ignore[return-value]

    credentials = None

    # Prefer raw JSON credentials if provided
    if os.environ.get("GOOGLE_VERTEX_CREDENTIALS_JSON"):
        try:
            creds_info = json.loads(os.environ["GOOGLE_VERTEX_CREDENTIALS_JSON"])
        except json.JSONDecodeError as decode_error:
            raise ValueError(
                "GOOGLE_VERTEX_CREDENTIALS_JSON is not valid JSON. "
                "Ensure the environment variable contains the raw service account JSON."
            ) from decode_error
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=[_VERTEX_API_SCOPE]
        )

    # Fallback to GOOGLE_APPLICATION_CREDENTIALS file path
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        credentials = service_account.Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=[_VERTEX_API_SCOPE]
        )

    # Finally, use Application Default Credentials (gcloud, metadata server, etc.)
    else:
        credentials, _ = google.auth.default(scopes=[_VERTEX_API_SCOPE])

    request = Request()
    credentials.refresh(request)

    token = credentials.token
    expiry_ts = (
        credentials.expiry.timestamp()
        if getattr(credentials, "expiry", None)
        else time.time() + 3500
    )

    with _TOKEN_LOCK:
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["expiry"] = expiry_ts

    return token


def _sanitize_system_content(content: Any) -> str:
    """Normalize system message content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "\n".join(part for part in text_parts if part)
    return str(content)


def _translate_openai_tools_to_vertex(tools: list[dict]) -> list[dict]:
    """Translate OpenAI tools format to Google Vertex AI functionDeclarations format.

    OpenAI format:
    [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"}
                    },
                    "required": ["location"]
                }
            }
        }
    ]

    Vertex AI format:
    [
        {
            "functionDeclarations": [
                {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"}
                        },
                        "required": ["location"]
                    }
                }
            ]
        }
    ]

    Args:
        tools: List of OpenAI-format tool definitions

    Returns:
        List containing a single dict with functionDeclarations for Vertex AI
    """
    if not tools:
        return []

    function_declarations = []

    for tool in tools:
        # Only process function-type tools
        if tool.get("type") != "function":
            logger.warning(
                f"Skipping non-function tool type: {tool.get('type')}. "
                "Only 'function' type tools are supported for Vertex AI."
            )
            continue

        function_def = tool.get("function", {})
        if not function_def:
            logger.warning("Skipping tool with missing 'function' definition")
            continue

        name = function_def.get("name")
        if not name:
            logger.warning("Skipping function with missing 'name' field")
            continue

        # Build Vertex AI function declaration
        vertex_function = {
            "name": name,
        }

        # Add description if present
        if function_def.get("description"):
            vertex_function["description"] = function_def["description"]

        # Add parameters if present
        # Vertex AI uses OpenAPI 3.0.3 schema format which is compatible with OpenAI's JSON Schema
        if function_def.get("parameters"):
            vertex_function["parameters"] = function_def["parameters"]

        function_declarations.append(vertex_function)

    if not function_declarations:
        logger.warning("No valid function declarations found after translation")
        return []

    logger.info(
        f"Translated {len(function_declarations)} OpenAI tool(s) to Vertex AI functionDeclarations"
    )

    # Vertex AI expects tools as: [{"functionDeclarations": [...]}]
    return [{"functionDeclarations": function_declarations}]


def _translate_tool_choice_to_vertex(tool_choice: str | dict | None) -> dict | None:
    """Translate OpenAI tool_choice to Vertex AI toolConfig.

    OpenAI tool_choice values:
    - "none": Model will not call any tools
    - "auto": Model decides whether to call tools (default)
    - "required": Model must call at least one tool
    - {"type": "function", "function": {"name": "..."}}:  Model must call the specific function

    Vertex AI toolConfig format:
    {
        "functionCallingConfig": {
            "mode": "NONE" | "AUTO" | "ANY",
            "allowedFunctionNames": ["..."]  # Only for ANY mode
        }
    }

    Args:
        tool_choice: OpenAI tool_choice value

    Returns:
        Vertex AI toolConfig dict, or None if no translation needed
    """
    if tool_choice is None:
        return None

    # String values
    if isinstance(tool_choice, str):
        if tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        elif tool_choice == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif tool_choice == "required":
            return {"functionCallingConfig": {"mode": "ANY"}}
        else:
            logger.warning(f"Unknown tool_choice value: {tool_choice}. Using AUTO mode.")
            return {"functionCallingConfig": {"mode": "AUTO"}}

    # Object value: force specific function
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            function_obj = tool_choice.get("function")
            function_name = function_obj.get("name") if function_obj else None
            if function_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [function_name],
                    }
                }
            else:
                logger.warning("tool_choice function object missing 'name'. Using ANY mode.")
                return {"functionCallingConfig": {"mode": "ANY"}}

    logger.warning(f"Unrecognized tool_choice format: {tool_choice}. Using AUTO mode.")
    return {"functionCallingConfig": {"mode": "AUTO"}}


def _prepare_vertex_contents(messages: list) -> tuple[list, str | None]:
    """Split OpenAI messages into conversational content and system instruction."""
    system_messages = []
    conversational_messages = []

    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            system_messages.append(_sanitize_system_content(message.get("content", "")))
            continue
        conversational_messages.append(message)

    contents = _build_vertex_content(conversational_messages)
    system_instruction = "\n\n".join(filter(None, system_messages)) if system_messages else None
    return contents, system_instruction


def initialize_vertex_ai(location: str | None = None):
    """Initialize Vertex AI using Application Default Credentials (ADC)

    This function initializes Vertex AI with your project and location.
    It does NOT pass explicit credentials - the library will automatically
    discover them from the environment.

    The library finds credentials in this order:
    1. GOOGLE_APPLICATION_CREDENTIALS environment variable (path to JSON)
    2. Application Default Credentials (ADC) from gcloud, metadata server, etc.
    3. If GOOGLE_VERTEX_CREDENTIALS_JSON is set (raw JSON), we'll write it to a temp file
       and set GOOGLE_APPLICATION_CREDENTIALS to point to it

    Args:
        location: Optional GCP location override. If not provided, uses Config.GOOGLE_VERTEX_LOCATION

    Raises:
        ValueError: If project_id or location is not configured
    """
    try:
        # Use provided location or fall back to config
        effective_location = location or Config.GOOGLE_VERTEX_LOCATION

        logger.info(
            f"Initializing Vertex AI with Application Default Credentials (location: {effective_location})"
        )

        # Validate configuration & prepare environment
        _prepare_vertex_environment()

        # Ensure Vertex AI SDK is available
        vertexai, _ = _ensure_vertex_imports()

        # Initialize Vertex AI - DO NOT pass credentials parameter
        # The library will automatically find them from the environment
        vertexai.init(project=Config.GOOGLE_PROJECT_ID, location=effective_location)

        logger.info(
            f"✓ Successfully initialized Vertex AI for project: {Config.GOOGLE_PROJECT_ID} in {effective_location}"
        )

    except Exception as e:
        error_msg = f"Failed to initialize Vertex AI: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise ValueError(error_msg) from e


def transform_google_vertex_model_id(model_id: str) -> str:
    """Transform model ID to Google Vertex AI format

    For the REST API, we just need the model name (e.g., 'gemini-2.5-flash-lite').
    The full URL path is constructed in the API call functions.

    Args:
        model_id: Model identifier (e.g., 'gemini-2.0-flash', 'gemini-1.5-pro',
                  'google/gemini-2.0-flash')

    Returns:
        Simple model name (e.g., 'gemini-2.5-flash-lite')
    """
    # If already in full format, extract the model name
    if model_id.startswith("projects/"):
        # Extract model name from projects/.../models/{model}
        return model_id.split("/models/")[-1]

    # Strip provider prefix (e.g., 'google/gemini-2.0-flash' -> 'gemini-2.0-flash')
    if model_id.startswith("google/"):
        return model_id[7:]  # len("google/") == 7

    # Otherwise, return as-is
    return model_id


def _make_google_vertex_request_sdk(
    messages: list,
    model: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    **kwargs,
) -> dict:
    """Make request to Google Vertex AI using the Vertex AI SDK with ADC

    Converts OpenAI-compatible parameters to Google Vertex AI Gemini format
    and returns a normalized response.

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
        logger.info(f"Making Google Vertex request for model: {model}")

        # Step 1: Transform model ID first to determine location
        try:
            model_name = transform_google_vertex_model_id(model)
            logger.info(f"Using model name: {model_name}")
        except Exception as transform_error:
            logger.error(f"Failed to transform model ID: {transform_error}", exc_info=True)
            raise

        # Step 2: Determine the appropriate location for this model
        # Use regional fallback if configured (for A/B testing or latency optimization)
        location = _get_model_location(
            model_name, try_regional_fallback=Config.GOOGLE_VERTEX_REGIONAL_FALLBACK
        )
        logger.info(f"Using location '{location}' for model '{model_name}'")

        # Step 3: Initialize Vertex AI with the appropriate location (will use ADC)
        try:
            initialize_vertex_ai(location=location)
        except Exception as init_error:
            logger.error(f"Failed to initialize Vertex AI: {init_error}", exc_info=True)
            raise

        # Step 4: Create GenerativeModel instance
        try:
            _, GenerativeModel = _ensure_vertex_imports()
            gemini_model = GenerativeModel(model_name)
            logger.info(f"Created GenerativeModel for {model_name}")
        except Exception as model_error:
            logger.error(f"Failed to create GenerativeModel: {model_error}", exc_info=True)
            raise

        # Step 4: Build generation config
        try:
            generation_config = {}
            if max_tokens is not None:
                # Validate and clamp to Vertex AI's valid range to prevent 400 errors
                adjusted_max_tokens = max(
                    VERTEX_MIN_OUTPUT_TOKENS, min(max_tokens, VERTEX_MAX_OUTPUT_TOKENS)
                )
                if adjusted_max_tokens != max_tokens:
                    logger.warning(
                        f"max_tokens={max_tokens} is outside valid range ({VERTEX_MIN_OUTPUT_TOKENS}-{VERTEX_MAX_OUTPUT_TOKENS}). "
                        f"Adjusting to {adjusted_max_tokens} for Google Vertex AI compatibility."
                    )
                generation_config["max_output_tokens"] = adjusted_max_tokens
            if temperature is not None:
                generation_config["temperature"] = temperature
            if top_p is not None:
                generation_config["top_p"] = top_p
            logger.debug(f"Generation config: {generation_config}")
        except Exception as config_error:
            logger.error(f"Failed to build generation config: {config_error}", exc_info=True)
            raise

        # Step 5: Extract tools from kwargs (if provided)
        tools = kwargs.get("tools")
        if tools:
            logger.info(
                f"Tools parameter detected: {len(tools) if isinstance(tools, list) else 0} tools"
            )
            logger.warning(
                "Google Vertex AI function calling support requires transformation from OpenAI format to Gemini format. "
                "Currently, tools are extracted but not yet transformed. Function calling may not work correctly."
            )
            # TODO: Transform OpenAI tools format to Gemini function calling format
            # Gemini uses a different schema: tools need to be converted to FunctionDeclaration format
            # See: https://cloud.google.com/vertex-ai/docs/generative-ai/model-reference/gemini#function_calling

        # Step 6: Convert messages to Vertex AI format
        try:
            # Extract the last user message as the prompt
            # For simplicity, we'll combine all messages into a single prompt
            prompt_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    prompt_parts.append(f"System: {content}")
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")

            prompt = "\n\n".join(prompt_parts)
            logger.debug(f"Built prompt with {len(messages)} messages")
        except Exception as content_error:
            logger.error(f"Failed to build prompt: {content_error}", exc_info=True)
            raise

        # Step 7: Make the API call
        try:
            logger.info("Calling GenerativeModel.generate_content()")
            response = gemini_model.generate_content(
                prompt, generation_config=generation_config if generation_config else None
            )
            logger.info("Received response from Vertex AI")
            logger.debug(f"Response: {response}")
        except Exception as api_error:
            logger.error(f"Vertex AI API call failed: {api_error}", exc_info=True)
            raise

        # Step 8: Process and normalize response
        try:
            processed_response = _process_google_vertex_sdk_response(response, model)
            logger.info("Successfully processed Vertex AI response")
            return processed_response
        except Exception as process_error:
            logger.error(f"Failed to process Vertex AI response: {process_error}", exc_info=True)
            raise

    except Exception as e:
        logger.error(f"Google Vertex AI request failed: {e}", exc_info=True)
        raise


def _make_google_vertex_request_rest(
    messages: list,
    model: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    **kwargs,
) -> dict:
    """Make request to Google Vertex AI using the public REST API."""
    logger.info(f"Making Google Vertex REST request for model: {model}")

    try:
        _prepare_vertex_environment()

        model_name = transform_google_vertex_model_id(model)
        logger.info(f"Using REST model name: {model_name}")

        contents, system_instruction = _prepare_vertex_contents(messages)
        request_body: dict[str, Any] = {"contents": contents}

        if system_instruction:
            request_body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        # Translate OpenAI tools to Vertex AI functionDeclarations
        tools = kwargs.get("tools")
        if tools:
            logger.info(
                f"Tools parameter detected for Vertex REST call (count={len(tools) if isinstance(tools, list) else 0}). "
                "Translating to Vertex AI functionDeclarations format."
            )
            vertex_tools = _translate_openai_tools_to_vertex(tools)
            if vertex_tools:
                request_body["tools"] = vertex_tools
                logger.debug(f"Added tools to request: {vertex_tools}")

        # Translate tool_choice to toolConfig if provided
        # This is handled separately from tools because tool_choice="none" is valid
        # even when no tools are provided (explicitly disabling tool calling)
        tool_choice = kwargs.get("tool_choice")
        if tool_choice:
            tool_config = _translate_tool_choice_to_vertex(tool_choice)
            if tool_config:
                request_body["toolConfig"] = tool_config
                logger.debug(f"Added toolConfig to request: {tool_config}")

        generation_config: dict[str, Any] = {}
        if max_tokens is not None:
            # Validate and clamp to Vertex AI's valid range to prevent 400 errors
            adjusted_max_tokens = max(
                VERTEX_MIN_OUTPUT_TOKENS, min(max_tokens, VERTEX_MAX_OUTPUT_TOKENS)
            )
            if adjusted_max_tokens != max_tokens:
                logger.warning(
                    f"max_tokens={max_tokens} is outside valid range ({VERTEX_MIN_OUTPUT_TOKENS}-{VERTEX_MAX_OUTPUT_TOKENS}). "
                    f"Adjusting to {adjusted_max_tokens} for Google Vertex AI compatibility."
                )
            generation_config["maxOutputTokens"] = adjusted_max_tokens
        if temperature is not None:
            generation_config["temperature"] = temperature
        if top_p is not None:
            generation_config["topP"] = top_p
        if generation_config:
            request_body["generationConfig"] = generation_config

        if kwargs.get("safety_settings"):
            request_body["safetySettings"] = kwargs["safety_settings"]

        # Determine the appropriate location for this model
        # Use regional fallback if configured (for A/B testing or latency optimization)
        location = _get_model_location(
            model_name, try_regional_fallback=Config.GOOGLE_VERTEX_REGIONAL_FALLBACK
        )
        logger.info(f"Using location '{location}' for model '{model_name}'")

        # Build the API URL based on location
        # For global endpoints, the URL is https://aiplatform.googleapis.com/v1/...
        # For regional endpoints, the URL is https://{region}-aiplatform.googleapis.com/v1/...
        if location == "global":
            base_url = "https://aiplatform.googleapis.com/v1"
        else:
            base_url = f"https://{location}-aiplatform.googleapis.com/v1"

        url = (
            f"{base_url}/"
            f"projects/{Config.GOOGLE_PROJECT_ID}/"
            f"locations/{location}/"
            f"publishers/google/models/{model_name}:generateContent"
        )

        timeout_seconds = kwargs.get("vertex_timeout") or Config.GOOGLE_VERTEX_TIMEOUT

        def _execute_request(force_refresh_token: bool = False) -> httpx.Response:
            access_token = _get_google_vertex_access_token(force_refresh=force_refresh_token)
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            client_timeout = httpx.Timeout(timeout_seconds)
            with httpx.Client(timeout=client_timeout) as client:
                return client.post(url, headers=headers, json=request_body)

        response = _execute_request()
        if response.status_code == 401:
            logger.warning("Vertex REST call returned 401. Refreshing token and retrying once.")
            response = _execute_request(force_refresh_token=True)

        if response.status_code >= 400:
            logger.error(
                "Vertex REST call failed. status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
            raise ValueError(
                f"Vertex REST API returned HTTP {response.status_code}: {response.text[:2000]}"
            )

        try:
            response_data = response.json()
        except ValueError as parse_error:
            raise ValueError(
                f"Failed to parse Vertex REST response as JSON: {response.text[:2000]}"
            ) from parse_error

        return _process_google_vertex_rest_response(response_data, model)

    except httpx.RequestError as request_error:
        logger.error("HTTP request to Vertex REST API failed: %s", request_error, exc_info=True)
        raise ValueError(f"Vertex REST API request failed: {request_error}") from request_error
    except Exception as e:
        logger.error("Google Vertex REST request failed: %s", e, exc_info=True)
        raise


def make_google_vertex_request_openai(
    messages: list,
    model: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    **kwargs,
) -> dict:
    """Public entry point that routes to SDK or REST transport."""
    transport = (Config.GOOGLE_VERTEX_TRANSPORT or _DEFAULT_TRANSPORT).lower()
    allowed_transports = {"rest", "sdk", "auto"}
    if transport not in allowed_transports:
        logger.warning(
            "Unknown GOOGLE_VERTEX_TRANSPORT value '%s'. Falling back to '%s'.",
            transport,
            _DEFAULT_TRANSPORT,
        )
        transport = _DEFAULT_TRANSPORT

    logger.info(f"Google Vertex transport preference: {transport}")

    def _attempt_sdk_request() -> dict | None:
        try:
            return _make_google_vertex_request_sdk(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                **kwargs,
            )
        except ImportError as sdk_error:
            logger.warning(
                "Vertex SDK unavailable (%s). Falling back to REST transport.", sdk_error
            )
        except ValueError as sdk_value_error:
            if "libstdc++.so.6" not in str(sdk_value_error):
                raise
            logger.warning(
                "Detected libstdc++.so.6 import error when using Vertex SDK. "
                "Automatically switching to REST transport."
            )
        except Exception as sdk_exception:
            if "libstdc++.so.6" in str(sdk_exception):
                logger.warning(
                    "Vertex SDK failed due to missing libstdc++.so.6. Using REST transport."
                )
            else:
                raise
        return None

    if transport in {"sdk", "auto"}:
        sdk_response = _attempt_sdk_request()
        if sdk_response is not None:
            return sdk_response
        logger.info("Vertex SDK request unavailable; falling back to REST transport.")

    # REST is default or fallback
    return _make_google_vertex_request_rest(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        **kwargs,
    )


def _process_google_vertex_sdk_response(response: Any, model: str) -> dict:
    """Process Google Vertex AI SDK response to OpenAI-compatible format

    Args:
        response: Response from GenerativeModel.generate_content()
        model: Model name used

    Returns:
        OpenAI-compatible response dictionary
    """
    try:
        logger.debug(f"Processing SDK response: {response}")

        # Extract text from the response
        text_content = response.text if hasattr(response, "text") else ""
        logger.info(f"Extracted text content length: {len(text_content)} characters")

        # Extract usage metadata if available
        prompt_tokens = 0
        completion_tokens = 0
        if hasattr(response, "usage_metadata"):
            usage = response.usage_metadata
            prompt_tokens = getattr(usage, "prompt_token_count", 0)
            completion_tokens = getattr(usage, "candidates_token_count", 0)

        # Extract finish reason
        finish_reason = "stop"  # Default
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "finish_reason"):
                finish_reason_value = candidate.finish_reason
                # Map Vertex AI finish reasons to OpenAI format
                finish_reason_map = {
                    1: "stop",  # STOP
                    2: "length",  # MAX_TOKENS
                    3: "content_filter",  # SAFETY
                    4: "stop",  # RECITATION
                    0: "unknown",  # FINISH_REASON_UNSPECIFIED
                }
                finish_reason = finish_reason_map.get(finish_reason_value, "stop")

        return {
            "id": f"vertex-{int(time.time() * 1000)}",
            "object": "text_completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text_content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    except Exception as e:
        logger.error(f"Failed to process Google Vertex AI SDK response: {e}", exc_info=True)
        raise


def make_google_vertex_request_openai_stream(
    messages: list,
    model: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    **kwargs,
) -> Iterator[dict]:
    """Make streaming request to Google Vertex AI

    NOTE: For compatibility, this implementation gets the full response
    and returns it in OpenAI-compatible streaming format as dict objects.

    Args:
        messages: List of message objects in OpenAI format
        model: Model name to use
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        top_p: Nucleus sampling parameter
        **kwargs: Additional parameters

    Yields:
        OpenAI-compatible streaming chunk dicts (NOT SSE strings)
    """
    try:
        logger.info(f"Starting streaming request for model {model}")
        # Get non-streaming response
        response = make_google_vertex_request_openai(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            **kwargs,
        )

        logger.info(f"Received response: {json.dumps(response, indent=2, default=str)}")

        # Extract content safely
        choices = response.get("choices", [])
        if not choices:
            logger.error(f"No choices in response: {response}")
            raise ValueError("No choices in response")

        content = choices[0].get("message", {}).get("content", "")
        finish_reason = choices[0].get("finish_reason", "stop")

        logger.info(f"Content length: {len(content)}, finish_reason: {finish_reason}")

        # Convert to streaming format by yielding complete response as single chunk
        # Yield dict objects (NOT SSE strings) for StreamNormalizer compatibility
        chunk = {
            "id": response.get("id"),
            "object": "chat.completion.chunk",
            "created": response.get("created"),
            "model": response.get("model"),
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ],
        }

        logger.debug(f"Yielding chunk: {json.dumps(chunk, indent=2, default=str)}")
        yield chunk

        # Final chunk with finish_reason
        finish_chunk = {
            "id": response.get("id"),
            "object": "chat.completion.chunk",
            "created": response.get("created"),
            "model": response.get("model"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }

        logger.debug(f"Yielding finish chunk: {json.dumps(finish_chunk, indent=2, default=str)}")
        yield finish_chunk

    except Exception as e:
        logger.error(f"Google Vertex AI streaming request failed: {e}", exc_info=True)
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
                        # Base64 encoded image - extract MIME type and raw base64 data
                        # Format: data:image/jpeg;base64,<base64_data>
                        # Vertex AI expects only the raw base64 data, not the data URI prefix
                        try:
                            # Parse the data URL to extract MIME type and base64 data
                            # Expected format: data:<mime_type>;base64,<data>
                            header, base64_data = image_url.split(",", 1)
                            # Extract MIME type from header (e.g., "data:image/png;base64")
                            mime_type = "image/jpeg"  # default
                            if header.startswith("data:") and ";" in header:
                                mime_part = header[5:].split(";")[0]  # Remove "data:" prefix
                                if mime_part:
                                    mime_type = mime_part
                            parts.append(
                                {"inline_data": {"mime_type": mime_type, "data": base64_data}}
                            )
                        except ValueError:
                            # If parsing fails, log warning and skip this part
                            logger.warning(f"Failed to parse base64 data URL: {image_url[:50]}...")
                    else:
                        # URL reference
                        parts.append(
                            {"file_data": {"mime_type": "image/jpeg", "file_uri": image_url}}
                        )
        else:
            parts = [{"text": str(content)}]

        contents.append({"role": vertex_role, "parts": parts})

    return contents


def _normalize_vertex_candidate_to_openai(candidate: dict, model: str) -> dict:
    """Convert a Vertex AI candidate to OpenAI-compatible format

    This shared helper function normalizes response data from both protobuf
    and REST API formats to avoid code duplication.

    Args:
        candidate: Candidate object from Vertex AI response
        model: Model name used

    Returns:
        OpenAI-compatible response dictionary
    """
    logger.debug(f"Normalizing candidate: {json.dumps(candidate, indent=2, default=str)}")

    # Extract content from candidate
    content_parts = candidate.get("content", {}).get("parts", [])
    logger.debug(f"Content parts count: {len(content_parts)}")

    # Extract text from parts
    text_content = ""
    tool_calls = []
    for part in content_parts:
        if "text" in part:
            text_content += part["text"]
        # Check for tool use in parts (function calling)
        if "functionCall" in part:
            tool_call = part["functionCall"]
            tool_calls.append(
                {
                    "id": f"call_{int(time.time() * 1000)}",
                    "type": "function",
                    "function": {
                        "name": tool_call.get("name", "unknown"),
                        "arguments": json.dumps(tool_call.get("args", {})),
                    },
                }
            )

    logger.info(f"Extracted text content length: {len(text_content)} characters")

    # Warn if content is empty
    if not text_content and not tool_calls:
        logger.warning(
            f"Received empty text content from Vertex AI for model {model}. Candidate: {json.dumps(candidate, default=str)}"
        )

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
        "FINISH_REASON_UNSPECIFIED": "unknown",
    }

    # Build message with content and tool_calls if present
    message = {"role": "assistant", "content": text_content}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": f"vertex-{int(time.time() * 1000)}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason_map.get(finish_reason, "stop"),
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


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
        MessageToDict = _ensure_protobuf_imports()
        response_dict = MessageToDict(response)
        logger.debug(
            f"Google Vertex response dict: {json.dumps(response_dict, indent=2, default=str)}"
        )

        # Extract predictions
        predictions = response_dict.get("predictions", [])
        logger.debug(f"Predictions count: {len(predictions)}")

        if not predictions:
            logger.error(f"No predictions in Vertex AI response. Full response: {response_dict}")
            raise ValueError("No predictions in Vertex AI response")

        # Get the first prediction
        prediction = predictions[0]
        logger.debug(f"First prediction: {json.dumps(prediction, indent=2, default=str)}")

        # Extract content from candidates
        candidates = prediction.get("candidates", [])
        logger.debug(f"Candidates count: {len(candidates)}")

        if not candidates:
            logger.error(f"No candidates in Vertex AI prediction. Prediction: {prediction}")
            raise ValueError("No candidates in Vertex AI prediction")

        candidate = candidates[0]

        # Use shared normalization function
        return _normalize_vertex_candidate_to_openai(candidate, model)

    except Exception as e:
        logger.error(f"Failed to process Google Vertex AI response: {e}", exc_info=True)
        raise


def process_google_vertex_response(response: Any) -> dict:
    """Process Google Vertex AI response - handles both old and new formats

    Args:
        response: Either a protobuf response (old) or already-processed dict (new)

    Returns:
        OpenAI-compatible response dict
    """
    # If response is already a dict with the expected structure, return as-is
    if isinstance(response, dict) and "choices" in response and "usage" in response:
        logger.debug("Response is already in OpenAI format, returning as-is")
        return response

    # Otherwise, try to process as old protobuf format (for backward compatibility)
    logger.debug("Processing response as protobuf format")
    return _process_google_vertex_response(response, "google-vertex")


def _process_google_vertex_rest_response(response_data: dict, model: str) -> dict:
    """Process Google Vertex AI REST API response to OpenAI-compatible format

    Args:
        response_data: Response dictionary from the REST API
        model: Model name used

    Returns:
        OpenAI-compatible response dictionary
    """
    try:
        logger.debug(
            f"Google Vertex REST response: {json.dumps(response_data, indent=2, default=str)}"
        )

        # Extract candidates from REST API response
        candidates = response_data.get("candidates", [])
        logger.debug(f"Candidates count: {len(candidates)}")

        if not candidates:
            # Check for promptFeedback which indicates why the prompt was blocked
            prompt_feedback = response_data.get("promptFeedback", {})
            block_reason = prompt_feedback.get("blockReason", "")
            safety_ratings = prompt_feedback.get("safetyRatings", [])

            # Build a descriptive error message
            error_details = []
            if block_reason:
                error_details.append(f"Block reason: {block_reason}")
            if safety_ratings:
                blocked_categories = [
                    f"{r.get('category', 'UNKNOWN')}: {r.get('probability', 'UNKNOWN')}"
                    for r in safety_ratings
                    if r.get("blocked", False) or r.get("probability") in ["HIGH", "MEDIUM"]
                ]
                if blocked_categories:
                    error_details.append(f"Safety issues: {', '.join(blocked_categories)}")

            # If no promptFeedback, check for other response indicators
            # Some Gemini 3 preview models may return empty candidates without promptFeedback
            # This can happen due to model overload or transient issues
            usage_metadata = response_data.get("usageMetadata", {})
            model_version = response_data.get("modelVersion", "")

            if not error_details:
                # No explicit block reason - likely a transient/model issue
                error_details.append(
                    f"Model '{model_version or model}' returned no candidates without explicit block reason. "
                    "This may be a transient issue with preview models or rate limiting."
                )
                if usage_metadata.get("promptTokenCount"):
                    error_details.append(f"Prompt tokens: {usage_metadata.get('promptTokenCount')}")

            error_msg = "Vertex AI returned no candidates. " + " | ".join(error_details)
            logger.error(f"{error_msg}. Full response: {response_data}")
            raise ValueError(error_msg)

        # Get the first candidate
        candidate = candidates[0]

        # Merge top-level usage metadata into candidate for consistency with shared function
        if "usageMetadata" in response_data and "usageMetadata" not in candidate:
            candidate["usageMetadata"] = response_data["usageMetadata"]

        # Use shared normalization function
        return _normalize_vertex_candidate_to_openai(candidate, model)

    except Exception as e:
        logger.error(f"Failed to process Google Vertex AI REST response: {e}", exc_info=True)
        raise


def diagnose_google_vertex_credentials() -> dict:
    """Diagnose Google Vertex AI credentials and return detailed status

    Returns a dictionary with:
    - credentials_available: bool - Whether credentials were found and loaded
    - credential_source: str - Where credentials came from (env_json, file, adc, none)
    - project_id: str or None - Configured GCP project ID
    - location: str or None - Configured GCP region
    - initialization_successful: bool - Whether Vertex AI initialized successfully
    - error: str or None - Error message if any step failed
    - steps: list - Detailed step-by-step diagnostics

    This function is safe to call even if credentials are not configured - it returns
    diagnostic information without raising exceptions.
    """
    result = {
        "credentials_available": False,
        "credential_source": "none",
        "project_id": Config.GOOGLE_PROJECT_ID,
        "location": Config.GOOGLE_VERTEX_LOCATION,
        "initialization_successful": False,
        "error": None,
        "steps": [],
    }

    # Step 1: Check configuration
    step1 = {"step": "Configuration check", "passed": False, "details": ""}
    if not Config.GOOGLE_PROJECT_ID:
        step1["details"] = "GOOGLE_PROJECT_ID not set"
        result["steps"].append(step1)
    else:
        step1["passed"] = True
        step1["details"] = f"Project ID: {Config.GOOGLE_PROJECT_ID}"
        result["steps"].append(step1)

    if not Config.GOOGLE_VERTEX_LOCATION:
        step1["details"] += " | GOOGLE_VERTEX_LOCATION not set"
    else:
        step1["details"] += f" | Location: {Config.GOOGLE_VERTEX_LOCATION}"

    # Step 2: Check for credentials
    step2 = {"step": "Credential check", "passed": False, "source": "none", "details": ""}
    try:
        # Check GOOGLE_APPLICATION_CREDENTIALS (file path)
        if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            result["credentials_available"] = True
            step2["passed"] = True
            step2["source"] = "GOOGLE_APPLICATION_CREDENTIALS (file)"
            result["credential_source"] = "file"
            creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            step2["details"] = f"Credentials file path: {creds_path}"

        # Check GOOGLE_VERTEX_CREDENTIALS_JSON (raw JSON)
        elif os.environ.get("GOOGLE_VERTEX_CREDENTIALS_JSON"):
            result["credentials_available"] = True
            step2["passed"] = True
            step2["source"] = "GOOGLE_VERTEX_CREDENTIALS_JSON (env)"
            result["credential_source"] = "env_json"

            # Parse to get service account email for logging
            try:
                creds_json = os.environ.get("GOOGLE_VERTEX_CREDENTIALS_JSON")
                creds_dict = json.loads(creds_json)
                service_email = creds_dict.get("client_email", "unknown")
                step2["details"] = f"Raw JSON credentials (service account: {service_email})"
            except Exception:
                step2["details"] = "Raw JSON credentials detected"
        else:
            step2["details"] = (
                "No credentials found in GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_VERTEX_CREDENTIALS_JSON. Will try ADC."
            )
            result["credential_source"] = "adc"
            result["credentials_available"] = True  # ADC might still work
            step2["passed"] = True

    except Exception as e:
        step2["details"] = f"Failed to check credentials: {str(e)[:200]}"
        result["error"] = str(e)

    result["steps"].append(step2)

    # Step 3: Try to initialize Vertex AI / fetch access token
    step3 = {"step": "Vertex AI initialization", "passed": False, "details": ""}
    transport = (Config.GOOGLE_VERTEX_TRANSPORT or _DEFAULT_TRANSPORT).lower()
    try:
        _get_google_vertex_access_token(force_refresh=True)
        result["initialization_successful"] = True
        step3["passed"] = True
        step3["details"] = "Successfully obtained Vertex access token via REST transport"

        if transport in {"sdk", "auto"}:
            try:
                initialize_vertex_ai()
                step3["details"] += " | SDK initialization successful"
            except Exception as sdk_error:
                sdk_msg = str(sdk_error)[:200]
                step3["details"] += f" | SDK initialization failed: {sdk_msg}"
                logger.warning("Vertex SDK diagnostics failed: %s", sdk_msg)

    except Exception as e:
        step3["details"] = f"Failed to initialize: {str(e)[:200]}"
        result["error"] = str(e)

    result["steps"].append(step3)

    # Step 4: Summary
    is_healthy = (
        result["initialization_successful"]
        and Config.GOOGLE_PROJECT_ID
        and Config.GOOGLE_VERTEX_LOCATION
    )

    result["health_status"] = "healthy" if is_healthy else "unhealthy"

    if not is_healthy:
        issues = []
        if not Config.GOOGLE_PROJECT_ID:
            issues.append("GOOGLE_PROJECT_ID not configured")
        if not Config.GOOGLE_VERTEX_LOCATION:
            issues.append("GOOGLE_VERTEX_LOCATION not configured")
        if not result["initialization_successful"]:
            issues.append("Vertex AI initialization failed")

        result["error"] = "Configuration issues: " + "; ".join(issues)

    return result


def _fetch_models_from_vertex_api() -> list[dict] | None:
    """Fetch available models dynamically from Google Vertex AI API.

    Uses the publishers/google/models endpoint to list all available Gemini models.
    Returns None if the API call fails, allowing fallback to static config.

    Note: This function attempts both regional and global endpoints due to inconsistent
    availability across different regions and API versions.
    """
    try:
        _prepare_vertex_environment()
        access_token = _get_google_vertex_access_token()

        # Use the Vertex AI discovery endpoint to list publisher models
        # This endpoint lists all Google-published models available in Vertex AI
        location = Config.GOOGLE_VERTEX_LOCATION
        project_id = Config.GOOGLE_PROJECT_ID

        # Try multiple endpoint patterns as the API availability varies by region
        # Pattern 1: Regional endpoint with project context (most accurate for access control)
        # Pattern 2: Regional endpoint without project (simpler, but may not respect all access)
        # Pattern 3: Global endpoint (fallback, but doesn't always have all models)
        endpoint_patterns = [
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/publishers/google/models",
            f"https://{location}-aiplatform.googleapis.com/v1/publishers/google/models",
            "https://aiplatform.googleapis.com/v1/publishers/google/models",
        ]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        last_error = None
        for url in endpoint_patterns:
            try:
                logger.debug(f"Attempting to fetch models from Vertex AI API: {url}")

                with httpx.Client(timeout=30.0) as client:
                    response = client.get(url, headers=headers)

                    if response.status_code == 200:
                        data = response.json()
                        models = data.get("publisherModels", [])
                        logger.info(
                            f"Successfully fetched {len(models)} models from Vertex AI API using endpoint: {url}"
                        )
                        return models
                    else:
                        last_error = f"Status {response.status_code}: {response.text[:500]}"
                        logger.debug(f"Endpoint {url} returned {response.status_code}")
                        continue

            except Exception as e:
                last_error = str(e)
                logger.debug(f"Endpoint {url} failed: {e}")
                continue

        # All endpoints failed
        logger.warning(
            f"Failed to fetch models from all Vertex AI API endpoints. Last error: {last_error}. "
            "Falling back to static model configuration (12 models)."
        )
        return None

    except Exception as e:
        logger.warning(f"Failed to fetch models from Vertex AI API: {e}. Using fallback configuration.")
        return None


def _normalize_vertex_api_model(api_model: dict) -> dict | None:
    """Convert a Vertex AI API model response to our normalized format.

    Args:
        api_model: Raw model data from the Vertex AI publishers/google/models API

    Returns:
        Normalized model dict or None if the model should be skipped
    """
    try:
        # Extract model name from the full resource name
        # Format: publishers/google/models/gemini-2.0-flash
        name = api_model.get("name", "")
        model_id = name.split("/")[-1] if "/" in name else name

        if not model_id:
            return None

        # Skip non-generative models (embeddings handled separately, imagen, etc.)
        # We want chat/text generation models
        supported_actions = api_model.get("supportedActions", {})
        if not supported_actions.get("generateContent") and not supported_actions.get(
            "streamGenerateContent"
        ):
            # Check if it's an embedding model we want to include
            if not supported_actions.get("computeTokens") and "embedding" not in model_id.lower():
                return None

        # Get version info
        version_info = api_model.get("versionId", "")
        raw_display_name = api_model.get("openSourceCategory", "") or model_id
        # Clean malformed model names (remove company prefix, parentheses, etc.)
        display_name = clean_model_name(raw_display_name)

        # Extract from publisherModelTemplate if available
        template = api_model.get("publisherModelTemplate", {})

        # Get context length from inputTokenLimit or default
        input_token_limit = None
        output_token_limit = None

        # Try to get limits from various possible locations in the API response
        if "inputTokenLimit" in api_model:
            input_token_limit = api_model.get("inputTokenLimit")
        if "outputTokenLimit" in api_model:
            output_token_limit = api_model.get("outputTokenLimit")

        # Default context lengths based on model family
        if input_token_limit is None:
            if "gemini-3" in model_id or "gemini-2.5" in model_id or "gemini-2.0" in model_id:
                input_token_limit = 1000000  # 1M context for newer models
            elif "gemini-1.5" in model_id:
                input_token_limit = 1000000
            elif "gemma" in model_id:
                input_token_limit = 8192
            else:
                input_token_limit = 32768  # Safe default

        # Determine modalities
        input_modalities = ["text"]
        output_modalities = ["text"]

        # Gemini models support multimodal input
        if "gemini" in model_id.lower():
            input_modalities = ["text", "image", "audio", "video"]

        # Check for image generation models
        if "imagen" in model_id.lower() or "image" in model_id.lower():
            output_modalities = ["image"]

        # Determine features based on model capabilities
        features = ["streaming"]
        if "gemini" in model_id.lower():
            features.extend(["multimodal", "function_calling"])
            if "pro" in model_id.lower() or "flash" in model_id.lower():
                features.append("thinking")

        if "embedding" in model_id.lower():
            features = ["embeddings"]
            input_modalities = ["text"]
            output_modalities = ["embedding"]

        # Build description
        description = api_model.get("description", "") or f"Google {model_id} model"

        # Create display name
        name_display = api_model.get("displayName", "") or model_id.replace("-", " ").title()

        prefixed_slug = f"google-vertex/{model_id}"

        return {
            "id": model_id,
            "slug": prefixed_slug,
            "canonical_slug": prefixed_slug,
            "hugging_face_id": None,
            "name": name_display,
            "created": None,
            "description": description,
            "context_length": input_token_limit,
            "architecture": {
                "modality": "text->text",
                "input_modalities": input_modalities,
                "output_modalities": output_modalities,
                "tokenizer": None,
                "instruct_type": "chat",
            },
            "pricing": {
                "prompt": None,  # Dynamic pricing not available from API
                "completion": None,
                "request": None,
                "image": None,
                "web_search": None,
                "internal_reasoning": None,
            },
            "per_request_limits": None,
            "supported_parameters": [
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "stream",
            ],
            "default_parameters": {},
            "provider_slug": "google",
            "provider_site_url": "https://cloud.google.com/vertex-ai",
            "model_logo_url": None,
            "source_gateway": "google-vertex",
            "tags": features,
            "raw_google_vertex": {
                "id": model_id,
                "name": name_display,
                "version": version_info,
                "api_response": api_model,
            },
        }

    except Exception as e:
        logger.warning(f"Failed to normalize Vertex AI model: {e}")
        return None


def _get_static_model_config() -> list[dict]:
    """Get static model configurations with pricing info.

    Returns models from google_models_config.py which contains
    accurate pricing and feature information.
    """
    from src.services.google_models_config import get_google_models

    multi_provider_models = get_google_models()
    normalized_models = []

    for model in multi_provider_models:
        vertex_provider = next((p for p in model.providers if p.name == "google-vertex"), None)

        pricing = {}
        features = []
        if vertex_provider:
            pricing = {
                "prompt": str(vertex_provider.cost_per_1k_input),
                "completion": str(vertex_provider.cost_per_1k_output),
                "request": None,
                "image": None,
                "web_search": None,
                "internal_reasoning": None,
            }
            features = vertex_provider.features

        input_modalities = model.modalities if model.modalities else ["text"]
        output_modalities = ["text"]

        prefixed_slug = f"google-vertex/{model.id}"
        # Use the provider-specific model_id if available (e.g., gemini-3-flash-preview)
        # This is the actual model ID used when making API requests to the provider
        provider_model_id = vertex_provider.model_id if vertex_provider else model.id
        normalized = {
            "id": model.id,
            "slug": prefixed_slug,
            "canonical_slug": prefixed_slug,
            "hugging_face_id": None,
            "name": model.name,
            "created": None,
            "description": model.description,
            "context_length": model.context_length,
            "architecture": {
                "modality": "text->text",
                "input_modalities": input_modalities,
                "output_modalities": output_modalities,
                "tokenizer": None,
                "instruct_type": "chat",
            },
            "pricing": pricing,
            "per_request_limits": None,
            "supported_parameters": [
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "stream",
            ],
            "default_parameters": {},
            "provider_slug": "google",
            "provider_site_url": "https://cloud.google.com/vertex-ai",
            "model_logo_url": None,
            "source_gateway": "google-vertex",
            "tags": features,
            # Include provider_model_id for database sync - this is the actual model ID
            # used by the provider (e.g., "gemini-3-flash-preview" for Vertex AI)
            "provider_model_id": provider_model_id,
            "raw_google_vertex": {
                "id": model.id,
                "name": model.name,
                "provider_model_id": provider_model_id,
                "modalities": model.modalities,
                "context_length": model.context_length,
            },
        }
        normalized_models.append(normalized)

    return normalized_models


def fetch_models_from_google_vertex():
    """Fetch models from Google Vertex AI API.

    Attempts to fetch models dynamically from the Vertex AI API.
    Falls back to static configuration if the API call fails.
    Merges dynamic models with static config to get accurate pricing.
    """
    from datetime import datetime

    from src.cache import _google_vertex_models_cache

    logger.info("Fetching Google Vertex AI model catalog")

    try:
        # Get static config for pricing and known models
        static_models = _get_static_model_config()
        static_by_id = {m["id"]: m for m in static_models}

        # Try to fetch dynamic models from API
        api_models = _fetch_models_from_vertex_api()

        if api_models is not None:
            # Merge API models with static config
            normalized_models = []
            seen_ids = set()

            for api_model in api_models:
                normalized = _normalize_vertex_api_model(api_model)
                if normalized is None:
                    continue

                model_id = normalized["id"]

                # If we have static config for this model, use its pricing
                if model_id in static_by_id:
                    static = static_by_id[model_id]
                    normalized["pricing"] = static["pricing"]
                    normalized["tags"] = static["tags"]
                    normalized["name"] = static["name"]
                    normalized["description"] = static["description"]

                normalized_models.append(normalized)
                seen_ids.add(model_id)

            # Add any static models not returned by API (e.g., embeddings, special models)
            for model_id, static_model in static_by_id.items():
                if model_id not in seen_ids:
                    normalized_models.append(static_model)
                    logger.debug(f"Added static model not in API: {model_id}")

            logger.info(
                f"Loaded {len(normalized_models)} Google Vertex AI models "
                f"({len(api_models)} from API, merged with static config)"
            )
        else:
            # Fallback to static config only
            normalized_models = static_models
            logger.info(
                f"Loaded {len(normalized_models)} Google Vertex AI models from static config (API unavailable)"
            )

        # Update cache
        _google_vertex_models_cache["data"] = normalized_models
        _google_vertex_models_cache["timestamp"] = datetime.now(UTC)

        return normalized_models

    except Exception as e:
        logger.error(f"Failed to load Google Vertex AI models: {e}")
        return []
