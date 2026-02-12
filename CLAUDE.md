# Gatewayz Universal Inference API - Codebase Context

## Overview

**Project**: Gatewayz Universal Inference API (v2.0.3)

**Purpose**: A production-ready, enterprise-grade FastAPI application that provides a unified API gateway for accessing 100+ AI models from 30+ different providers (OpenRouter, Portkey, Featherless, Chutes, DeepInfra, Fireworks, Together, HuggingFace, Google Vertex AI, Groq, Cerebras, Cloudflare Workers AI, and more).

**Key Features**:
- OpenAI-compatible API endpoints (drop-in replacement)
- Anthropic Messages API compatibility (Claude models)
- Multi-provider model aggregation and routing
- Credit-based billing system with real-time tracking
- Enterprise security (encrypted API keys, IP allowlists, audit logging)
- Advanced features: chat history, image generation, free trials, subscriptions, referrals
- Comprehensive analytics and monitoring (Prometheus, Grafana, Sentry, Arize)
- OpenTelemetry-based distributed tracing
- Rate limiting and request prioritization
- Intelligent health monitoring and provider failover
- Multiple deployment options (Vercel, Railway, Docker)

---

## Codebase Structure

### Directory Layout

```
/root/repo/
├── src/                           # Main application source (85,080 lines of Python)
│   ├── main.py                   # FastAPI app factory and route initialization
│   ├── config/                   # Configuration management (8 files)
│   │   ├── config.py            # Environment-based configuration
│   │   ├── db_config.py         # Database configuration
│   │   ├── redis_config.py      # Redis caching configuration
│   │   ├── supabase_config.py   # Supabase client initialization
│   │   ├── arize_config.py      # Arize AI observability configuration
│   │   ├── logging_config.py    # Logging configuration (Loki integration)
│   │   └── opentelemetry_config.py # OpenTelemetry tracing configuration
│   │
│   ├── middleware/               # Middleware Layer (6 modules) - NEW
│   │   ├── auto_sentry_middleware.py   # Sentry error tracking middleware
│   │   ├── observability_middleware.py # Observability/tracing middleware
│   │   ├── request_timeout_middleware.py # Request timeout handling
│   │   ├── security_middleware.py      # Security & rate limiting (IP-based + velocity mode)
│   │   ├── selective_gzip_middleware.py # Conditional compression
│   │   └── trace_context_middleware.py  # Distributed tracing context
│   │
│   ├── db/                       # Database Access Layer (24 modules)
│   │   ├── users.py             # User CRUD operations
│   │   ├── api_keys.py          # API key management with encryption
│   │   ├── chat_history.py      # Chat session management
│   │   ├── payments.py          # Payment/transaction records
│   │   ├── plans.py             # Subscription plan management
│   │   ├── trials.py            # Free trial tracking
│   │   ├── coupons.py           # Coupon/discount codes
│   │   ├── referral.py          # Referral system tracking
│   │   ├── activity.py          # User activity tracking
│   │   ├── rate_limits.py       # Rate limit configurations
│   │   ├── roles.py             # Role-based access control
│   │   ├── ranking.py           # Model ranking data
│   │   ├── credit_transactions.py # Credit transaction history
│   │   ├── gateway_analytics.py # Gateway usage analytics
│   │   ├── ping.py              # Ping statistics
│   │   ├── failover_db.py       # Failover database operations
│   │   ├── feedback.py          # User feedback storage
│   │   ├── model_health.py      # Model health metrics
│   │   ├── models_catalog_db.py # Model catalog database
│   │   ├── postgrest_schema.py  # PostgREST schema definitions
│   │   ├── providers_db.py      # Provider database operations
│   │   ├── subscription_products.py # Subscription product management
│   │   └── webhook_events.py    # Webhook event logging
│   │
│   ├── routes/                   # API Endpoint Handlers (43 modules)
│   │   ├── chat.py              # Chat completions (OpenAI-compatible)
│   │   ├── messages.py          # Anthropic Messages API (Claude-compatible)
│   │   ├── images.py            # Image generation endpoints
│   │   ├── catalog.py           # Model catalog & discovery
│   │   ├── health.py            # Health check endpoints
│   │   ├── ping.py              # Ping/statistics service
│   │   ├── auth.py              # Authentication & login
│   │   ├── users.py             # User management
│   │   ├── api_keys.py          # API key CRUD operations
│   │   ├── admin.py             # Admin operations & monitoring
│   │   ├── payments.py          # Payment processing (Stripe webhooks)
│   │   ├── plans.py             # Subscription plans
│   │   ├── chat_history.py      # Chat history management
│   │   ├── coupons.py           # Coupon management
│   │   ├── notifications.py     # Notification endpoints
│   │   ├── audit.py             # Audit log queries
│   │   ├── rate_limits.py       # Rate limit configuration
│   │   ├── referral.py          # Referral system endpoints
│   │   ├── roles.py             # Role management
│   │   ├── activity.py          # Activity tracking
│   │   ├── analytics.py         # Analytics events
│   │   ├── availability.py      # Model availability checks
│   │   ├── system.py            # System health & cache management
│   │   ├── optimization_monitor.py # Connection pool & performance stats
│   │   ├── root.py              # Root/welcome endpoint
│   │   ├── transaction_analytics.py # Transaction analytics
│   │   ├── ranking.py           # Model ranking endpoints
│   │   ├── ai_sdk.py            # AI SDK endpoints
│   │   ├── alibaba_debug.py     # Alibaba debugging endpoints
│   │   ├── credits.py           # Credit management endpoints
│   │   ├── error_monitor.py     # Error monitoring endpoints
│   │   ├── grafana_metrics.py   # Grafana metrics integration
│   │   ├── instrumentation.py   # Instrumentation/tracing endpoints
│   │   ├── metrics.py           # Prometheus metrics endpoints
│   │   ├── model_health.py      # Model health status endpoints
│   │   ├── model_sync.py        # Model synchronization endpoints
│   │   ├── models_catalog_management.py # Catalog management endpoints
│   │   ├── monitoring.py        # Monitoring endpoints
│   │   ├── pricing_audit.py     # Pricing audit endpoints
│   │   ├── pricing_sync.py      # Pricing synchronization endpoints
│   │   ├── providers_management.py # Provider management endpoints
│   │   └── status_page.py       # Status page endpoint
│   │
│   ├── services/                 # Business Logic Layer (95 modules)
│   │   # Provider Clients (30 modules)
│   │   ├── openrouter_client.py  # OpenRouter API integration
│   │   ├── featherless_client.py # Featherless provider
│   │   ├── chutes_client.py      # Chutes provider
│   │   ├── deepinfra_client.py   # DeepInfra provider
│   │   ├── fireworks_client.py   # Fireworks AI provider
│   │   ├── together_client.py    # Together AI provider
│   │   ├── huggingface_client.py # HuggingFace inference
│   │   ├── xai_client.py         # XAI provider
│   │   ├── aimo_client.py        # AIMO provider
│   │   ├── near_client.py        # Near AI provider
│   │   ├── fal_image_client.py   # Fal.ai image generation
│   │   ├── anannas_client.py     # Anannas provider
│   │   ├── google_vertex_client.py # Google Vertex AI
│   │   ├── modelz_client.py      # Modelz provider
│   │   ├── aihubmix_client.py    # AiHubMix provider
│   │   ├── vercel_ai_gateway_client.py # Vercel AI Gateway
│   │   ├── ai_sdk_client.py      # AI SDK integration
│   │   ├── akash_client.py       # Akash provider
│   │   ├── alibaba_cloud_client.py # Alibaba Cloud provider
│   │   ├── alpaca_network_client.py # Alpaca Network provider
│   │   ├── cerebras_client.py    # Cerebras provider
│   │   ├── clarifai_client.py    # Clarifai provider
│   │   ├── cloudflare_workers_ai_client.py # Cloudflare Workers AI
│   │   ├── groq_client.py        # Groq provider
│   │   ├── helicone_client.py    # Helicone provider
│   │   ├── morpheus_client.py    # Morpheus provider
│   │   ├── nebius_client.py      # Nebius provider
│   │   ├── novita_client.py      # Novita provider
│   │   └── onerouter_client.py   # OneRouter provider
│   │   │
│   │   # Core Services (12 modules)
│   │   ├── models.py             # Model catalog management
│   │   ├── providers.py          # Provider registry & caching
│   │   ├── model_transformations.py # Model ID transformation/routing
│   │   ├── model_availability.py # Model availability checking
│   │   ├── model_health_monitor.py # Health monitoring
│   │   ├── huggingface_models.py # HuggingFace model catalog
│   │   ├── huggingface_hub_service.py # HuggingFace Hub integration
│   │   ├── image_generation_client.py # Image generation router
│   │   ├── canonical_registry.py # Canonical model registry
│   │   ├── multi_provider_registry.py # Multi-provider registry
│   │   ├── provider_selector.py  # Provider selection logic
│   │   └── model_catalog_sync.py # Model catalog synchronization
│   │   │
│   │   # Health Monitoring Services (7 modules)
│   │   ├── autonomous_monitor.py # Autonomous health monitoring
│   │   ├── gateway_health_service.py # Gateway health service
│   │   ├── health_alerting.py    # Health alerting system
│   │   ├── intelligent_health_monitor.py # Intelligent health monitoring
│   │   ├── passive_health_monitor.py # Passive health monitoring
│   │   ├── simple_health_cache.py # Simple health caching
│   │   └── failover_service.py   # Provider failover service
│   │   │
│   │   # Caching Services (6 modules)
│   │   ├── auth_cache.py         # Authentication caching
│   │   ├── db_cache.py           # Database caching
│   │   ├── model_catalog_cache.py # Model catalog caching
│   │   ├── response_cache.py     # Response caching
│   │   ├── user_lookup_cache.py  # User lookup caching
│   │   └── connection_pool_monitor.py # Connection pool monitoring
│   │   │
│   │   # Pricing Services (5 modules)
│   │   ├── pricing.py            # Pricing calculations
│   │   ├── pricing_lookup.py     # Pricing data lookup
│   │   ├── pricing_audit_service.py # Pricing audit service
│   │   ├── pricing_provider_auditor.py # Provider pricing auditor
│   │   └── pricing_sync_service.py # Pricing synchronization
│   │   │
│   │   # Observability Services (12 modules)
│   │   ├── error_monitor.py      # Error monitoring service
│   │   ├── grafana_metrics_service.py # Grafana metrics service
│   │   ├── metrics_aggregator.py # Metrics aggregation
│   │   ├── metrics_instrumentation.py # Metrics instrumentation
│   │   ├── metrics_parser.py     # Metrics parsing
│   │   ├── prometheus_exporter.py # Prometheus exporter
│   │   ├── prometheus_metrics.py # Prometheus metrics
│   │   ├── prometheus_pb2.py     # Prometheus protobuf
│   │   ├── prometheus_remote_write.py # Prometheus remote write
│   │   ├── redis_metrics.py      # Redis metrics
│   │   └── tempo_otlp.py         # Tempo OpenTelemetry integration
│   │   │
│   │   # Feature Services (10 modules)
│   │   ├── payments.py           # Payment processing service
│   │   ├── trial_service.py      # Trial management
│   │   ├── trial_validation.py   # Trial validation logic
│   │   ├── referral.py           # Referral tracking
│   │   ├── rate_limiting.py      # Redis-based rate limiting
│   │   ├── rate_limiting_fallback.py # Fallback rate limiting
│   │   ├── roles.py              # Role management service
│   │   ├── ping.py               # Ping statistics service
│   │   ├── request_prioritization.py # Request prioritization
│   │   └── provider_failover.py  # Provider failover logic
│   │   │
│   │   # Utility Services (13 modules)
│   │   ├── notification.py       # Email notifications
│   │   ├── professional_email_templates.py # Email templates
│   │   ├── analytics.py          # Analytics service
│   │   ├── statsig_service.py    # Statsig feature flags
│   │   ├── posthog_service.py    # PostHog analytics
│   │   ├── startup.py            # Application startup/lifespan
│   │   ├── connection_pool.py    # Connection pooling
│   │   ├── anthropic_transformer.py # Message format transformation
│   │   ├── background_tasks.py   # Background task management
│   │   ├── bug_fix_generator.py  # Bug fix generation utility
│   │   ├── query_timeout.py      # Query timeout handling
│   │   ├── stream_normalizer.py  # Stream normalization
│   │   ├── google_models_config.py # Google models configuration
│   │   └── google_oauth2_jwt.py  # Google OAuth2 JWT handling
│   │
│   ├── schemas/                  # Pydantic Data Models (15 modules)
│   │   ├── chat.py              # Chat request/response schemas
│   │   ├── auth.py              # Authentication schemas
│   │   ├── api_keys.py          # API key schemas
│   │   ├── users.py             # User schemas
│   │   ├── payments.py          # Payment schemas
│   │   ├── admin.py             # Admin operation schemas
│   │   ├── coupons.py           # Coupon schemas
│   │   ├── plans.py             # Plan schemas
│   │   ├── trials.py            # Trial schemas
│   │   ├── notification.py      # Notification schemas
│   │   ├── common.py            # Common/shared schemas
│   │   ├── proxy.py             # Proxy request schemas
│   │   ├── models_catalog.py    # Model catalog schemas
│   │   └── providers.py         # Provider schemas
│   │
│   ├── security/                # Security & Auth Layer (3 modules)
│   │   ├── security.py          # Encryption/hashing utilities (Fernet, HMAC)
│   │   └── deps.py              # Security dependencies (get_api_key, etc)
│   │
│   ├── models/                  # Model Definition Files (3 modules)
│   │   ├── health_models.py     # Health check models
│   │   └── image_models.py      # Image generation models
│   │
│   ├── utils/                   # Utility Modules (15 modules)
│   │   ├── validators.py        # Input validation
│   │   ├── security_validators.py # Security-specific validators
│   │   ├── auto_sentry.py       # Sentry integration utilities
│   │   ├── braintrust_tracing.py # Braintrust tracing
│   │   ├── crypto.py            # Cryptographic utilities
│   │   ├── dependency_utils.py  # FastAPI dependency utilities
│   │   ├── performance_tracker.py # Performance tracking
│   │   ├── rate_limit_headers.py # Rate limit header utilities
│   │   ├── release_tracking.py  # Release tracking
│   │   ├── reset_welcome_emails.py # Email reset utility
│   │   ├── retry.py             # Retry logic utilities
│   │   ├── sentry_context.py    # Sentry context management
│   │   ├── sentry_insights.py   # Sentry insights
│   │   ├── token_estimator.py   # Token estimation utilities
│   │   └── trial_utils.py       # Trial utility functions
│   │
│   ├── constants.py             # Application constants
│   ├── models.py                # Legacy/global models
│   ├── cache.py                 # Caching utilities
│   ├── db_security.py           # Database security utilities
│   ├── redis_config.py          # Root-level Redis config
│   ├── backfill_legacy_keys.py  # Legacy key backfill script
│   └── enhanced_notification_service.py # Enhanced notification service
│
├── tests/                        # Test Suite (228 test files)
│   ├── conftest.py             # Pytest configuration & fixtures
│   ├── factories.py            # Test data factories
│   ├── config/                 # Configuration tests
│   ├── db/                     # Database tests
│   ├── e2e/                    # End-to-end tests
│   ├── health/                 # Health check tests
│   ├── integration/            # Integration tests
│   ├── middleware/             # Middleware tests
│   ├── routes/                 # Route tests
│   ├── schemas/                # Schema tests
│   ├── security/               # Security tests
│   ├── services/               # Service tests
│   ├── smoke/                  # Smoke tests
│   └── utils/                  # Utility tests
│
├── docs/                        # Documentation (121 files)
│   ├── architecture.md         # System architecture
│   ├── api.md                  # API reference
│   ├── setup.md                # Setup instructions
│   ├── DEPLOYMENT.md           # Deployment guide
│   ├── STRIPE.md               # Stripe integration
│   ├── REFERRAL_SYSTEM.md      # Referral system
│   ├── ACTIVITY_LOGGING.md     # Activity logging
│   └── integration/            # Provider integration guides
│
├── supabase/                    # Database Migrations
│   ├── config.toml             # Supabase configuration
│   └── migrations/             # SQL migrations (36 files)
│
├── scripts/                     # Utility Scripts
│   ├── checks/                 # Pre-deployment checks
│   ├── database/               # Database utilities
│   ├── integration-tests/      # Test scripts
│   └── utilities/              # Helper scripts
│
├── api/                         # Vercel Serverless Entry Point
│   └── index.py                # Vercel deployment handler
│
├── .github/                     # CI/CD Configuration
│   └── workflows/              # GitHub Actions workflows (9 files)
│
├── pyproject.toml              # Project metadata & tool configuration
├── requirements.txt            # Python dependencies (pinned versions)
├── requirements-dev.txt        # Development dependencies
├── vercel.json                 # Vercel deployment config
├── railway.json                # Railway deployment config
├── railway.toml                # Railway TOML configuration
├── docker-compose.prometheus.yml # Prometheus Docker Compose
├── pytest.ini                  # Pytest configuration
├── README.md                   # Main documentation
└── CLAUDE.md                   # This file - AI context
```

