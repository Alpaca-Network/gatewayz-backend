import os

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

    # DeepInfra Configuration (for direct API access)
    DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY")
    XAI_API_KEY = os.environ.get("XAI_API_KEY")
    NOVITA_API_KEY = os.environ.get("NOVITA_API_KEY")
    NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY")
    CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY")
    HUG_API_KEY = os.environ.get("HUG_API_KEY")

    # Featherless.ai Configuration
    FEATHERLESS_API_KEY = os.environ.get("FEATHERLESS_API_KEY")

    # Anthropic / Autonomous Monitoring
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

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

    # Anannas Configuration
    ANANNAS_API_KEY = os.environ.get("ANANNAS_API_KEY")

    # Alpaca Network Configuration
    ALPACA_NETWORK_API_KEY = os.environ.get("ALPACA_NETWORK_API_KEY")

    # Alibaba Cloud Configuration
    ALIBABA_CLOUD_API_KEY = os.environ.get("ALIBABA_CLOUD_API_KEY")

    # Clarifai Configuration
    CLARIFAI_API_KEY = os.environ.get("CLARIFAI_API_KEY")
    CLARIFAI_USER_ID = os.environ.get("CLARIFAI_USER_ID")
    CLARIFAI_APP_ID = os.environ.get("CLARIFAI_APP_ID")

    # Google Vertex AI Configuration (for image generation & generative APIs)
    GOOGLE_PROJECT_ID = os.environ.get("GOOGLE_PROJECT_ID", "gatewayz-468519")
    GOOGLE_VERTEX_LOCATION = os.environ.get("GOOGLE_VERTEX_LOCATION", "us-central1")
    GOOGLE_VERTEX_ENDPOINT_ID = os.environ.get("GOOGLE_VERTEX_ENDPOINT_ID", "6072619212881264640")
    GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    GOOGLE_VERTEX_TRANSPORT = os.environ.get("GOOGLE_VERTEX_TRANSPORT", "rest").lower()
    GOOGLE_VERTEX_TIMEOUT = float(os.environ.get("GOOGLE_VERTEX_TIMEOUT", "60"))

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
    TEMPO_ENABLED = os.environ.get("TEMPO_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "gatewayz-api")
    TEMPO_OTLP_HTTP_ENDPOINT = os.environ.get(
        "TEMPO_OTLP_HTTP_ENDPOINT",
        "http://tempo:4318",
    )
    TEMPO_OTLP_GRPC_ENDPOINT = os.environ.get(
        "TEMPO_OTLP_GRPC_ENDPOINT",
        "localhost:4317",
    )

    # Grafana Loki Configuration
    LOKI_ENABLED = os.environ.get("LOKI_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    LOKI_PUSH_URL = _default_loki_push_url
    LOKI_QUERY_URL = _default_loki_query_url

    @classmethod
    def validate(cls):
        """Validate that all required environment variables are set"""
        # Skip validation in Vercel environment to prevent startup failures
        if os.environ.get("VERCEL"):
            return True

        missing_vars = []

        if not cls.SUPABASE_URL:
            missing_vars.append("SUPABASE_URL")
        if not cls.SUPABASE_KEY:
            missing_vars.append("SUPABASE_KEY")
        if not cls.OPENROUTER_API_KEY:
            missing_vars.append("OPENROUTER_API_KEY")

        if missing_vars:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing_vars)}\n"
                "Please create a .env file with the following variables:\n"
                "SUPABASE_URL=your_supabase_project_url\n"
                "SUPABASE_KEY=your_supabase_anon_key\n"
                "OPENROUTER_API_KEY=your_openrouter_api_key\n"
                "OPENROUTER_SITE_URL=your_site_url (optional)\n"
                "OPENROUTER_SITE_NAME=your_site_name (optional)"
            )

        return True

    @classmethod
    def get_supabase_config(cls):
        """Get Supabase configuration as a tuple"""
        return cls.SUPABASE_URL, cls.SUPABASE_KEY

    @classmethod
    def validate_critical_env_vars(cls) -> tuple[bool, list[str]]:
        """
        Validate that all critical environment variables are set.

        Returns:
            tuple: (is_valid, missing_vars)
                - is_valid: bool indicating if all critical vars are present
                - missing_vars: list of missing variable names
        """
        # Skip validation in Vercel environment to prevent startup failures
        if os.environ.get("VERCEL"):
            return True, []

        critical_vars = {
            "SUPABASE_URL": cls.SUPABASE_URL,
            "SUPABASE_KEY": cls.SUPABASE_KEY,
            "OPENROUTER_API_KEY": cls.OPENROUTER_API_KEY,
        }

        missing = [name for name, value in critical_vars.items() if not value]
        is_valid = len(missing) == 0

        return is_valid, missing
