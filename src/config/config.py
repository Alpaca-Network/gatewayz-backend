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
        return Path("/tmp/gatewayz_data")  # nosec B108 — serverless read-only fs fallback
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
        fallback = Path("/tmp") / path.name  # nosec B108 — directory creation fallback
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

    # Abuse / Cost Controls
    # Anonymous (unauthenticated) inference is off by default. Set to "true" to allow.
    ANONYMOUS_ENABLED = os.environ.get("ANONYMOUS_ENABLED", "false").lower() in {"1", "true", "yes"}
    # Phase 3 credit ledger (Gatewayz One §6.D), SHADOW dual-write.
    # When true, a settled double-entry is recorded in public.credit_ledger alongside
    # the authoritative deduction (non-blocking, off-thread, never raises) so the
    # ledger can be reconciled against live billing before any cutover. The shadow
    # write only adds records — it never touches the authoritative balances — so it
    # is safe to run in production. Requires the credit_ledger migration (applied).
    #
    # Default: ON in production (the deploy env where reconciliation data must
    # accrue), OFF everywhere else (tests/dev unaffected). An explicit
    # CREDIT_LEDGER_SHADOW_ENABLED env var overrides the per-env default either way.
    CREDIT_LEDGER_SHADOW_ENABLED = os.environ.get(
        "CREDIT_LEDGER_SHADOW_ENABLED", "true" if IS_PRODUCTION else "false"
    ).lower() in {"1", "true", "yes"}
    # Scheduled credit-ledger reconciliation (shadow vs live). Defaults to follow the
    # shadow flag — there is nothing to reconcile until shadow is accruing. Read-only:
    # it fetches the recent window, compares, and logs (drift logs at ERROR so it is
    # alertable). It never mutates billing.
    ENABLE_LEDGER_RECONCILIATION = os.environ.get(
        "ENABLE_LEDGER_RECONCILIATION", str(CREDIT_LEDGER_SHADOW_ENABLED)
    ).lower() in {"1", "true", "yes"}
    LEDGER_RECONCILIATION_INTERVAL_MINUTES = int(
        os.environ.get("LEDGER_RECONCILIATION_INTERVAL_MINUTES", "360")
    )
    LEDGER_RECONCILIATION_WINDOW_HOURS = int(
        os.environ.get("LEDGER_RECONCILIATION_WINDOW_HOURS", "24")
    )
    # Reject inference requests for models without a row in model_pricing.
    REQUIRE_MODEL_PRICING = os.environ.get("REQUIRE_MODEL_PRICING", "true").lower() in {
        "1",
        "true",
        "yes",
    }

    # Gatewayz One Phase 2 — smart (cost-first) router. When true, the live provider
    # failover chain is REORDERED by the policy-based smart router using the
    # model_provider_offers projection so the CHEAPEST healthy provider for a model
    # leads the chain — the gateway keeps the markup + provider-cost spread on every
    # request. Health/circuit-breaker routing still gets the final say and bumps an
    # unhealthy cost-winner off the front. No-ops (exact passthrough) when the offers
    # table has no rows for the model. Enabled by default; set SMART_ROUTER_ENABLED=
    # false as a kill-switch to restore the legacy static-priority chain.
    SMART_ROUTER_ENABLED = os.environ.get("SMART_ROUTER_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    # Default routing policy when no per-key routing_policies row applies. "cost"
    # maximizes the captured spread (cheapest provider for the same model wins);
    # "balanced" / "latency" / "quality" trade margin for latency/quality.
    SMART_ROUTER_POLICY = os.environ.get("SMART_ROUTER_POLICY", "cost").strip().lower()

    # Gatewayz One Phase 4 — context assembly. When true, conversation messages are
    # reassembled within a per-request token budget (system + memory + rolling
    # summary + most-recent turns, oldest-first dropping) before the upstream call.
    # Off by default; exact passthrough when disabled.
    CONTEXT_ASSEMBLY_ENABLED = os.environ.get("CONTEXT_ASSEMBLY_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    # Fraction of a model's context window reserved for the assembled prompt
    # (the rest is left for the completion). Only used when CONTEXT_ASSEMBLY_ENABLED.
    CONTEXT_ASSEMBLY_BUDGET_RATIO = float(os.environ.get("CONTEXT_ASSEMBLY_BUDGET_RATIO", "0.7"))
    # Phase 4 — heuristic user-memory capture. When on, durable self-stated facts
    # ("my name is…", "I prefer…", "remember that…") are extracted from a user's
    # messages and saved to user_memory (post-response background task) so the
    # context assembler can recall them. High-precision + capped; off by default.
    MEMORY_CAPTURE_ENABLED = os.environ.get("MEMORY_CAPTURE_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    MEMORY_MAX_PER_USER = int(os.environ.get("MEMORY_MAX_PER_USER", "100"))

    # Assumed budget when a model's context length is unknown. Deliberately large
    # so an unknown window does NOT cause aggressive truncation — when we can't tell
    # a model's real window, we pass the turns through (no worse than today) rather
    # than risk dropping context that would have fit.
    CONTEXT_ASSEMBLY_DEFAULT_BUDGET = int(
        os.environ.get("CONTEXT_ASSEMBLY_DEFAULT_BUDGET", "1000000")
    )

    # Gatewayz One Phase 5 — multi-region (rollout phase 1: inventory + wire, no
    # traffic change). The region_router selection core is fed from these. Until
    # MULTI_REGION_ENABLED is true the inventory is just this single region, so all
    # traffic is served locally and selection is a no-op — this only exposes the
    # serving region (X-Gatewayz-Region header / health) for observability.
    # The name of the region THIS instance runs as (Railway injects RAILWAY_REPLICA_REGION).
    GATEWAY_REGION = (
        os.environ.get("GATEWAY_REGION") or os.environ.get("RAILWAY_REPLICA_REGION") or "primary"
    )
    # Region inventory: comma-separated "name" or "name:latency_ms" (e.g.
    # "us-east:10,eu-west:80"). Empty → single region (GATEWAY_REGION). Only consulted
    # when MULTI_REGION_ENABLED is true.
    GATEWAY_REGIONS = os.environ.get("GATEWAY_REGIONS", "").strip()
    # The home region for a user's billing-affecting writes (Phase 5 rollout step 3
    # pins these to one region until the ledger is the billing source of truth).
    GATEWAY_HOME_REGION = os.environ.get("GATEWAY_HOME_REGION", "").strip() or GATEWAY_REGION
    # Master switch for multi-region behavior. Off by default (single region).
    MULTI_REGION_ENABLED = os.environ.get("MULTI_REGION_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    # Subscription statuses that may NOT call the API (comma-separated).
    # Includes both American (Stripe) and British spellings of cancel*, plus all
    # Stripe failure states: past_due (charge failed), unpaid (multiple failures),
    # incomplete_expired (initial payment never confirmed).
    BLOCKED_SUBSCRIPTION_STATUSES = {
        s.strip().lower()
        for s in os.environ.get(
            "BLOCKED_SUBSCRIPTION_STATUSES",
            "expired,bot,canceled,cancelled,suspended,past_due,unpaid,incomplete_expired",
        ).split(",")
        if s.strip()
    }
    # Subset of blocked statuses that block regardless of balance (abuse states).
    # For the remaining blocked statuses (payment lapses like canceled/past_due),
    # users who still hold prepaid purchased credits may keep spending them —
    # purchased credits are retained on cancellation and were already paid for.
    HARD_BLOCKED_SUBSCRIPTION_STATUSES = {
        s.strip().lower()
        for s in os.environ.get(
            "HARD_BLOCKED_SUBSCRIPTION_STATUSES",
            "bot,suspended",
        ).split(",")
        if s.strip()
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
    CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY")
    HUG_API_KEY = os.environ.get("HUG_API_KEY")

    # Featherless.ai Configuration
    FEATHERLESS_API_KEY = os.environ.get("FEATHERLESS_API_KEY")

    # OpenAI Direct API Configuration
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("PROVIDER_OPENAI_API_KEY")

    # Anthropic Direct API / Autonomous Monitoring
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "PROVIDER_ANTHROPIC_API_KEY"
    )
    # Model to use for Anthropic API calls (bug fix generator, autonomous monitoring)
    # Valid models: claude-3-5-sonnet-20241022, claude-3-opus-20240229, claude-3-sonnet-20240229, claude-3-haiku-20240307
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

    # Fireworks.ai Configuration
    FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY")

    # Together.ai Configuration
    TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY")

    # Groq Configuration
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

    # Fal.ai Configuration
    FAL_API_KEY = os.environ.get("FAL_API_KEY")

    # Resemble AI / Chatterbox TTS Configuration
    RESEMBLE_API_KEY = os.environ.get("RESEMBLE_API_KEY")

    # Tavily Web Search Configuration
    TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

    # Alibaba Cloud Configuration
    ALIBABA_CLOUD_API_KEY = os.environ.get("ALIBABA_CLOUD_API_KEY")
    ALIBABA_CLOUD_API_KEY_INTERNATIONAL = os.environ.get("ALIBABA_CLOUD_API_KEY_INTERNATIONAL")
    ALIBABA_CLOUD_API_KEY_CHINA = os.environ.get("ALIBABA_CLOUD_API_KEY_CHINA")
    ALIBABA_CLOUD_REGION = os.environ.get(
        "ALIBABA_CLOUD_REGION", "international"
    )  # 'international' or 'china'

    # Nosana GPU Computing Network Configuration
    NOSANA_API_KEY = os.environ.get("NOSANA_API_KEY")
    NOSANA_BASE_URL = os.environ.get("NOSANA_BASE_URL", "https://dashboard.k8s.prd.nos.ci/api")

    # Z.AI Configuration (Zhipu AI - GLM models)
    ZAI_API_KEY = os.environ.get("ZAI_API_KEY")

    # Soundsgood Configuration (GLM-4.5-Air distilled model)
    SOUNDSGOOD_API_KEY = os.environ.get("SOUNDSGOOD_API_KEY")

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

    # Admin Credit Grant Safety Controls
    # Maximum single credit grant amount in dollars (default $1000)
    ADMIN_MAX_CREDIT_GRANT: float = float(os.environ.get("ADMIN_MAX_CREDIT_GRANT", "1000"))
    # Maximum total credits an admin can grant in a 24-hour rolling window (default $5000)
    ADMIN_DAILY_GRANT_LIMIT: float = float(os.environ.get("ADMIN_DAILY_GRANT_LIMIT", "5000"))

    # GZip Compression Configuration
    # Minimum response size (bytes) before GZip compression is applied.
    # 1 KB (1024 bytes) is a reasonable floor: below this the gzip header overhead
    # (~20 bytes) and CPU cost outweigh the savings. Streaming responses (SSE, ndjson)
    # are always excluded regardless of this setting.
    # Override with GZIP_MINIMUM_SIZE env var.
    GZIP_MINIMUM_SIZE: int = int(os.environ.get("GZIP_MINIMUM_SIZE", "1024"))

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
    # Hard-defaulted off as part of cost reduction; consumers no-op when false.
    TEMPO_ENABLED = os.environ.get("TEMPO_ENABLED", "false").lower() in {
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
    # When FastAPIInstrumentor is active it already creates a server span per request
    # (including HTTP method, route, and status code). Set this to true to prevent
    # TraceContextMiddleware from emitting duplicate request/response log lines.
    # Header injection (x-trace-id, x-span-id) is always performed regardless of this flag.
    OTEL_AUTO_INSTRUMENTED = os.environ.get("OTEL_AUTO_INSTRUMENTED", "false").lower() in {
        "1",
        "true",
        "yes",
    }

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
    # Pricing is now synced via model sync (model_catalog_sync.py)

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
        s.strip()
        for s in os.environ.get("MODEL_SYNC_SKIP_PROVIDERS", "featherless").split(",")
        if s.strip()
    }

    # Catalog quality gate — drop obvious junk (quant/merge/RP spam) at ingestion.
    # Provider-agnostic; conservative high-precision rules (see model_quality_gate.py).
    MODEL_QUALITY_GATE_ENABLED: bool = os.environ.get(
        "MODEL_QUALITY_GATE_ENABLED", "true"
    ).lower() in {"1", "true", "yes"}

    # Lightweight price-only refresh (src/services/price_refresh.py).
    # Independent of ENABLE_SCHEDULED_MODEL_SYNC: this job only keeps PRICES
    # current (it never inserts/deactivates models, touches non-pricing columns,
    # or rebuilds the catalog cache).
    # DEFAULT OFF (deliberate): the full sync currently never persists
    # metadata.pricing_raw (transform_normalized_model_to_db_schema computes
    # pricing then discards it), so the FIRST run would not just refresh prices
    # but POPULATE them — shifting the billing source from curated
    # manual_pricing.json to raw fetched provider prices. Run
    # refresh_all_prices(dry_run=True) to review the impact, then enable in prod
    # via ENABLE_PRICE_REFRESH=true.
    ENABLE_PRICE_REFRESH: bool = os.environ.get("ENABLE_PRICE_REFRESH", "false").lower() in {
        "1",
        "true",
        "yes",
    }

    # How often to run the price-only refresh (in minutes). Default 360 (6h) —
    # prices change slowly, and this keeps provider API load minimal.
    PRICE_REFRESH_INTERVAL_MINUTES: int = int(
        os.environ.get("PRICE_REFRESH_INTERVAL_MINUTES", "360")
    )

    # Enabled providers — only these providers will be loaded, routed to,
    # shown in the catalog, and synced.  Comma-separated slugs using the
    # hyphenated gateway names (e.g. "openrouter,openai,anthropic").
    # Empty string or unset means ALL providers are enabled.
    _raw_enabled = os.environ.get("ENABLED_PROVIDERS", "openrouter")
    ENABLED_PROVIDERS: frozenset[str] | None = (
        frozenset(s.strip() for s in _raw_enabled.split(",") if s.strip())
        if _raw_enabled.strip()
        else None  # None = all providers enabled
    )

    # Health-gated catalog. When true, models whose models.health_status == 'down'
    # are hidden from the served catalog. This is INERT until an active health sweep
    # (see src/services/monitoring/model_health_sweep.py) actually marks a model
    # 'down' — which only happens for models that CONSISTENTLY hard-fail
    # (dead / 404 / 5xx). Models that merely rate-limit (429), time out, or have
    # auth problems are NEVER hidden. Defaults ON but inert until fresh sweep data
    # exists; set HEALTH_GATING_ENABLED=false to disable the filter entirely.
    HEALTH_GATING_ENABLED: bool = os.getenv("HEALTH_GATING_ENABLED", "true").lower() == "true"

    # Pricing markup — multiplier applied on top of upstream provider cost.
    # 1.0 = pass-through (no profit), 1.25 = 25% margin, 1.30 = 30% margin.
    # This is the primary revenue lever for inference requests.
    PRICING_MARKUP: float = float(os.environ.get("PRICING_MARKUP", "1.25"))

    # Credit top-up fee — fraction withheld from a credit purchase as revenue
    # (the OpenRouter-style monetization model). 0.0 = disabled (default, no
    # behaviour change); 0.05 = keep 5% of each top-up. To run the pure
    # "5%-on-credits" model, set CREDIT_TOPUP_FEE_RATE=0.05 AND PRICING_MARKUP=1.0
    # (near-cost inference passthrough + fee on purchases).
    CREDIT_TOPUP_FEE_RATE: float = float(os.environ.get("CREDIT_TOPUP_FEE_RATE", "0.0"))

    # BYOK routing fee — fraction charged as revenue when a request is served
    # using a customer's own provider key (bring-your-own-key). Inference cost
    # is paid on the customer's upstream account, so we bill only this routing
    # fee (fraction of the computed upstream cost) instead of debiting credits.
    # 0.0 = disabled (default); 0.05 = 5% routing fee. See Phase 5 in
    # docs/BUSINESS_PIVOT_DIRECT_SUPPLY.md.
    BYOK_ROUTING_FEE_RATE: float = float(os.environ.get("BYOK_ROUTING_FEE_RATE", "0.0"))

    # Master switch for bring-your-own-key routing. Off by default: when disabled
    # the inference path does no per-request BYOK lookup, so there is zero added
    # latency. Enable to let users serve requests on their own stored provider
    # keys (billed at BYOK_ROUTING_FEE_RATE instead of full credit cost).
    BYOK_ENABLED: bool = os.environ.get("BYOK_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    # Pricing Sync Configuration - DEPRECATED 2026-02 (Phase 3, Issue #1063)
    # Pricing is now synced via model sync (model_catalog_sync.py)
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