---

## Key Technologies & Dependencies

### Core Framework & Web Server
- **FastAPI 0.104.1** - Modern, fast web framework with async support
- **Uvicorn 0.24.0** - ASGI server for running FastAPI
- **Python 3.10+ to <3.13** - Required Python version
- **Prometheus Client 0.21.0** - Metrics exposure for observability
- **anyio >=3.7.1,<4.0.0** - Async compatibility layer

### Data Validation & Serialization
- **Pydantic 2.12.2** with email validator - Type-safe data validation
- **Python-multipart 0.0.6** - Multipart form data parsing

### Database & Data Storage
- **Supabase 2.12.0** - PostgreSQL with real-time capabilities via PostgREST API
- **Redis 5.0.1** - In-memory cache for rate limiting and response caching
- **psycopg[binary] 3.1.18** - PostgreSQL adapter

### External Service Integrations
- **Stripe 13.0.1** - Payment processing and subscriptions
- **Resend 0.8.0** - Transactional email delivery
- **OpenAI 1.44.0** - OpenAI API client
- **HTTPX 0.27.0** - Async HTTP client
- **Requests 2.31.0** - Synchronous HTTP client
- **Tenacity 8.2.3** - Retry logic for API calls

### Provider SDKs
- **Cerebras Cloud SDK 1.0.0+** - Cerebras inference
- **XAI SDK 0.1.0+** - X.AI provider integration
- **Google Cloud AIplatform 1.38.0+** - Google Vertex AI
- **Google Auth 2.0.0+** - Google authentication
- **Clarifai 10.0.0+** - Clarifai provider SDK
- **HuggingFace Hub 0.23.0+** - HuggingFace model hub integration
- **Novita Client 0.5.0+** - Novita provider SDK
- **PyJWT[crypto] 2.8.0** - JWT token handling

