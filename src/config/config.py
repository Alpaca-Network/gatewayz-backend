import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from ..env file
load_dotenv()


def _get_env_var(name: str, default: str | None = None, *, strip: bool = True) -> str | None:
    """
    Fetch an environment variable with optional whitespace trimming.

    Args:
        name: Environment variable to look up.
        default: Value to return when the env var is unset or empty.
        strip: Whether to strip leading/trailing whitespace (default: True).

    Returns:
        The normalized string value or the provided default when empty.
    """
    value = os.environ.get(name)
    if value is None:
        return default

    if strip:
        value = value.strip()

    return value or default


def _derive_loki_query_url(push_url: str | None) -> str:
    """
    Build a Loki query endpoint from the configured push endpoint.

    The Railway-provided URL typically ends with /loki/api/v1/push. When querying we need
    /loki/api/v1/query_range instead, but we want to preserve the scheme/host/custom base.
    """
    default_query = "http://loki:3100/loki/api/v1/query_range"
    if not push_url:
        return default_query

    normalized = push_url.rstrip("/")
    push_suffix = "/loki/api/v1/push"
    if normalized.endswith(push_suffix):
        normalized = normalized[: -len(push_suffix)]
    return f"{normalized}/loki/api/v1/query_range"


_default_loki_push_url = os.environ.get(
    "LOKI_PUSH_URL",
    "http://loki:3100/loki/api/v1/push",
)
_default_loki_query_url = os.environ.get("LOKI_QUERY_URL") or _derive_loki_query_url(
    _default_loki_push_url
)

_project_root = Path(__file__).resolve().parents[2]
_src_root = Path(__file__).resolve().parents[1]


# Use /tmp for serverless environments (read-only file system), otherwise use src/data
def _get_data_dir() -> Path:
    """Get data directory, using /tmp in serverless environments."""
    # Check if we're in a serverless environment (read-only /var/task)
    if os.path.exists("/var/task") and not os.access("/var/task", os.W_OK):
        return Path("/tmp/gatewayz_data")
    return _src_root / "data"


_default_data_dir = _get_data_dir()


def _resolve_path_env(var_name: str, default: Path) -> Path:
    """Resolve a filesystem path from environment variables with fallback."""
    value = os.environ.get(var_name)
    if not value:
        return default
    return Path(value).expanduser().resolve()


