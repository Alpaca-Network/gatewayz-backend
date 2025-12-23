import logging
import os
import time
import httpx

from src.config.config import Config
from supabase import Client, create_client
from supabase.client import ClientOptions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_supabase_client: Client | None = None
_last_error: Exception | None = None  # Track last initialization error
_last_error_time: float = 0  # Timestamp of last error
ERROR_CACHE_TTL = 60.0  # Retry after 60 seconds


def get_supabase_client() -> Client:
    global _supabase_client, _last_error, _last_error_time

    if _supabase_client is not None:
        return _supabase_client

    # Check if error is stale (>60s old), retry if so
    if _last_error is not None:
        time_since_error = time.time() - _last_error_time
        if time_since_error < ERROR_CACHE_TTL:
            # Error is still fresh, don't retry yet
            retry_in = int(ERROR_CACHE_TTL - time_since_error)
            logger.debug(
                f"Supabase client unavailable (retry in {retry_in}s). "
                f"Last error: {_last_error}"
            )
            raise RuntimeError(
                f"Supabase unavailable (retry in {retry_in}s): {_last_error}"
            ) from _last_error
        else:
            # Error is stale, clear it and retry
            logger.info("Error cache expired, retrying Supabase initialization...")
            _last_error = None
            _last_error_time = 0

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

        # Determine deployment environment for optimal connection pool settings
        is_serverless = os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")

        # Configure HTTP client with environment-specific connection pool settings
        # IMPORTANT: base_url must be set so postgrest relative paths resolve correctly
        # IMPORTANT: headers must include apikey and Authorization for Supabase auth
        # IMPORTANT: Using sync Client (not AsyncClient) as Supabase SDK requires it

        if is_serverless:
            # Serverless: Conservative limits to prevent connection exhaustion
            max_conn, keepalive_conn = 30, 10
            logger.info("Using serverless-optimized connection pool settings")
        else:
            # Container/Railway: Higher limits for better concurrent request handling
            max_conn, keepalive_conn = 100, 30
            logger.info("Using container-optimized connection pool settings")

        httpx_client = httpx.Client(
            base_url=postgrest_base_url,
            headers={
                "apikey": Config.SUPABASE_KEY,
                "Authorization": f"Bearer {Config.SUPABASE_KEY}",
            },
            timeout=httpx.Timeout(45.0, connect=10.0),  # Increased from 30s to 45s for complex queries
            limits=httpx.Limits(
                max_connections=max_conn,
                max_keepalive_connections=keepalive_conn,
                keepalive_expiry=60.0,  # 60s to reduce connection churn
            ),
            http2=True,  # Enable HTTP/2 for connection multiplexing
        )

        _supabase_client = create_client(
            supabase_url=Config.SUPABASE_URL,
            supabase_key=Config.SUPABASE_KEY,
            options=ClientOptions(
                postgrest_client_timeout=45,  # 45 second timeout (increased for complex queries)
                storage_client_timeout=45,  # Consistent with httpx timeout
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
        # Store the error with timestamp for time-based retry
        _last_error = e
        _last_error_time = time.time()

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
    global _supabase_client, _last_error

    status = {
        "initialized": _supabase_client is not None,
        "has_error": _last_error is not None,
        "error_message": str(_last_error) if _last_error else None,
        "error_type": type(_last_error).__name__ if _last_error else None,
    }

    return status


def cleanup_supabase_client():
    """
    Cleanup the Supabase client and close httpx connections.

    This should be called during application shutdown to ensure
    all connections are properly closed and resources are released.
    """
    global _supabase_client

    try:
        if _supabase_client is not None:
            # Close httpx client if it was injected
            if hasattr(_supabase_client, 'postgrest') and hasattr(_supabase_client.postgrest, 'session'):
                session = _supabase_client.postgrest.session
                if hasattr(session, 'close'):
                    session.close()
                    logger.info("✅ Supabase httpx client closed successfully")

            _supabase_client = None
            logger.info("✅ Supabase client cleanup completed")
    except Exception as e:
        logger.warning(f"Error during Supabase client cleanup: {e}")


def reset_supabase_client():
    """
    Reset the Supabase client by closing the existing connection and clearing the cache.

    This is useful when HTTP/2 connection pool errors occur (e.g., LocalProtocolError)
    due to server-side connection resets or stale connections. The next call to
    get_supabase_client() will create a fresh client with a new connection pool.

    Returns:
        bool: True if reset was performed, False if no client was cached
    """
    global _supabase_client, _last_error, _last_error_time

    try:
        if _supabase_client is not None:
            # Try to close the httpx client gracefully
            try:
                if hasattr(_supabase_client, 'postgrest') and hasattr(_supabase_client.postgrest, 'session'):
                    session = _supabase_client.postgrest.session
                    if hasattr(session, 'close'):
                        session.close()
            except Exception as close_error:
                logger.debug(f"Error closing httpx client during reset: {close_error}")

            _supabase_client = None
            # Clear any cached errors so we can retry immediately
            _last_error = None
            _last_error_time = 0
            logger.info("🔄 Supabase client reset - next request will create fresh connection")
            return True
        return False
    except Exception as e:
        logger.warning(f"Error during Supabase client reset: {e}")
        # Force clear the client anyway
        _supabase_client = None
        _last_error = None
        _last_error_time = 0
        return True


def is_http2_protocol_error(error: Exception) -> bool:
    """
    Check if an exception is an HTTP/2 protocol error that requires connection reset.

    These errors typically occur when:
    - Server-side connection reset
    - HTTP/2 stream state corruption
    - Stale keepalive connections

    Args:
        error: The exception to check

    Returns:
        bool: True if this is an HTTP/2 protocol error requiring reset
    """
    error_str = str(error).lower()
    error_type = type(error).__name__

    # Check error type name for protocol errors
    if "protocolerror" in error_type.lower():
        return True

    # Check for httpx/httpcore HTTP/2 specific protocol errors
    # These patterns are specific to HTTP/2 state machine errors
    http2_error_indicators = [
        "streaminputs.send_headers",
        "streaminputs.recv_data",
        "connectioninputs.recv_data",
        "connectionstate.closed",
        "in state 5",  # HTTP/2 stream closed state (more specific)
        "in state connectionstate",  # HTTP/2 connection state errors
        "stream closed",
        "connection reset by peer",
        "goaway",
        "h2_error",
        "http2 error",
    ]

    # Check error message for HTTP/2 specific patterns
    for indicator in http2_error_indicators:
        if indicator in error_str:
            return True

    # Check for httpcore/httpx specific protocol error messages
    # These typically contain both "invalid input" AND a state reference
    if "invalid input" in error_str and ("state" in error_str or "inputs" in error_str):
        return True

    # Check for connection closed errors that are HTTP/2 specific
    if "connection closed" in error_str and ("http2" in error_str or "h2" in error_str):
        return True

    return False


def execute_with_retry(operation, max_retries: int = 2, operation_name: str = "database operation"):
    """
    Execute a database operation with automatic retry on HTTP/2 protocol errors.

    When an HTTP/2 protocol error occurs (e.g., LocalProtocolError due to stale
    connections), this function will reset the Supabase client and retry the
    operation with a fresh connection.

    Args:
        operation: A callable that performs the database operation.
                   It should accept a Supabase client as its first argument.
        max_retries: Maximum number of retry attempts (default: 2)
        operation_name: Name of the operation for logging purposes

    Returns:
        The result of the operation

    Raises:
        Exception: If all retry attempts fail

    Example:
        def insert_activity(client):
            return client.table("activity_log").insert(data).execute()

        result = execute_with_retry(insert_activity, operation_name="log_activity")
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            client = get_supabase_client()
            return operation(client)
        except Exception as e:
            last_error = e

            if is_http2_protocol_error(e):
                if attempt < max_retries:
                    logger.warning(
                        f"HTTP/2 protocol error in {operation_name} (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Resetting client and retrying..."
                    )
                    reset_supabase_client()
                    # Small delay to allow connection cleanup
                    time.sleep(0.1)
                    continue
                else:
                    logger.error(
                        f"HTTP/2 protocol error in {operation_name} after {max_retries + 1} attempts: {e}"
                    )
                    raise
            else:
                # Not an HTTP/2 error, don't retry
                raise

    # Should not reach here, but just in case
    raise last_error if last_error else RuntimeError(f"{operation_name} failed with no error captured")


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