### Security & Cryptography
- **Cryptography 41.0.7** - Fernet (AES-128) encryption and HMAC hashing
- **Python-dotenv 1.0.0** - Environment variable management
- **Email-validator 2.1.0** - Email format validation
- **Sentry SDK[fastapi] 2.0.0+** - Error tracking and monitoring

### Analytics & Monitoring
- **Statsig Python Core 0.10.2** - Feature flags and A/B testing
- **PostHog 6.7.8** - Product analytics
- **Prometheus Client 0.21.0** - Prometheus metrics export
- **Braintrust** - ML/AI evaluation and monitoring
- **Arize OTEL 0.11.0+** - Arize AI observability platform
- **OpenInference Instrumentation OpenAI 0.1.41+** - OpenAI instrumentation

### OpenTelemetry Stack (Distributed Tracing)
- **opentelemetry-api 1.28.0+** - Core tracing API
- **opentelemetry-sdk 1.28.0+** - SDK implementation
- **opentelemetry-instrumentation 0.49b0+** - Base instrumentation
- **opentelemetry-instrumentation-fastapi 0.49b0+** - FastAPI auto-instrumentation
- **opentelemetry-instrumentation-httpx 0.49b0+** - HTTPX auto-instrumentation
- **opentelemetry-instrumentation-requests 0.49b0+** - Requests auto-instrumentation
- **opentelemetry-exporter-otlp 1.28.0+** - OTLP exporter for traces

