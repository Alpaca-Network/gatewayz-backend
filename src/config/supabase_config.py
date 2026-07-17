"""
Supabase client configuration with module-level singleton pool.

Connection pooling strategy
---------------------------
The Supabase Python SDK uses ``httpx.Client`` (sync) internally for all
PostgREST queries.  By default the SDK creates a new ``httpx.Client`` per
``create_client()`` call, which means each call would open fresh TCP
connections on every request — no connection reuse.

To fix this we:
1. Create each ``httpx.Client`` once, configured with ``httpx.Limits``
   (max_connections, max_keepalive_connections) and inject it into the
   PostgREST session (``client.postgrest.session = httpx_client``).
2. Cache the resulting Supabase ``Client`` objects in module-level globals
   (``_supabase_client``, ``_read_replica_client``, ``_sync_client``).
3. Guard each initialisation path with a ``threading.Lock`` + double-checked
   locking so only one thread ever builds the client.

The three singleton clients and their connection budgets:
  - Primary API client  : 80 connections  (general reads + writes)
  - Read-replica client : 30-100 connections  (catalog SELECT queries, if configured)
  - Sync client         : 20 connections  (bulk model sync, isolated to prevent API downtime)
"""

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
_sync_client: Client | None = None  # Dedicated client for model sync operations (separate pool)
_last_error: Exception | None = None  # Track last initialization error
_last_error_time: float = 0  # Timestamp of last error
_client_lock = threading.Lock()  # Thread-safe client access
_replica_lock = threading.Lock()  # Thread-safe replica access
_sync_lock = threading.Lock()  # Thread-safe sync client access
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
            # Container/Railway: Reduced from 100 to 80 to reserve 20 for sync client
            # TOTAL CONNECTIONS = 80 (API) + 20 (sync) = 100 connections
            max_conn, keepalive_conn = 80, 25
            logger.info(
                "Using container-optimized connection pool: 80 connections "
                "(20 reserved for sync operations)"
            )

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
                keepalive_expiry=120.0,  # Increased to 120s to reduce reconnection overhead
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
        if hasattr(_supabase_client, "postgrest") and hasattr(
            _supabase_client.postgrest, "session"
        ):
            _supabase_client.postgrest.session = httpx_client
            logger.info(
                "Configured Supabase client with optimized connection pool "
                "(HTTP/1.1, %d max connections, base_url: %s)",
                max_conn,
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
            exc_info=True,  # Include full traceback in logs
        )

        # Capture to Sentry with additional context
        try:
            import sentry_sdk

            with sentry_sdk.push_scope() as scope:
                scope.set_context(
                    "supabase_config",
                    {
                        "supabase_url_set": bool(Config.SUPABASE_URL),
                        "supabase_key_set": bool(Config.SUPABASE_KEY),
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    },
                )
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
                scope.set_context(
                    "connection_test",
                    {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "test_table": "users",
                    },
                )
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
    Cleanup all Supabase clients and close httpx connections.

    This should be called during application shutdown to ensure
    all connections are properly closed and resources are released.

    Cleans up:
    - Primary API client
    - Read replica client
    - Dedicated sync client
    """
    global _supabase_client, _read_replica_client, _sync_client

    try:
        # Cleanup primary client
        with _client_lock:
            if _supabase_client is not None:
                if hasattr(_supabase_client, "postgrest") and hasattr(
                    _supabase_client.postgrest, "session"
                ):
                    session = _supabase_client.postgrest.session
                    if hasattr(session, "close"):
                        session.close()
                        logger.info("✅ Primary Supabase client closed successfully")
                _supabase_client = None

        # Cleanup read replica client
        with _replica_lock:
            if _read_replica_client is not None:
                if hasattr(_read_replica_client, "postgrest") and hasattr(
                    _read_replica_client.postgrest, "session"
                ):
                    session = _read_replica_client.postgrest.session
                    if hasattr(session, "close"):
                        session.close()
                        logger.info("✅ Read replica client closed successfully")
                _read_replica_client = None

        # Cleanup sync client
        with _sync_lock:
            if _sync_client is not None:
                if hasattr(_sync_client, "postgrest") and hasattr(
                    _sync_client.postgrest, "session"
                ):
                    session = _sync_client.postgrest.session
                    if hasattr(session, "close"):
                        session.close()
                        logger.info("✅ Sync client closed successfully")
                _sync_client = None

        logger.info("✅ All Supabase clients cleaned up successfully")
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
                if hasattr(_supabase_client, "postgrest") and hasattr(
                    _supabase_client.postgrest, "session"
                ):
                    session = _supabase_client.postgrest.session
                    if hasattr(session, "close"):
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
        "bad file descriptor",
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
            logger.info("Initializing read replica client...")

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
                    keepalive_expiry=120.0,  # Increased to 120s to reduce reconnection overhead
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
            if hasattr(_read_replica_client, "postgrest") and hasattr(
                _read_replica_client.postgrest, "session"
            ):
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


def get_sync_client() -> Client:
    """
    Get dedicated Supabase client for model sync operations.

    This client has a SEPARATE connection pool (20 connections) to prevent
    sync operations from exhausting connections needed for API requests.

    CONNECTION POOL ISOLATION:
    - API requests: 80 connections (primary client)
    - Model sync: 20 connections (this client)
    - Total: 100 connections to Supabase

    This prevents the 8-minute API downtime during bulk model syncs.

    Returns:
        Dedicated Supabase client for sync operations

    Usage:
        # In model sync operations
        sync_client = get_sync_client()
        sync_client.table("models").insert(models).execute()
    """
    global _sync_client

    # Fast path: return cached client if available
    if _sync_client is not None:
        return _sync_client

    # Slow path: initialize client with thread safety
    with _sync_lock:
        # Double-check after acquiring lock
        if _sync_client is not None:
            return _sync_client

        try:
            Config.validate()

            logger.info("🔄 Initializing dedicated Supabase sync client...")
            logger.info(f"   Config validation: {'OK' if Config.SUPABASE_URL else 'MISSING URL'}")

            postgrest_base_url = f"{Config.SUPABASE_URL}/rest/v1"
            logger.info(f"   PostgREST URL: {postgrest_base_url[:50]}...")

            # SYNC-SPECIFIC CONNECTION POOL: Smaller pool for sync operations
            # This prevents sync from exhausting all available connections
            max_conn = 20  # Dedicated for sync (vs 80-100 for API)
            keepalive_conn = 10

            logger.info(
                f"🔧 Sync client connection pool: {max_conn} max connections "
                f"(isolated from API traffic)"
            )

            # Configure transport with retry
            transport = httpx.HTTPTransport(
                retries=3,
                http2=False,  # Disable HTTP/2 for stability
            )

            httpx_client = httpx.Client(
                base_url=postgrest_base_url,
                headers={
                    "apikey": Config.SUPABASE_KEY,
                    "Authorization": f"Bearer {Config.SUPABASE_KEY}",
                },
                # Longer timeout for sync operations (can take time for bulk inserts)
                timeout=httpx.Timeout(60.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=max_conn,
                    max_keepalive_connections=keepalive_conn,
                    keepalive_expiry=120.0,
                ),
                transport=transport,
            )

            _sync_client = create_client(
                supabase_url=Config.SUPABASE_URL,
                supabase_key=Config.SUPABASE_KEY,
                options=ClientOptions(
                    postgrest_client_timeout=60,  # Longer timeout for bulk operations
                    storage_client_timeout=60,
                    schema="public",
                    headers={"X-Client-Info": "gatewayz-backend-sync/1.0"},
                ),
            )

            # Inject the configured httpx client
            if hasattr(_sync_client, "postgrest") and hasattr(_sync_client.postgrest, "session"):
                _sync_client.postgrest.session = httpx_client
                logger.info(
                    "✅ Configured dedicated sync client with isolated connection pool "
                    f"({max_conn} connections)"
                )

            return _sync_client

        except Exception as e:
            logger.error(f"❌ Failed to initialize sync client: {type(e).__name__}: {e}")
            raise RuntimeError(f"Sync client initialization failed: {e}") from e


def get_client_for_query(read_only: bool = False, for_sync: bool = False) -> Client:
    """
    Get appropriate Supabase client based on query type.

    This is the recommended way to get a database client as it automatically
    routes queries to the appropriate connection pool.

    Args:
        read_only: True for SELECT queries, False for writes (INSERT/UPDATE/DELETE)
        for_sync: True for model sync operations (uses dedicated pool to prevent API downtime)

    Returns:
        - Sync client for sync operations (dedicated 20-connection pool)
        - Read replica client for read-only queries (if configured)
        - Primary client for regular writes (80-100 connection pool)

    Usage:
        # Read-only query (use replica if available)
        client = get_client_for_query(read_only=True)
        models = client.table("models").select("*").execute()

        # Write query (always use primary)
        client = get_client_for_query(read_only=False)
        client.table("models").insert({"name": "gpt-4"}).execute()

        # Model sync operation (use dedicated sync pool)
        client = get_client_for_query(for_sync=True)
        client.table("models").upsert(bulk_models).execute()
    """
    # Sync operations get dedicated pool to prevent API downtime
    if for_sync:
        logger.debug("Using dedicated sync client (isolated connection pool)")
        return get_sync_client()

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


# ============================================================================
# Direct PostgreSQL (psycopg2) configuration — merged from db_config.py
# (MVP Task 17). Separate concern from the Supabase REST client above, but
# consolidated here as the single DB-config module. Moved verbatim.
# ============================================================================

import logging
import os
from contextlib import contextmanager

# Conditional imports
try:
    import psycopg2
    from psycopg2 import extras, pool

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    psycopg2 = None
    pool = None
    extras = None
    # Note: This is expected in Supabase deployments where PostgreSQL client
    # connections are handled via PostgREST API rather than direct psycopg2
    logging.debug(
        "psycopg2 not installed. Direct PostgreSQL features will use Supabase PostgREST API."
    )

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """PostgreSQL database configuration and connection management"""

    def __init__(self):
        # Database connection parameters
        self.db_host = os.environ.get("DB_HOST", "localhost")
        self.db_port = int(os.environ.get("DB_PORT", "5432"))
        self.db_name = os.environ.get("DB_NAME", "gatewayz_db")
        self.db_user = os.environ.get("DB_USER", "gatewayz")
        self.db_password = os.environ.get("DB_PASSWORD", "gatewayz_dev_password")

        # Connection pool settings
        self.db_min_connections = int(os.environ.get("DB_MIN_CONNECTIONS", "1"))
        # FREEZE FIX: Increased from 10 → 20. psycopg2 ThreadedConnectionPool.getconn()
        # blocks indefinitely when the pool is exhausted; 10 connections is too low under load.
        self.db_max_connections = int(os.environ.get("DB_MAX_CONNECTIONS", "20"))

        # Connection pool instance (type hint is Optional[Any] when psycopg2 not available)
        self._connection_pool = None

        # Feature flag
        self.enabled = PSYCOPG2_AVAILABLE

    def get_connection_string(self) -> str:
        """Get PostgreSQL connection string"""
        return (
            f"host={self.db_host} "
            f"port={self.db_port} "
            f"dbname={self.db_name} "
            f"user={self.db_user} "
            f"password={self.db_password}"
        )

    def get_connection_dict(self) -> dict:
        """Get connection parameters as dictionary"""
        return {
            "host": self.db_host,
            "port": self.db_port,
            "database": self.db_name,
            "user": self.db_user,
            "password": self.db_password,
        }

    def get_connection_pool(self):
        """
        Get or create database connection pool.
        Uses connection pooling for better performance.

        Returns:
            psycopg2.pool.ThreadedConnectionPool instance
        """
        if not self.enabled:
            raise RuntimeError(
                "PostgreSQL support not available. "
                "Install psycopg2-binary: pip install psycopg2-binary"
            )

        if self._connection_pool is None:
            try:
                self._connection_pool = pool.ThreadedConnectionPool(
                    self.db_min_connections,
                    self.db_max_connections,
                    host=self.db_host,
                    port=self.db_port,
                    database=self.db_name,
                    user=self.db_user,
                    password=self.db_password,
                    # Connection options
                    connect_timeout=10,
                    options="-c timezone=timezone.utc",
                )
                logger.info(
                    f"Database connection pool created: "
                    f"{self.db_host}:{self.db_port}/{self.db_name} "
                    f"(min={self.db_min_connections}, max={self.db_max_connections})"
                )
            except Exception as e:
                logger.error(f"Failed to create database connection pool: {e}")
                raise RuntimeError(f"Database connection pool creation failed: {e}") from e

        return self._connection_pool

    @contextmanager
    def get_connection(self):
        """
        Context manager for database connections.
        Automatically handles connection checkout, commit, rollback, and return.

        Usage:
            with db_config.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users")
                results = cursor.fetchall()
                cursor.close()
        """
        if not self.enabled:
            raise RuntimeError(
                "PostgreSQL support not available. "
                "Install psycopg2-binary: pip install psycopg2-binary"
            )

        conn = None
        pool_instance = None

        try:
            # Get connection from pool
            pool_instance = self.get_connection_pool()
            conn = pool_instance.getconn()

            if conn is None:
                raise RuntimeError("Failed to get connection from pool")

            # Yield connection to caller
            yield conn

            # Commit transaction if no exception occurred
            conn.commit()

        except Exception as e:
            # Rollback on error
            if conn:
                try:
                    conn.rollback()
                    logger.warning(f"Transaction rolled back due to error: {e}")
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback transaction: {rollback_error}")

            logger.error(f"Database operation failed: {e}")
            raise

        finally:
            # Return connection to pool
            if conn and pool_instance:
                try:
                    pool_instance.putconn(conn)
                except Exception as putconn_error:
                    logger.error(f"Failed to return connection to pool: {putconn_error}")

    def test_connection(self) -> bool:
        """
        Test database connectivity.
        Returns True if connection successful, False otherwise.
        """
        if not self.enabled:
            logger.warning("PostgreSQL support not available")
            return False

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 as test")
                result = cursor.fetchone()
                cursor.close()

                if result and result[0] == 1:
                    logger.info("Database connection test successful")
                    return True
                else:
                    logger.error("Database connection test failed: unexpected result")
                    return False

        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False

    def get_database_info(self) -> dict:
        """
        Get information about the database server.
        Returns dict with version, name, etc.
        """
        if not self.enabled:
            return {"error": "PostgreSQL support not available"}

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Get PostgreSQL version
                cursor.execute("SELECT version();")
                version = cursor.fetchone()[0]

                # Get current database
                cursor.execute("SELECT current_database();")
                current_db = cursor.fetchone()[0]

                # Get current user
                cursor.execute("SELECT current_user;")
                current_user = cursor.fetchone()[0]

                # Get database size
                cursor.execute("""
                    SELECT pg_size_pretty(pg_database_size(current_database()));
                """)
                db_size = cursor.fetchone()[0]

                cursor.close()

                return {
                    "version": version,
                    "database": current_db,
                    "user": current_user,
                    "size": db_size,
                    "host": self.db_host,
                    "port": self.db_port,
                }

        except Exception as e:
            logger.error(f"Failed to get database info: {e}")
            return {"error": str(e)}

    def close_all_connections(self):
        """
        Close all connections in the pool.
        Call this when shutting down the application.
        """
        if self._connection_pool:
            try:
                self._connection_pool.closeall()
                logger.info("All database connections closed")
                self._connection_pool = None
            except Exception as e:
                logger.error(f"Error closing connection pool: {e}")

    def execute_query(self, query: str, params: tuple = None, fetch_one: bool = False):
        """
        Helper method to execute a query and return results.

        Args:
            query: SQL query to execute
            params: Query parameters (optional)
            fetch_one: If True, return only first row

        Returns:
            Query results (list of tuples or single tuple if fetch_one=True)
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)

                if fetch_one:
                    result = cursor.fetchone()
                else:
                    result = cursor.fetchall()

                cursor.close()
                return result

        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            raise

    def execute_many(self, query: str, params_list: list):
        """
        Execute a query with multiple parameter sets (batch insert/update).

        Args:
            query: SQL query with placeholders
            params_list: List of parameter tuples

        Returns:
            Number of rows affected
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany(query, params_list)
                row_count = cursor.rowcount
                cursor.close()
                return row_count

        except Exception as e:
            logger.error(f"Batch execution failed: {e}")
            raise


# Global database configuration instance
_db_config: DatabaseConfig | None = None


def get_db_config() -> DatabaseConfig:
    """
    Get the global database configuration instance (singleton pattern).

    Returns:
        DatabaseConfig instance
    """
    global _db_config
    if _db_config is None:
        _db_config = DatabaseConfig()
    return _db_config


def get_db_connection():
    """
    Get database connection context manager.
    This is the primary function to use for database operations.

    Usage:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users")
            results = cursor.fetchall()

    Returns:
        Context manager for database connection
    """
    config = get_db_config()
    return config.get_connection()


def test_db_connection() -> bool:
    """
    Test database connection.
    Convenience function for quick connection testing.

    Returns:
        True if connection successful, False otherwise
    """
    config = get_db_config()
    return config.test_connection()


def close_db_connections():
    """
    Close all database connections.
    Call this on application shutdown.
    """
    config = get_db_config()
    config.close_all_connections()


def is_db_available() -> bool:
    """
    Check if PostgreSQL database is available.

    Returns:
        True if psycopg2 is installed and database is configured
    """
    config = get_db_config()
    return config.enabled
