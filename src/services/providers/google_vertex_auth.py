"""Google Vertex AI authentication, credential prep, and SDK/token state.

Extracted verbatim from google_vertex_client.py (Gatewayz One Phase 0c-2). Holds
the lazy SDK/protobuf imports, the OAuth2 access-token cache, temp-credential
handling, and ``initialize_vertex_ai``. The mutable module globals
(``_vertexai``, ``_TOKEN_CACHE`` etc.) are owned here and only touched by these
functions; the request implementations in google_vertex_client.py consume SDK
objects / tokens via these functions' return values.

These functions are re-imported into google_vertex_client so that
``google_vertex_client.<fn>`` stays patchable by the existing test suite.
"""

import json
import logging
import os
import tempfile
import threading
import time

from src.config import Config

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