### Logging & Observability
- **python-json-logger 2.0.7+** - JSON structured logging
- **python-logging-loki 0.3.1+** - Loki log aggregation
- **python-snappy 0.7.0+** - Snappy compression for metrics
- **protobuf 4.24.0+** - Protocol buffers for metrics

### Testing
- **Pytest 7.4.3** - Testing framework
- **Pytest-cov 4.1.0** - Code coverage measurement
- **Pytest-asyncio 0.21.1** - Async test support
- **Pytest-xdist 3.5.0+** - Parallel test execution
- **Pytest-split 0.9.0+** - Test split and distribution
- **Pytest-timeout 2.2.0** - Test timeout handling
- **Pytest-mock 3.12.0** - Mocking utilities
- **Playwright 1.40.0+** - Browser automation for E2E tests
- **Flask-SQLAlchemy** - Database testing utilities

### Code Quality Tools
- **Ruff** - Fast Python linter (configured for 100 char line length, target Python 3.12)
- **Black** - Code formatter (100 char line length, target Python 3.12)
- **isort** - Import organizer (black profile)
- **MyPy** - Type checking (Python 3.12 target)

### Deployment
- **Vercel** - Serverless platform
- **Railway** - Container hosting platform
- **Docker** - Containerization
- **Prometheus/Grafana** - Metrics and dashboards