def _ensure_directory(path: Path) -> Path:
    """Ensure a directory exists and return the path."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        # If we can't create the directory, use /tmp as fallback
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to create directory {path}: {e}. Using /tmp fallback.")
        fallback = Path("/tmp") / path.name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    return path


_data_dir = _ensure_directory(_resolve_path_env("GATEWAYZ_DATA_DIR", _default_data_dir))
_pricing_history_dir = _ensure_directory(
    _resolve_path_env("PRICING_HISTORY_DIR", _data_dir / "pricing_history")
)
_pricing_backup_dir = _ensure_directory(
    _resolve_path_env("PRICING_BACKUP_DIR", _data_dir / "pricing_backups")
)
# Pricing sync log file removed - deprecated 2026-02 (Phase 3, Issue #1063)
_manual_pricing_file = _resolve_path_env("MANUAL_PRICING_FILE", _data_dir / "manual_pricing.json")


class Config:
    """Configuration class for the application"""

    # Environment Detection
    APP_ENV = os.environ.get("APP_ENV", "development")  # development, staging, production
    IS_PRODUCTION = APP_ENV == "production"
    IS_STAGING = APP_ENV == "staging"
    IS_DEVELOPMENT = APP_ENV == "development"
    IS_TESTING = APP_ENV in {"testing", "test"} or os.environ.get("TESTING", "").lower() in {
        "1",
        "true",
        "yes",
    }

    # Supabase Configuration
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    # Optional direct Postgres connection string for maintenance tasks
    SUPABASE_DB_DSN = os.environ.get("SUPABASE_DB_DSN")

    # OpenRouter Configuration
    OPENROUTER_API_KEY = _get_env_var("OPENROUTER_API_KEY")
    OPENROUTER_SITE_URL = _get_env_var("OPENROUTER_SITE_URL", "https://your-site.com")
    OPENROUTER_SITE_NAME = _get_env_var("OPENROUTER_SITE_NAME", "Openrouter AI Gateway")

    # Infron AI Configuration (formerly OneRouter)
    ONEROUTER_API_KEY = os.environ.get("ONEROUTER_API_KEY")

    # DeepInfra Configuration (for direct API access)
    DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY")
    XAI_API_KEY = os.environ.get("XAI_API_KEY")
    NOVITA_API_KEY = os.environ.get("NOVITA_API_KEY")
    NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY")
    CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY")
    HUG_API_KEY = os.environ.get("HUG_API_KEY")

    # Featherless.ai Configuration
    FEATHERLESS_API_KEY = os.environ.get("FEATHERLESS_API_KEY")

    # OpenAI Direct API Configuration
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

    # Anthropic Direct API / Autonomous Monitoring
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    # Model to use for Anthropic API calls (bug fix generator, autonomous monitoring)
    # Valid models: claude-3-5-sonnet-20241022, claude-3-opus-20240229, claude-3-sonnet-20240229, claude-3-haiku-20240307
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    # Chutes.ai Configuration
    CHUTES_API_KEY = os.environ.get("CHUTES_API_KEY")

    # Fireworks.ai Configuration
    FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY")

    # Together.ai Configuration
    TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY")

    # Groq Configuration
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

    # AIMO Configuration
    AIMO_API_KEY = os.environ.get("AIMO_API_KEY")
    AIMO_FETCH_TIMEOUT = float(os.environ.get("AIMO_FETCH_TIMEOUT", "5.0"))  # 5 second timeout
    AIMO_CONNECT_TIMEOUT = float(
        os.environ.get("AIMO_CONNECT_TIMEOUT", "3.0")
    )  # 3 second connect timeout
    AIMO_MAX_RETRIES = int(os.environ.get("AIMO_MAX_RETRIES", "2"))  # Retry up to 2 times
    AIMO_ENABLE_HTTP_FALLBACK = (
        os.environ.get("AIMO_ENABLE_HTTP_FALLBACK", "true").lower() == "true"
    )
    AIMO_BASE_URLS = [
        "https://beta.aimo.network/api/v1",
    ]  # Primary URL (beta.aimo.network is the active endpoint)

    # Near AI Configuration
    NEAR_API_KEY = os.environ.get("NEAR_API_KEY")

    # Vercel AI Gateway Configuration
    VERCEL_AI_GATEWAY_API_KEY = os.environ.get("VERCEL_AI_GATEWAY_API_KEY")

    # Helicone AI Gateway Configuration
    HELICONE_API_KEY = os.environ.get("HELICONE_API_KEY")

    # Vercel AI SDK Configuration
    AI_SDK_API_KEY = os.environ.get("AI_SDK_API_KEY")

    # AiHubMix Configuration
    AIHUBMIX_API_KEY = os.environ.get("AIHUBMIX_API_KEY")
    AIHUBMIX_APP_CODE = os.environ.get("AIHUBMIX_APP_CODE")

    # Fal.ai Configuration
    FAL_API_KEY = os.environ.get("FAL_API_KEY")

    # Resemble AI / Chatterbox TTS Configuration
    RESEMBLE_API_KEY = os.environ.get("RESEMBLE_API_KEY")

    # Tavily Web Search Configuration
    TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

    # Anannas Configuration
    ANANNAS_API_KEY = os.environ.get("ANANNAS_API_KEY")

    # Alpaca Network Configuration
    ALPACA_NETWORK_API_KEY = os.environ.get("ALPACA_NETWORK_API_KEY")

    # Alibaba Cloud Configuration
    ALIBABA_CLOUD_API_KEY = os.environ.get("ALIBABA_CLOUD_API_KEY")
    ALIBABA_CLOUD_API_KEY_INTERNATIONAL = os.environ.get("ALIBABA_CLOUD_API_KEY_INTERNATIONAL")
    ALIBABA_CLOUD_API_KEY_CHINA = os.environ.get("ALIBABA_CLOUD_API_KEY_CHINA")
    ALIBABA_CLOUD_REGION = os.environ.get(
        "ALIBABA_CLOUD_REGION", "international"
    )  # 'international' or 'china'

    # Clarifai Configuration
    CLARIFAI_API_KEY = os.environ.get("CLARIFAI_API_KEY")
    CLARIFAI_USER_ID = os.environ.get("CLARIFAI_USER_ID")
    CLARIFAI_APP_ID = os.environ.get("CLARIFAI_APP_ID")

    # Akash ML Configuration
    AKASH_API_KEY = os.environ.get("AKASH_API_KEY")

    # Morpheus AI Gateway Configuration
    MORPHEUS_API_KEY = os.environ.get("MORPHEUS_API_KEY")

    # Simplismart AI Configuration
    SIMPLISMART_API_KEY = os.environ.get("SIMPLISMART_API_KEY")

    # Sybil AI Configuration
    SYBIL_API_KEY = os.environ.get("SYBIL_API_KEY")

    # Canopy Wave AI Configuration
    CANOPYWAVE_API_KEY = os.environ.get("CANOPYWAVE_API_KEY")
    CANOPYWAVE_BASE_URL = os.environ.get("CANOPYWAVE_BASE_URL", "https://inference.canopywave.io/v1")

    # Nosana GPU Computing Network Configuration
    NOSANA_API_KEY = os.environ.get("NOSANA_API_KEY")
    NOSANA_BASE_URL = os.environ.get("NOSANA_BASE_URL", "https://dashboard.k8s.prd.nos.ci/api")

    # Z.AI Configuration (Zhipu AI - GLM models)
    ZAI_API_KEY = os.environ.get("ZAI_API_KEY")

    # Soundsgood Configuration (GLM-4.5-Air distilled model)
    SOUNDSGOOD_API_KEY = os.environ.get("SOUNDSGOOD_API_KEY")

    # NotDiamond AI Router Configuration
    NOTDIAMOND_API_KEY = os.environ.get("NOTDIAMOND_API_KEY", "")
    NOTDIAMOND_TIMEOUT = int(os.environ.get("NOTDIAMOND_TIMEOUT", "10"))
    NOTDIAMOND_ENABLED = os.environ.get("NOTDIAMOND_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }

    # Butter.dev LLM Response Caching Configuration
    # Butter.dev is a caching proxy for LLM APIs that identifies patterns in responses
    # and serves cached responses to reduce costs and improve latency.
    # See: https://butter.dev
    # Supports both BUTTER_DEV_ENABLED and BUTTER_ENABLED env vars for compatibility
    BUTTER_DEV_ENABLED: bool = (
        os.environ.get("BUTTER_DEV_ENABLED", os.environ.get("BUTTER_ENABLED", "false"))
        .lower()
        in {"1", "true", "yes"}
    )
    BUTTER_DEV_BASE_URL: str = os.environ.get(
        "BUTTER_DEV_BASE_URL", os.environ.get("BUTTER_PROXY_URL", "https://proxy.butter.dev/v1")
    )
    BUTTER_DEV_TIMEOUT: int = int(os.environ.get("BUTTER_DEV_TIMEOUT", "30"))
    # Enable automatic fallback to direct provider on Butter.dev errors
    BUTTER_DEV_FALLBACK_ENABLED: bool = os.environ.get(
        "BUTTER_DEV_FALLBACK_ENABLED", "true"
    ).lower() in {"1", "true", "yes"}

    # Cloudflare Workers AI Configuration
    CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN")
    CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID")

    # Google Vertex AI Configuration (for image generation & generative APIs)
    GOOGLE_PROJECT_ID = os.environ.get("GOOGLE_PROJECT_ID", "gatewayz-468519")
    GOOGLE_VERTEX_LOCATION = os.environ.get("GOOGLE_VERTEX_LOCATION", "us-central1")
    GOOGLE_VERTEX_ENDPOINT_ID = os.environ.get("GOOGLE_VERTEX_ENDPOINT_ID", "6072619212881264640")
    GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    GOOGLE_VERTEX_TRANSPORT = os.environ.get("GOOGLE_VERTEX_TRANSPORT", "rest").lower()
    # Timeout for Google Vertex API calls. Default increased to 180s to accommodate
    # larger models like gemini-2.5-pro and preview models (gemini-3-pro-preview) which
    # may take longer to process complex requests, especially on global endpoints with cold starts.
    GOOGLE_VERTEX_TIMEOUT = float(os.environ.get("GOOGLE_VERTEX_TIMEOUT", "180"))
    # Enable regional fallback for Gemini 3 preview models. When enabled, uses regional
    # endpoints instead of global endpoints, which may provide faster response times.
    # Use this for A/B testing or when global endpoints are experiencing high latency.
    GOOGLE_VERTEX_REGIONAL_FALLBACK = os.environ.get(
        "GOOGLE_VERTEX_REGIONAL_FALLBACK", "false"
    ).lower() in {"1", "true", "yes"}

    # OpenRouter Analytics Cookie (for transaction analytics API)
    OPENROUTER_COOKIE = os.environ.get("OPENROUTER_COOKIE")

    # Admin Configuration
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

    # ==================== Monitoring & Observability Configuration ====================

    # Sentry Configuration
    SENTRY_DSN = os.environ.get("SENTRY_DSN")
    SENTRY_ENABLED = os.environ.get("SENTRY_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", APP_ENV)
    SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0"))
    SENTRY_PROFILES_SAMPLE_RATE = float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "1.0"))
    # Release tracking - automatically inferred from version or environment variable
    SENTRY_RELEASE = os.environ.get("SENTRY_RELEASE", "2.0.3")
    # Version string for release tracking
    APP_VERSION = os.environ.get("APP_VERSION", "2.0.3")

    # Prometheus Configuration
    PROMETHEUS_ENABLED = os.environ.get("PROMETHEUS_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    PROMETHEUS_REMOTE_WRITE_URL = os.environ.get(
        "PROMETHEUS_REMOTE_WRITE_URL",
        "http://prometheus:9090/api/v1/write",
    )
    PROMETHEUS_SCRAPE_ENABLED = os.environ.get("PROMETHEUS_SCRAPE_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }

    # Tempo/OpenTelemetry OTLP Configuration
    # Enabled by default for distributed tracing observability
    TEMPO_ENABLED = os.environ.get("TEMPO_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "gatewayz-api")
    # Default to localhost for local development with docker-compose monitoring stack
    # For Railway: set TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.railway.internal:4318
    TEMPO_OTLP_HTTP_ENDPOINT = os.environ.get(
        "TEMPO_OTLP_HTTP_ENDPOINT",
        "http://localhost:4318",
    )
    TEMPO_OTLP_GRPC_ENDPOINT = os.environ.get(
        "TEMPO_OTLP_GRPC_ENDPOINT",
        "localhost:4317",
    )
    # Skip endpoint reachability check during startup (allows async connection)
    TEMPO_SKIP_REACHABILITY_CHECK = os.environ.get(
        "TEMPO_SKIP_REACHABILITY_CHECK", "true"
    ).lower() in {"1", "true", "yes"}

    # Grafana Loki Configuration
    LOKI_ENABLED = os.environ.get("LOKI_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    LOKI_PUSH_URL = _default_loki_push_url
    LOKI_QUERY_URL = _default_loki_query_url

    # Grafana Cloud Configuration
    GRAFANA_CLOUD_ENABLED = os.environ.get("GRAFANA_CLOUD_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    # Grafana Cloud Prometheus (Metrics)
    GRAFANA_PROMETHEUS_REMOTE_WRITE_URL = os.environ.get("GRAFANA_PROMETHEUS_REMOTE_WRITE_URL")
    GRAFANA_PROMETHEUS_USERNAME = os.environ.get("GRAFANA_PROMETHEUS_USERNAME")
    GRAFANA_PROMETHEUS_API_KEY = os.environ.get("GRAFANA_PROMETHEUS_API_KEY")

    # Grafana Cloud Loki (Logs)
    GRAFANA_LOKI_USERNAME = os.environ.get("GRAFANA_LOKI_USERNAME")
    GRAFANA_LOKI_API_KEY = os.environ.get("GRAFANA_LOKI_API_KEY")

    # Grafana Cloud Tempo (Traces)
    GRAFANA_TEMPO_USERNAME = os.environ.get("GRAFANA_TEMPO_USERNAME")
    GRAFANA_TEMPO_API_KEY = os.environ.get("GRAFANA_TEMPO_API_KEY")

    # Arize AI Observability Configuration
    ARIZE_ENABLED = os.environ.get("ARIZE_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    ARIZE_SPACE_ID = os.environ.get("ARIZE_SPACE_ID")
    ARIZE_API_KEY = os.environ.get("ARIZE_API_KEY")
    ARIZE_PROJECT_NAME = os.environ.get("ARIZE_PROJECT_NAME", "GATEWAYZ")

    # Redis Configuration (for real-time metrics and rate limiting)
    REDIS_ENABLED = os.environ.get("REDIS_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    REDIS_MAX_CONNECTIONS = int(os.environ.get("REDIS_MAX_CONNECTIONS", "50"))
    REDIS_SOCKET_TIMEOUT = int(os.environ.get("REDIS_SOCKET_TIMEOUT", "5"))
    REDIS_SOCKET_CONNECT_TIMEOUT = int(os.environ.get("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))

    # Concurrency Control (global server-level admission gate)
    CONCURRENCY_LIMIT = int(os.environ.get("CONCURRENCY_LIMIT", "20"))
    CONCURRENCY_QUEUE_SIZE = int(os.environ.get("CONCURRENCY_QUEUE_SIZE", "50"))
    CONCURRENCY_QUEUE_TIMEOUT = float(os.environ.get("CONCURRENCY_QUEUE_TIMEOUT", "10.0"))

    # Pricing Sync Scheduler Configuration - DEPRECATED 2026-02 (Phase 3, Issue #1063)
    # Pricing is now synced via model sync (provider_model_sync_service.py)

    # ============================================================================
    # MODEL SYNC CONFIGURATION
    # ============================================================================

    # Model Sync Configuration
    # Controls scheduled background sync of models from provider APIs to database.
    # Disabled by default — the sync blocks resources for 10-20 minutes and causes
    # 499 errors. Models are already in the DB from initial sync and rarely change.
    # Re-enable with ENABLE_SCHEDULED_MODEL_SYNC=true when container has enough RAM.
    ENABLE_SCHEDULED_MODEL_SYNC: bool = os.environ.get(
        "ENABLE_SCHEDULED_MODEL_SYNC", "false"
    ).lower() in {
        "1",
        "true",
        "yes",
    }

    # How often to sync models from provider APIs (in minutes)
    # Recommended: 15-30 minutes for balance between freshness and API rate limits
    MODEL_SYNC_INTERVAL_MINUTES: int = int(os.environ.get("MODEL_SYNC_INTERVAL_MINUTES", "30"))

    # Providers to skip during scheduled sync (comma-separated slugs).
    # Featherless is skipped by default because it returns 17,796 models in
    # a single API call (~50MB), which causes OOM on Railway containers.
    # Their models are already in the DB from initial sync and rarely change.
    # Set to empty string to sync all providers: MODEL_SYNC_SKIP_PROVIDERS=""
    MODEL_SYNC_SKIP_PROVIDERS: set[str] = {
        s.strip() for s in
        os.environ.get("MODEL_SYNC_SKIP_PROVIDERS", "featherless").split(",")
        if s.strip()
    }

    # Pricing Sync Configuration - DEPRECATED 2026-02 (Phase 3, Issue #1063)
    # Pricing is now synced via model sync (provider_model_sync_service.py)
    # No separate pricing sync configuration needed

    # Metrics Aggregation Configuration
    METRICS_AGGREGATION_ENABLED = os.environ.get("METRICS_AGGREGATION_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    METRICS_AGGREGATION_INTERVAL_MINUTES = int(
        os.environ.get("METRICS_AGGREGATION_INTERVAL_MINUTES", "60")
    )
    METRICS_REDIS_RETENTION_HOURS = int(os.environ.get("METRICS_REDIS_RETENTION_HOURS", "2"))

    # ==================== Filesystem Paths ====================
    PROJECT_ROOT = _project_root
    SRC_ROOT = _src_root
    DATA_DIR = _data_dir
    PRICING_HISTORY_DIR = _pricing_history_dir
    PRICING_BACKUP_DIR = _pricing_backup_dir
    # PRICING_SYNC_LOG_FILE removed - deprecated 2026-02 (Phase 3, Issue #1063)
    MANUAL_PRICING_FILE = _manual_pricing_file

    @classmethod
    def validate(cls):
        """Validate that all required environment variables are set"""
        # In Vercel environment, only validate URL format (not presence of keys)
        # to prevent startup failures while still catching configuration errors
        is_vercel = os.environ.get("VERCEL")

        missing_vars = []
        invalid_vars = []

        # Always validate URL format, even in Vercel
        if cls.SUPABASE_URL and not cls.SUPABASE_URL.startswith(("http://", "https://")):
            url_preview = (
                cls.SUPABASE_URL[:50] + "..." if len(cls.SUPABASE_URL) > 50 else cls.SUPABASE_URL
            )
            invalid_vars.append(
                f"SUPABASE_URL must start with 'http://' or 'https://' (got: '{url_preview}'). "
                f"Example: https://{url_preview}"
            )

        # Skip presence validation in Vercel environment
        if is_vercel:
            if invalid_vars:
                raise RuntimeError(
                    "Invalid environment variables:\n" + "\n".join(f"  - {v}" for v in invalid_vars)
                )
            return True

        if not cls.SUPABASE_URL:
            missing_vars.append("SUPABASE_URL")
        if not cls.SUPABASE_KEY:
            missing_vars.append("SUPABASE_KEY")
        if not cls.OPENROUTER_API_KEY:
            missing_vars.append("OPENROUTER_API_KEY")

        error_messages = []
        if missing_vars:
            error_messages.append(
                f"Missing required environment variables: {', '.join(missing_vars)}\n"
                "Please create a .env file with the following variables:\n"
                "SUPABASE_URL=your_supabase_project_url\n"
                "SUPABASE_KEY=your_supabase_anon_key\n"
                "OPENROUTER_API_KEY=your_openrouter_api_key\n"
                "OPENROUTER_SITE_URL=your_site_url (optional)\n"
                "OPENROUTER_SITE_NAME=your_site_name (optional)"
            )
        if invalid_vars:
            error_messages.append(
                "Invalid environment variables:\n" + "\n".join(f"  - {v}" for v in invalid_vars)
            )

        if error_messages:
            raise RuntimeError("\n\n".join(error_messages))

        return True

    @classmethod
    def get_supabase_config(cls):
        """Get Supabase configuration as a tuple"""
        return cls.SUPABASE_URL, cls.SUPABASE_KEY

    @classmethod
    def validate_critical_env_vars(cls) -> tuple[bool, list[str]]:
        """
        Validate that all critical environment variables are set and valid.

        Returns:
            tuple: (is_valid, issues)
                - is_valid: bool indicating if all critical vars are present and valid
                - issues: list of missing or invalid variable descriptions
        """
        is_vercel = os.environ.get("VERCEL")
        issues = []

        # Always validate SUPABASE_URL format, even in Vercel
        if cls.SUPABASE_URL and not cls.SUPABASE_URL.startswith(("http://", "https://")):
            issues.append(
                f"SUPABASE_URL (missing http:// or https:// protocol - should be https://{cls.SUPABASE_URL})"
            )

        # Skip presence validation in Vercel environment to prevent startup failures
        if is_vercel:
            is_valid = len(issues) == 0
            return is_valid, issues

        critical_vars = {
            "SUPABASE_URL": cls.SUPABASE_URL,
            "SUPABASE_KEY": cls.SUPABASE_KEY,
            "OPENROUTER_API_KEY": cls.OPENROUTER_API_KEY,
        }

        issues.extend([name for name, value in critical_vars.items() if not value])

        is_valid = len(issues) == 0

        return is_valid, issues
