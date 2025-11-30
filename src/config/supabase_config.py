import logging
import httpx

from src.config.config import Config
from supabase import Client, create_client
from supabase.client import ClientOptions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_supabase_client: Client | None = None


def get_supabase_client() -> Client:
    global _supabase_client

    if _supabase_client is not None:
        return _supabase_client

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

        # Configure HTTP client with better connection pooling for HTTP/2
        # This helps prevent connection resets under high concurrency
        # IMPORTANT: base_url must be set so postgrest relative paths resolve correctly
        # IMPORTANT: headers must include apikey and Authorization for Supabase auth
        httpx_client = httpx.Client(
            base_url=postgrest_base_url,
            headers={
                "apikey": Config.SUPABASE_KEY,
                "Authorization": f"Bearer {Config.SUPABASE_KEY}",
            },
            timeout=httpx.Timeout(120.0, connect=10.0),  # 120s total, 10s connect timeout
            limits=httpx.Limits(
                max_connections=100,  # Maximum total connections in the pool
                max_keepalive_connections=20,  # Keep alive connections to reuse
                keepalive_expiry=30.0,  # Keep connections alive for 30 seconds
            ),
            http2=True,  # Enable HTTP/2 explicitly
        )

        _supabase_client = create_client(
            supabase_url=Config.SUPABASE_URL,
            supabase_key=Config.SUPABASE_KEY,
            options=ClientOptions(
                postgrest_client_timeout=120,  # 120 second timeout for database operations
                storage_client_timeout=120,
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

        test_connection()

        return _supabase_client

    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        raise RuntimeError(f"Supabase client initialization failed: {e}") from e


def test_connection() -> bool:
    try:
        client = get_supabase_client()
        client.table("users").select("*").limit(1).execute()
        return True
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