---

## Architecture Overview

### High-Level Design Pattern

The application follows a **layered, modular architecture** with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────┐
│              External Clients                               │
│       (Web Apps, Mobile Apps, CLI Tools)                    │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTPS/REST API
                         │
┌────────────────────────▼────────────────────────────────────┐
│          FastAPI Application (src/main.py)                   │
│ ┌──────────────────────────────────────────────────────┐   │
│ │  Middleware Layer                                     │   │
│ │  • CORS, Authentication, Rate Limiting               │   │
│ │  • Request logging, GZip compression                 │   │
│ └──────────────────────────────────────────────────────┘   │
│ ┌──────────────────────────────────────────────────────┐   │
│ │  Routes Layer (src/routes/ - 43 modules)             │   │
│ │  Handles HTTP endpoints and request parsing          │   │
│ └──────────────────────────────────────────────────────┘   │
│ ┌──────────────────────────────────────────────────────┐   │
│ │  Services Layer (src/services/ - 95 modules)         │   │
│ │  Business logic, provider routing, pricing, etc      │   │
│ └──────────────────────────────────────────────────────┘   │
│ ┌──────────────────────────────────────────────────────┐   │
│ │  Data Access Layer (src/db/ - 24 modules)            │   │
│ │  Database operations via Supabase PostgREST API      │   │
│ └──────────────────────────────────────────────────────┘   │
└────────────┬──────────────────────────┬──────────────────────┘
             │                          │
    ┌────────▼──────────┐      ┌────────▼────────────────┐
    │  Supabase         │      │ External Providers      │
    │  (PostgreSQL)     │      │ • OpenRouter            │
    │                   │      │ • Portkey               │
    │ Tables:           │      │ • Featherless           │
    │ • users           │      │ • Chutes                │
    │ • api_keys        │      │ • DeepInfra             │
    │ • payments        │      │ • Fireworks             │
    │ • plans           │      │ • Together              │
    │ • chat_history    │      │ • HuggingFace           │
    │ • coupons         │      │ • Google Vertex         │
    │ • (20+ tables)    │      │ • Groq, Cerebras        │
    │                   │      │ • Cloudflare Workers    │
    │                   │      │ • & 20+ more            │
    └───────────────────┘      └─────────────────────────┘

                Also connected:
    ┌──────────────────┐   ┌──────────────────┐
    │    Redis         │   │  Stripe (Pay)    │
    │ (Cache/Rate      │   │  Resend (Email)  │
    │  Limiting)       │   │  Statsig (Flags) │
    └──────────────────┘   └──────────────────┘
