import logging
import httpx

from src.config.config import Config
from supabase import Client, create_client
from supabase.client import ClientOptions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_supabase_client: Client | None = None
_initialization_error: Exception | None = None  # Track last initialization error


def get_supabase_client() -> Client:
    global _supabase_client, _initialization_error

    if _supabase_client is not None:
        return _supabase_client

    # If previous initialization failed, re-raise the error with context
    if _initialization_error is not None:
        logger.error(
            f"Supabase client initialization previously failed. "
            f"Last error: {_initialization_error}"
        )
        # Capture to Sentry if available
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(_initialization_error)
        except ImportError:
            pass
        raise RuntimeError(
            f"Supabase client is unavailable due to previous initialization failure: "
            f"{_initialization_error}"
        ) from _initialization_error

    try:
        Config.validate()

        # Additional runtime validation for SUPABASE_URL
        # Log the URL (masked) for debugging configuration issues
        url_value = Config.SUPABASE_URL
        if url_value:
            # Mask the URL for logging (show protocol and domain structure only)
            masked_url = url_value[:30] + "..." if len(url_value) > 30 else url_value
            logger.info(f"Initializing Supabase client with URL: {masked_url}")
        else:
            logger.error("SUPABASE_URL is not set or empty")

        if not Config.SUPABASE_URL:
            raise RuntimeError(
                "SUPABASE_URL environment variable is not set. "
                "Please configure it with your Supabase project URL (e.g., https://xxxxx.supabase.co)"
            )
        if not Config.SUPABASE_URL.startswith(("http://", "https://")):
            raise RuntimeError(
                f"SUPABASE_URL must start with 'http://' or 'https://'. "
                f"Current value: '{Config.SUPABASE_URL}'. "
                f"Expected: 'https://{Config.SUPABASE_URL}'"
            )

        # Build the PostgREST base URL from the Supabase URL
        postgrest_base_url = f"{Config.SUPABASE_URL}/rest/v1"

        # Configure HTTP client optimized for serverless/async environments
        # IMPORTANT: base_url must be set so postgrest relative paths resolve correctly
        # IMPORTANT: headers must include apikey and Authorization for Supabase auth
        # IMPORTANT: Using sync Client (not AsyncClient) as Supabase SDK requires it
        httpx_client = httpx.Client(
            base_url=postgrest_base_url,
            headers={
                "apikey": Config.SUPABASE_KEY,
                "Authorization": f"Bearer {Config.SUPABASE_KEY}",
            },
            timeout=httpx.Timeout(30.0, connect=5.0),  # 30s total, 5s connect (reduced from 120s)
            limits=httpx.Limits(
                max_connections=30,  # Reduced from 100 for serverless
                max_keepalive_connections=10,  # Reduced from 20
                keepalive_expiry=60.0,  # Increased from 30s to reduce connection churn
            ),
            http2=True,  # Enable HTTP/2 for connection multiplexing
        )

        _supabase_client = create_client(
            supabase_url=Config.SUPABASE_URL,
            supabase_key=Config.SUPABASE_KEY,
            options=ClientOptions(
                postgrest_client_timeout=30,  # 30 second timeout (reduced from 120s)
                storage_client_timeout=30,  # Consistent with httpx timeout
                schema="public",
                headers={"X-Client-Info": "gatewayz-backend/1.0"},
            ),
        )

        # Inject the configured httpx client into the postgrest client
        # This ensures all database operations use our optimized connection pool
        if hasattr(_supabase_client, 'postgrest') and hasattr(_supabase_client.postgrest, 'session'):
            _supabase_client.postgrest.session = httpx_client
            logger.info(
                "Configured Supabase client with optimized HTTP/2 connection pooling (base_url: %s)",
                postgrest_base_url,
            )

        # Test connection using the client directly to avoid circular dependency
        # (calling test_connection() would recursively call get_supabase_client())
        _test_connection_internal(_supabase_client)

        return _supabase_client

    except Exception as e:
        # Store the error for future reference
        _initialization_error = e

        # Log detailed error information
        logger.error(
            f"❌ Failed to initialize Supabase client: {type(e).__name__}: {e}",
            exc_info=True  # Include full traceback in logs
        )

        # Capture to Sentry with additional context
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_context("supabase_config", {
                    "supabase_url_set": bool(Config.SUPABASE_URL),
                    "supabase_key_set": bool(Config.SUPABASE_KEY),
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                })
                scope.set_tag("component", "supabase_client")
                scope.set_tag("initialization_phase", "get_supabase_client")
                scope.level = "error"
                sentry_sdk.capture_exception(e)
                logger.info("Error captured to Sentry for monitoring")
        except ImportError:
            logger.warning("Sentry not available, error not tracked remotely")
        except Exception as sentry_error:
            logger.warning(f"Failed to capture error to Sentry: {sentry_error}")

        raise RuntimeError(f"Supabase client initialization failed: {e}") from e


def _test_connection_internal(client: Client) -> bool:
    """
    Test database connection using the provided client directly.

    This internal function accepts the client as a parameter to avoid
    circular dependency during initialization.

    Args:
        client: The Supabase client instance to test

    Returns:
        True if connection is successful

    Raises:
        RuntimeError: If connection test fails
    """
    try:
        client.table("users").select("*").limit(1).execute()
        logger.info("✅ Database connection test successful")
        return True
    except Exception as e:
        logger.error(f"❌ Database connection test failed: {type(e).__name__}: {e}", exc_info=True)

        # Capture to Sentry with context
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_context("connection_test", {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "test_table": "users",
                })
                scope.set_tag("component", "supabase_client")
                scope.set_tag("initialization_phase", "connection_test")
                scope.level = "error"
                sentry_sdk.capture_exception(e)
        except (ImportError, Exception):
            pass  # Silently ignore Sentry errors during connection test

        raise RuntimeError(f"Database connection failed: {e}") from e


def test_connection() -> bool:
    """
    Test database connection using the cached or newly created client.

    This public function retrieves the client via get_supabase_client(),
    which will use the cached client if already initialized.

    Returns:
        True if connection is successful

    Raises:
        RuntimeError: If connection test fails
    """
    try:
        client = get_supabase_client()
        return _test_connection_internal(client)
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        raise RuntimeError(f"Database connection failed: {e}") from e


def init_db():
    try:
        test_connection()
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


def get_client() -> Client:
    return get_supabase_client()


def get_initialization_status() -> dict:
    """
    Get the current Supabase client initialization status.

    Returns detailed information about the client state for monitoring
    and debugging purposes.

    Returns:
        dict with keys:
            - initialized: bool - Whether client is initialized
            - has_error: bool - Whether initialization failed
            - error_message: str | None - Last error message if any
            - error_type: str | None - Last error type if any
    """
    global _supabase_client, _initialization_error

    status = {
        "initialized": _supabase_client is not None,
        "has_error": _initialization_error is not None,
        "error_message": str(_initialization_error) if _initialization_error else None,
        "error_type": type(_initialization_error).__name__ if _initialization_error else None,
    }

    return status


class _LazySupabaseClient:
    """
    Lazy proxy for the Supabase client.

    This allows `from src.config.supabase_config import supabase` to work
    while deferring client initialization until first use.
    """

    def __getattr__(self, name: str):
        # Don't delegate dunder attributes to avoid triggering initialization
        # during introspection (e.g., by unittest.mock or hasattr checks)
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(get_supabase_client(), name)

    def __repr__(self):
        return "<LazySupabaseClient proxy>"


supabase = _LazySupabaseClient()
