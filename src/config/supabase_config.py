import logging
import os
import threading
import time

import httpx
from httpx import RemoteProtocolError
from supabase.client import ClientOptions

from src.config.config import Config
from supabase import Client, create_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_supabase_client: Client | None = None
_read_replica_client: Client | None = None  # Read-only replica for catalog queries
_last_error: Exception | None = None  # Track last initialization error
_last_error_time: float = 0  # Timestamp of last error
_client_lock = threading.Lock()  # Thread-safe client access
_replica_lock = threading.Lock()  # Thread-safe replica access
ERROR_CACHE_TTL = 60.0  # Retry after 60 seconds

# Connection error types that indicate the connection needs to be refreshed
CONNECTION_ERROR_TYPES = (
    RemoteProtocolError,
    ConnectionError,
    ConnectionResetError,
    BrokenPipeError,
)


def get_supabase_client() -> Client:
    global _supabase_client, _last_error, _last_error_time

    # Fast path: return cached client if available
    if _supabase_client is not None:
        return _supabase_client

    # Slow path: initialize client with thread safety
    with _client_lock:
        # Double-check after acquiring lock
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

        # Configure transport with retry for transient connection errors
        # IMPORTANT: Disable HTTP/2 to fix "Bad file descriptor" errors (errno 9)
        # HTTP/2 connection multiplexing causes issues when Supabase closes idle connections
        # HTTP/1.1 with connection pooling provides better stability for long-running services
        transport = httpx.HTTPTransport(
            retries=3,  # Increased retries for better resilience
            http2=False,  # Disable HTTP/2 to prevent stale connection issues
        )

        httpx_client = httpx.Client(
            base_url=postgrest_base_url,
            headers={
                "apikey": Config.SUPABASE_KEY,
                "Authorization": f"Bearer {Config.SUPABASE_KEY}",
            },
            # Timeout set to 30s for general database queries
            # Catalog fetches have their own per-provider timeout (15s) enforced at app level
            # Admin queries need longer for large dataset fetches
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=max_conn,
                max_keepalive_connections=keepalive_conn,
                keepalive_expiry=20.0,  # Reduced to 20s to aggressively close idle connections
            ),
            transport=transport,
        )

        _supabase_client = create_client(
            supabase_url=Config.SUPABASE_URL,
            supabase_key=Config.SUPABASE_KEY,
            options=ClientOptions(
                postgrest_client_timeout=30,  # General queries - catalog has app-level timeout
                storage_client_timeout=30,  # Storage operations
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
        with _client_lock:
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


def refresh_supabase_client() -> Client:
    """
    Force refresh the Supabase client by closing existing connections
    and creating a new client.

    This is useful when the HTTP/2 connection has been terminated
    by the server (e.g., after handling many requests) and needs
    to be re-established.

    Returns:
        A fresh Supabase client instance

    Thread-safe: Uses locking to prevent race conditions during refresh.
    """
    global _supabase_client, _last_error, _last_error_time

    logger.info("🔄 Refreshing Supabase client due to connection issue...")

    with _client_lock:
        # Cleanup existing client
        if _supabase_client is not None:
            try:
                if hasattr(_supabase_client, 'postgrest') and hasattr(_supabase_client.postgrest, 'session'):
                    session = _supabase_client.postgrest.session
                    if hasattr(session, 'close'):
                        session.close()
                        logger.debug("Closed existing httpx session during refresh")
            except Exception as e:
                logger.warning(f"Error closing existing session during refresh: {e}")

            _supabase_client = None

        # Clear any cached errors to allow fresh initialization
        _last_error = None
        _last_error_time = 0

    # Get a fresh client (this will create a new one since we cleared the cached client)
    return get_supabase_client()


def is_connection_error(error: Exception) -> bool:
    """
    Check if an exception is a connection-related error that can be
    recovered by refreshing the client.

    This includes HTTP/2 connection termination, connection resets,
    and other network-related errors.

    Args:
        error: The exception to check

    Returns:
        True if this is a recoverable connection error
    """
    # Check direct instance match
    if isinstance(error, CONNECTION_ERROR_TYPES):
        return True

    # Check error message for common connection termination patterns
    error_message = str(error).lower()
    connection_indicators = [
        "connectionterminated",
        "connection terminated",
        "connection reset",
        "connection refused",
        "broken pipe",
        "remotedisconnected",
        "remote disconnected",
        "connection closed",
        "http2 connection",
        "stream reset",
        "goaway",
    ]

    return any(indicator in error_message for indicator in connection_indicators)


def execute_with_retry(
    operation: callable,
    max_retries: int = 2,
    retry_delay: float = 0.5,
    operation_name: str = "database operation",
) -> any:
    """
    Execute a database operation with automatic retry on connection errors.

    This function wraps database operations and automatically retries them
    if a connection error occurs (e.g., HTTP/2 connection terminated).
    On connection errors, it refreshes the Supabase client before retrying.

    Args:
        operation: A callable that performs the database operation.
                   Should accept a Supabase client as its first argument.
        max_retries: Maximum number of retry attempts (default: 2)
        retry_delay: Delay in seconds between retries (default: 0.5)
        operation_name: Name of the operation for logging purposes

    Returns:
        The result of the successful operation

    Raises:
        The last exception if all retries fail

    Example:
        def do_insert(client):
            return client.table("users").insert({"name": "test"}).execute()

        result = execute_with_retry(do_insert, operation_name="insert user")
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            client = get_supabase_client()
            return operation(client)

        except Exception as e:
            last_exception = e

            # Check if this is a connection error that can be recovered
            if is_connection_error(e):
                if attempt < max_retries:
                    logger.warning(
                        f"Connection error during {operation_name} (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Refreshing client and retrying in {retry_delay}s..."
                    )
                    # Refresh the client to get a new connection
                    try:
                        refresh_supabase_client()
                    except Exception as refresh_error:
                        logger.error(f"Failed to refresh client: {refresh_error}")

                    time.sleep(retry_delay)
                    continue
                else:
                    logger.error(
                        f"Connection error during {operation_name} after {max_retries + 1} attempts: {e}. "
                        f"All retries exhausted."
                    )
            else:
                # Non-connection error, don't retry
                logger.error(f"Error during {operation_name}: {e}")
                raise

    # All retries exhausted
    if last_exception:
        raise last_exception


def get_read_replica_client() -> Client | None:
    """
    Get read-only Supabase client for catalog queries.

    Read replicas offload heavy SELECT queries from the primary database,
    improving performance and reducing connection pool saturation.

    Usage:
        - Use for all catalog queries (/models, /providers endpoints)
        - Use for analytics and reporting queries
        - DO NOT use for writes (INSERT, UPDATE, DELETE)
        - Falls back to primary DB if replica not configured

    Returns:
        Read replica client if SUPABASE_READ_REPLICA_URL configured,
        otherwise None (caller should use primary client)

    Configuration:
        Set SUPABASE_READ_REPLICA_URL environment variable to enable.
        Example: SUPABASE_READ_REPLICA_URL=https://replica.supabase.co
    """
    global _read_replica_client

    # Check if read replica is configured
    read_replica_url = os.getenv("SUPABASE_READ_REPLICA_URL")
    if not read_replica_url:
        logger.debug("Read replica not configured (SUPABASE_READ_REPLICA_URL not set)")
        return None

    # Fast path: return cached replica client
    if _read_replica_client is not None:
        return _read_replica_client

    # Slow path: initialize replica client with thread safety
    with _replica_lock:
        # Double-check after acquiring lock
        if _read_replica_client is not None:
            return _read_replica_client

        try:
            logger.info(f"Initializing read replica client...")

            # Validate replica URL
            if not read_replica_url.startswith(("http://", "https://")):
                logger.error(f"Invalid SUPABASE_READ_REPLICA_URL: {read_replica_url}")
                return None

            # Build PostgREST URL
            postgrest_base_url = f"{read_replica_url}/rest/v1"

            # Determine deployment environment
            is_serverless = os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME")

            # Configure connection pool (same as primary but for reads)
            if is_serverless:
                max_conn, keepalive_conn = 30, 10
                logger.info("Read replica: Using serverless-optimized pool")
            else:
                max_conn, keepalive_conn = 100, 30
                logger.info("Read replica: Using container-optimized pool")

            # Create HTTP transport (HTTP/2 disabled for stability)
            transport = httpx.HTTPTransport(
                retries=3,
                http2=False,  # Disable HTTP/2 to prevent stale connections
            )

            # Create HTTP client for read replica
            httpx_client = httpx.Client(
                base_url=postgrest_base_url,
                headers={
                    "apikey": Config.SUPABASE_KEY,
                    "Authorization": f"Bearer {Config.SUPABASE_KEY}",
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=max_conn,
                    max_keepalive_connections=keepalive_conn,
                    keepalive_expiry=20.0,
                ),
                transport=transport,
            )

            # Create Supabase client for read replica
            _read_replica_client = create_client(
                supabase_url=read_replica_url,
                supabase_key=Config.SUPABASE_KEY,
                options=ClientOptions(
                    postgrest_client_timeout=30,
                    storage_client_timeout=30,
                    schema="public",
                    headers={"X-Client-Info": "gatewayz-backend-replica/1.0"},
                ),
            )

            # Inject HTTP client
            if hasattr(_read_replica_client, 'postgrest') and hasattr(_read_replica_client.postgrest, 'session'):
                _read_replica_client.postgrest.session = httpx_client
                logger.info(f"✅ Read replica client initialized: {postgrest_base_url}")

            # Test connection
            try:
                _read_replica_client.table("users").select("*").limit(1).execute()
                logger.info("✅ Read replica connection test successful")
            except Exception as e:
                logger.error(f"❌ Read replica connection test failed: {e}")
                _read_replica_client = None
                return None

            return _read_replica_client

        except Exception as e:
            logger.error(f"Failed to initialize read replica client: {e}")
            _read_replica_client = None
            return None


def get_client_for_query(read_only: bool = False) -> Client:
    """
    Get appropriate Supabase client based on query type.

    This is the recommended way to get a database client as it automatically
    routes read-only queries to the read replica when available.

    Args:
        read_only: True for SELECT queries, False for writes (INSERT/UPDATE/DELETE)

    Returns:
        Read replica client for read-only queries (if configured),
        otherwise primary client

    Usage:
        # Read-only query (use replica if available)
        client = get_client_for_query(read_only=True)
        models = client.table("models").select("*").execute()

        # Write query (always use primary)
        client = get_client_for_query(read_only=False)
        client.table("models").insert({"name": "gpt-4"}).execute()
    """
    if read_only:
        replica = get_read_replica_client()
        if replica:
            logger.debug("Using read replica for query")
            return replica
        logger.debug("Read replica not available, using primary DB")

    return get_supabase_client()


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