```

### Architectural Principles

1. **Modularity**: Strict separation into routes (HTTP), services (logic), and db (data)
2. **Request Flow**:
   - Request enters with API key/authentication
   - Middleware validates and logs request
   - Route handler calls appropriate service
   - Service handles business logic (provider routing, pricing, rate limiting)
   - DB layer executes Supabase queries
   - Response formatted and returned
3. **Provider Abstraction**: Each provider (OpenRouter, Portkey, etc.) has its own client module
4. **Security**: Encrypted API keys (Fernet), HMAC validation, role-based access control
5. **Scalability**: Redis caching, connection pooling, async/await throughout

---

## Main Entry Points

### Local Development
- **File**: `src/main.py`
- **Function**: `create_app()` - Creates and configures FastAPI instance
- **Command**: `python src/main.py` or `uvicorn src.main:app --reload`
- **Port**: 8000 (default)

### Production Vercel
- **File**: `api/index.py`
- **Function**: Serverless function handler

### Docker/Railway
- **Script**: `start.sh`
- **Method**: Launches uvicorn server in container

---

## Critical Modules by Function

### Authentication & Security
- `src/security/security.py` - Encryption/HMAC utilities
- `src/security/deps.py` - FastAPI dependency injection for auth
- `src/db/api_keys.py` - Encrypted API key storage
- `src/routes/auth.py` - Authentication endpoints

### Model Routing & Catalog
- `src/services/models.py` - Model catalog aggregation
- `src/services/model_transformations.py` - Model ID normalization
- `src/services/model_availability.py` - Real-time availability
- `src/routes/catalog.py` - Model discovery endpoints

### Chat & Inference
- `src/routes/chat.py` - OpenAI-compatible chat endpoint
- `src/routes/messages.py` - Anthropic Messages API (Claude)
- `src/services/openrouter_client.py` - Primary provider integration
- `src/services/provider_failover.py` - Failover logic

### Credit Management
- `src/db/credit_transactions.py` - Transaction history
- `src/services/pricing.py` - Credit cost calculations
- `src/services/pricing_lookup.py` - Model-specific pricing
- `src/routes/users.py` - User balance endpoints

### Rate Limiting (Three-Layer Architecture)
- `src/middleware/security_middleware.py` - Layer 1: IP-based + Behavioral Fingerprinting + Velocity Mode
- `src/services/rate_limiting.py` - Layer 2: API key rate limiting (Redis-based)
- `src/services/anonymous_rate_limiter.py` - Layer 3: Anonymous user rate limiting
- `src/services/rate_limiting_fallback.py` - Fallback when Redis unavailable
- `src/db/rate_limits.py` - Rate limit configuration
- `docs/RATE_LIMITING.md` - Comprehensive rate limiting documentation

### Database & Configuration
- `src/config/supabase_config.py` - Database client initialization
- `src/config/config.py` - Environment configuration (30+ vars)
- `src/config/redis_config.py` - Redis client setup
- `supabase/migrations/` - Database schema (36 migration files)

### Health & Monitoring
- `src/routes/health.py` - Health check endpoints
- `src/routes/system.py` - Cache management and system stats
- `src/routes/optimization_monitor.py` - Performance metrics
- `src/routes/audit.py` - Audit log queries
- `src/routes/metrics.py` - Prometheus metrics endpoints
- `src/routes/grafana_metrics.py` - Grafana integration
- `src/routes/monitoring.py` - Monitoring endpoints
- `src/routes/model_health.py` - Model health status
- `src/services/intelligent_health_monitor.py` - Intelligent health monitoring
- `src/services/autonomous_monitor.py` - Autonomous health checks

---

## Database Schema

### Core Tables (20+)
- **users** - User accounts and profiles
- **api_keys** - Encrypted API keys with metadata
- **payments** - Transaction records
- **plans** - Subscription plans
- **chat_history** - Conversation history
- **coupons** - Discount codes
- **referrals** - Referral tracking
- **trials** - Free trial information
- **credit_transactions** - Credit deduction history
- **rate_limits** - Rate limit configurations
- **roles** - Role-based access control
- **activity** - User activity logging
- **ranking** - Model rankings
- **gateway_analytics** - Usage analytics
- **ping** - Ping statistics
- **feedback** - User feedback
- **model_health** - Model health metrics
- **models_catalog** - Model catalog data
- **providers** - Provider configurations
- **subscription_products** - Subscription product data
- **webhook_events** - Webhook event logs
- **failover** - Failover state data

### Database Migrations
Located in `supabase/migrations/` with 36 migration files covering:
- Schema initialization
- Table creation and modifications
- Index optimization
- Permission configurations

---

## Configuration Management

### Environment Variables (30+)
Configured in `src/config/config.py`:
- Database: SUPABASE_URL, SUPABASE_KEY
- Redis: REDIS_URL
- Providers: OPENROUTER_KEY, PORTKEY_KEY, etc.
- Payments: STRIPE_KEY, STRIPE_WEBHOOK_SECRET
- Email: RESEND_API_KEY
- Analytics: STATSIG_SDK_KEY, POSTHOG_KEY
- Security: JWT_SECRET, ENCRYPTION_KEY
- And more...

### Loading
- **Development**: `.env` file (via python-dotenv)
- **Production**: Environment variables (Railway, Vercel)

---

## Testing Strategy

### Test Organization (228 test files across 13 directories)
- **Unit Tests**: Fast, isolated tests without external dependencies
- **Integration Tests**: Test database and service interactions
- **E2E Tests**: End-to-end tests with Playwright browser automation
- **Health Tests**: Verify health check endpoints
- **Smoke Tests**: Quick verification of core functionality
- **Middleware Tests**: Test middleware components
- **Route Tests**: Test API route handlers
- **Service Tests**: Test business logic services
- **Schema Tests**: Test Pydantic schema validation
- **Security Tests**: Test authentication and authorization
- **Config Tests**: Test configuration loading
- **Utility Tests**: Test utility functions

### Running Tests
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src

# Run specific test file
pytest tests/integration/test_chat.py

# Run in parallel
pytest -n auto

# Run specific test category
pytest tests/e2e/  # End-to-end tests
pytest tests/smoke/  # Smoke tests
```

### Pytest Configuration
Located in `pyproject.toml` with:
- Markers for categorization (unit, integration, slow, critical)
- Coverage configuration (source = ["src"])
- Output formatting (verbose, colored)

---

## Code Quality Standards

### Linting & Formatting
- **Ruff**: Fast Python linter for code quality
- **Black**: Code formatter with 100 char line limit
- **isort**: Import organization
- **MyPy**: Type checking (optional)

### Configuration Files
- `pyproject.toml` - Ruff, Black, isort, MyPy config
- `pytest.ini` - Pytest configuration

---

## Deployment Options

### 1. Vercel (Serverless)
- **Entry Point**: `api/index.py`
- **Configuration**: `vercel.json`
- **Advantages**: Auto-scaling, serverless, no infrastructure

### 2. Railway (Container)
- **Entry Point**: `start.sh`
- **Configuration**: `railway.json`
- **Advantages**: Simple deployment, automatic scaling

### 3. Docker (Self-hosted)
- **Configuration**: Dockerfile(s) in repo
- **Command**: Build and run container
- **Advantages**: Full control, can run anywhere

---

## Common Development Tasks

### Starting Development Server
```bash
cd /root/repo
python src/main.py
# or
uvicorn src.main:app --reload
```

### Adding a New Route
1. Create new module in `src/routes/`
2. Define request/response schemas in `src/schemas/`
3. Implement route handlers
4. Import router in `src/main.py`

### Adding a New Provider
1. Create client module in `src/services/` (e.g., `new_provider_client.py`)
2. Implement provider API integration
3. Register provider in `src/services/providers.py`
4. Add pricing data to pricing configuration
5. Add model mappings to `src/services/model_transformations.py`

### Database Changes
1. Create SQL migration in `supabase/migrations/`
2. Apply with Supabase CLI
3. Update corresponding module in `src/db/`

### Running Tests
```bash
# All tests
pytest

# With coverage
pytest --cov=src

# Specific test
pytest tests/integration/test_chat.py -v
```

---

## Important Files to Know

### Configuration & Setup
- `src/main.py` - Application factory
- `src/config/config.py` - Configuration management
- `pyproject.toml` - Project metadata and dependencies
- `requirements.txt` - Pinned dependency versions

### API Endpoints
- `src/routes/chat.py` - Main chat completion endpoint
- `src/routes/catalog.py` - Model catalog endpoints
- `src/routes/auth.py` - Authentication endpoints
- `src/routes/users.py` - User management endpoints

### Business Logic
- `src/services/models.py` - Model catalog management
- `src/services/openrouter_client.py` - Primary inference provider
- `src/services/pricing.py` - Pricing calculations
- `src/services/rate_limiting.py` - Rate limiting

### Database
- `src/db/users.py` - User database operations
- `src/db/api_keys.py` - API key management
- `src/config/supabase_config.py` - Database initialization

---

## Key Design Patterns

1. **Dependency Injection**: FastAPI dependency system for authentication, logging
2. **Async/Await**: All I/O operations are asynchronous for performance
3. **Service Layer**: Business logic isolated from HTTP handlers
4. **Factory Pattern**: `create_app()` function for app initialization
5. **Encryption at Rest**: Fernet encryption for sensitive data
6. **Rate Limiting**: Redis-backed with fallback mechanism
7. **Multi-Provider Strategy**: Abstract interface with specific implementations
8. **Middleware Pipeline**: Layered middleware for cross-cutting concerns
9. **Registry Pattern**: Canonical model registry for multi-provider routing
10. **Health Check Pattern**: Intelligent, passive, and autonomous health monitors

---

## Performance & Scalability Features

1. **Caching**: Redis for response caching and rate limiting (multiple cache layers)
2. **Connection Pooling**: Reuse database connections with monitoring
3. **Request Prioritization**: Priority queue for important requests
4. **GZip Compression**: Selective response compression via middleware
5. **Async I/O**: Non-blocking for high concurrency
6. **Load Balancing**: Multi-provider routing for failover
7. **Health Checks**: Intelligent, passive, and autonomous provider health monitoring
8. **Metrics Aggregation**: Prometheus metrics with Grafana dashboards
9. **Distributed Tracing**: OpenTelemetry traces with Tempo integration
10. **Query Timeout Handling**: Configurable timeouts for database operations

---

## Security Measures

1. **Encryption**: Fernet (AES-128) for API key storage
2. **Hashing**: HMAC-SHA256 for validation
3. **Authentication**: API key-based with token support
4. **Authorization**: Role-based access control (RBAC)
5. **Audit Logging**: All actions logged to audit table
6. **IP Allowlists**: Restrict API key usage by IP
7. **Domain Restrictions**: Limit API usage by domain
8. **Rate Limiting**: Per-user, per-key, system-wide limits

---

## Recent Updates & Current State

### Latest Changes
- **Rate Limiting Improvements** (2025-02-11) - Fixed GitHub issue #1091
  - Implemented three-layer rate limiting architecture
  - Added security middleware with velocity mode protection
  - Increased IP rate limits (60→300 RPM, 10→60 RPM)
  - Adjusted velocity mode thresholds (10%→25%, 10min→3min)
  - Added authenticated user exemption from IP-based rate limiting
  - Improved error classification (exclude 4xx errors)
  - Added comprehensive rate limit headers
  - Enhanced observability with Prometheus metrics
- Expanded to 30 provider integrations (up from 17)
- OpenTelemetry-based distributed tracing
- Prometheus/Grafana metrics integration
- Sentry error tracking and monitoring
- Arize AI observability platform integration
- Loki log aggregation
- Intelligent health monitoring systems
- Advanced credit management system
- Comprehensive audit logging
- Feature flag integration (Statsig)
- Analytics pipelines (PostHog)
- Stripe payment integration
- Free trial system
- Referral program
- Chat history persistence

### Active Maintenance
- Regular dependency updates
- Provider API updates and integrations
- Performance optimization
- Bug fixes and security patches
- Observability improvements

---

## Quick Reference

| Component | Location | Count |
|-----------|----------|-------|
| Routes | `src/routes/` | 43 |
| Services | `src/services/` | 95 |
| Database Modules | `src/db/` | 24 |
| Schemas | `src/schemas/` | 15 |
| Config Modules | `src/config/` | 8 |
| Middleware | `src/middleware/` | 6 |
| Utilities | `src/utils/` | 15 |
| Test Files | `tests/` | 228 |
| Test Directories | `tests/` | 13 |
| Migrations | `supabase/migrations/` | 36 |
| Documentation | `docs/` | 121 |
| CI/CD Workflows | `.github/workflows/` | 9 |
| **Total Python Code** | `src/` | **85,080 LOC** |

---

## Useful Documentation Files

- `docs/architecture.md` - Detailed system architecture
- `docs/api.md` - API endpoint documentation
- `docs/setup.md` - Local development setup
- `docs/DEPLOYMENT.md` - Deployment guides
- `docs/STRIPE.md` - Payment integration details
- `docs/REFERRAL_SYSTEM.md` - Referral program documentation
- `docs/RATE_LIMITING.md` - Comprehensive rate limiting architecture and troubleshooting
- `README.md` - Main project documentation

---

## Troubleshooting

### Health Check
To verify the API is running and responsive:
```bash
curl https://api.gatewayz.ai/health
```

---

## Adding a New Gateway

To add a new gateway provider to the system:

1. **Add to GATEWAY_REGISTRY** in `src/routes/catalog.py`:

```python
"new-gateway": {
    "name": "New Gateway",
    "color": "bg-purple-500",
    "priority": "slow",
    "site_url": "https://newgateway.com",
},
```

2. **Ensure models include `source_gateway`** field:
   - When implementing the model fetch function, include `"source_gateway": "new-gateway"` in each model's data
   - Also include `"provider_slug": "new-gateway"` for consistency

3. **The frontend will automatically discover and display the new gateway!**
   - The frontend fetches gateway configs from `GET /gateways` endpoint
   - New gateways appear in the UI without frontend code changes
   - Gateway name, color, and priority are all configured in the backend

**Example model data structure:**
```python
{
    "id": "provider/model-name",
    "name": "Model Display Name",
    "source_gateway": "new-gateway",
    "provider_slug": "new-gateway",
    "context_length": 8192,
    # ... other fields
}
```

---

## Notes for Claude

This codebase is a sophisticated, production-grade AI gateway system. When working on tasks:

1. **Understand the Flow**: Requests go through middleware → routes → services → database
2. **Check Existing Patterns**: Many features follow established patterns (provider clients, service layers)
3. **Security First**: Always encrypt sensitive data; add audit logs for sensitive operations
4. **Database Migrations**: Any schema changes need SQL migrations in `supabase/migrations/`
5. **Testing**: Add tests for new features (follow existing test structure in appropriate test directory)
6. **Configuration**: Use environment variables via `src/config/config.py`
7. **Multiple Providers**: When adding features, consider how they work across all 30 providers
8. **Rate Limiting**: Account for Redis availability with fallback mechanisms
9. **Performance**: Use async/await; leverage caching; monitor connection pools
10. **Observability**: Add appropriate metrics (Prometheus), traces (OpenTelemetry), and error tracking (Sentry)
11. **Health Monitoring**: Consider health check impacts when adding new providers or services
12. **Documentation**: Update docs when adding major features

---

**Last Updated**: 2025-02-11
**Version**: 2.0.4
