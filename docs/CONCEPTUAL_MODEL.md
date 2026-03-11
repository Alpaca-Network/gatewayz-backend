# Gatewayz — Conceptual Model

## Part 1: The High-Level Explanation

### What Is Gatewayz?

Gatewayz is a **universal AI gateway**. It sits between applications and every major AI model provider in the world, giving developers access to thousands of AI models through a single API.

Think of it like this:

- **Without Gatewayz**: A company that wants to use AI models from OpenAI, Google, Anthropic, Meta, Mistral, and others needs to build and maintain a separate integration for each provider. Each has its own API format, its own billing account, its own authentication, and its own quirks. If one goes down, the application goes down with it.

- **With Gatewayz**: The company integrates once. One API call, one API key, one bill. Gatewayz handles the rest — routing the request to the right provider, translating between formats, switching to a backup if something fails, and tracking every token and dollar.

### The Analogy

Gatewayz is to AI providers what **Stripe is to payment processors**.

Stripe lets businesses accept payments from Visa, Mastercard, Amex, and dozens of other networks through one integration. Businesses don't think about which card network to use — Stripe handles routing, retries, and reconciliation.

Gatewayz does the same for AI inference. Developers don't think about which provider serves which model, or what happens if that provider has an outage. They send a request, get a response, and see the cost on one bill.

### What Does It Actually Do?

```
Your Application  ──►  Gatewayz  ──►  OpenAI
                                  ──►  Anthropic
                                  ──►  Google
                                  ──►  Meta (via providers)
                                  ──►  Mistral (via providers)
                                  ──►  DeepSeek (via providers)
                                  ──►  ... 30+ more providers
```

1. **One API, every model** — Send a standard API request. Gatewayz figures out which provider serves that model and routes accordingly.

2. **Automatic failover** — If a provider goes down mid-request, Gatewayz silently retries with another provider that serves the same model. The developer never sees the failure.

3. **Intelligent routing** — Don't know which model to use? Ask Gatewayz to pick the best one for your task — optimized for quality, cost, speed, or a balance of all three.

4. **One bill** — Every model from every provider is billed through one credit balance. Pay-as-you-go, subscription, or trial.

5. **Full visibility** — Every request is tracked: which model, which provider, how many tokens, how much it cost, how fast it responded, whether it succeeded.

### Who Is It For?

| Audience | What they get |
|----------|--------------|
| **Developers** | One SDK integration instead of 30. Drop-in compatible with OpenAI and Anthropic formats — existing code works unchanged. |
| **Engineering teams** | Automatic failover, health monitoring, and rate limiting without building it themselves. |
| **Product teams** | Access to every model for experimentation. Switch models by changing a string, not rewriting code. |
| **Finance / Ops** | One vendor, one invoice, clear per-request cost attribution. |
| **Enterprise** | Security (encrypted keys, IP allowlists, audit logs), compliance, and SLA-backed reliability. |

### The One-Sentence Pitch

> **One API key, every AI model, automatic reliability, one bill.**

---

## Part 2: The Optimal Conceptual Model

This section describes what Gatewayz aims to be — the complete, optimal system. It covers both what exists today and what the system should evolve into. This is the target architecture.

---

### 2.1 System Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           GATEWAYZ GATEWAY                               │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────────────┐  │
│  │  Ingress   │  │  Core      │  │  Intel-    │  │  Business         │  │
│  │  Layer     │  │  Routing   │  │  ligence   │  │  Layer            │  │
│  │            │  │  Engine    │  │  Layer     │  │                   │  │
│  │ Auth       │  │            │  │            │  │ Credits & Billing │  │
│  │ Rate Limit │  │ Provider   │  │ Health     │  │ Plans & Trials    │  │
│  │ Guardrails │  │ Resolution │  │ Monitoring │  │ Usage Analytics   │  │
│  │ Validation │  │ Failover   │  │ Benchmarks │  │ Webhooks          │  │
│  │            │  │ Load Bal.  │  │ Quality    │  │ SLA Tracking      │  │
│  │            │  │ Smart Rtr  │  │ Scoring    │  │                   │  │
│  └────────────┘  └────────────┘  └────────────┘  └───────────────────┘  │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────────────┐  │
│  │  Caching   │  │  Model     │  │  Observa-  │  │  Developer        │  │
│  │  System    │  │  Catalog   │  │  bility    │  │  Platform         │  │
│  │            │  │            │  │            │  │                   │  │
│  │ Semantic   │  │ Discovery  │  │ Metrics    │  │ Prompt Mgmt       │  │
│  │ Response   │  │ Metadata   │  │ Tracing    │  │ Batch Inference   │  │
│  │ Catalog    │  │ Pricing    │  │ Alerts     │  │ Eval & Testing    │  │
│  │ Auth       │  │ Enrichment │  │ Dashboards │  │ Playgrounds       │  │
│  └────────────┘  └────────────┘  └────────────┘  └───────────────────┘  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    30+ AI Model Provider Gateways
```

---

### 2.2 Ingress Layer — Request Entry & Protection

Every request passes through the ingress layer before anything else. This is the security and quality boundary.

#### Authentication & Authorization
- **API key authentication** with keys encrypted at rest (AES-128 Fernet)
- **HMAC-SHA256** key hashing for fast lookup without decryption
- **Role-based access control (RBAC)** — admin, developer, user tiers with distinct permissions
- **Per-key IP allowlists** — restrict an API key to specific IP addresses or ranges
- **Domain restrictions** — limit which domains can use a key

#### Rate Limiting (Three Layers)
- **Layer 1 — IP-level**: Protects against abuse at the network edge. Behavioral analysis and velocity detection for anomalous patterns.
- **Layer 2 — API key-level**: Redis-backed per-key limits (requests per minute, tokens per day/month). Tied to the user's plan tier.
- **Layer 3 — Anonymous**: Separate, stricter limits for unauthenticated requests.
- **Graceful degradation**: If Redis is unavailable, an in-memory fallback rate limiter activates. Requests are never blocked due to infrastructure failure.

#### Input Guardrails
- **PII detection** — Scan prompts for personally identifiable information (phone numbers, SSNs, emails, credit cards) before sending to external providers. Optionally redact or block.
- **Prompt injection defense** — Detect and block known injection patterns that attempt to override system prompts.
- **Topic restrictions** — Per-API-key configuration to restrict models to specific domains (e.g., "only answer customer support questions").
- **Content moderation** — Integration with moderation classifiers to block harmful or policy-violating inputs before they reach any provider.

#### Output Guardrails
- **Content filtering** — Scan model responses for policy violations, harmful content, or off-topic answers before returning to the customer.
- **Structured output validation** — When the customer requests JSON schema output, validate the response conforms before returning it.
- **Hallucination flags** — Surface provider-side safety metadata (refusals, safety filter triggers) in a standardized format regardless of which provider generated the response.

---

### 2.3 Core Routing Engine — Getting Requests to the Right Place

This is the central nervous system of Gatewayz. Every request must be resolved to a specific provider and model ID.

#### Model Resolution Pipeline

```
User sends: model = "deepseek-r1"
                │
                ▼
    ┌─ Alias Normalization ─┐
    │  "deepseek-r1"        │
    │  → "deepseek/deepseek-r1" │
    └───────────┬───────────┘
                ▼
    ┌─ Provider Detection ──┐
    │  Check overrides       │
    │  Check format rules    │
    │  Check registry        │
    │  → Provider: "fireworks" │
    └───────────┬───────────┘
                ▼
    ┌─ Model ID Transform ──┐
    │  Translate to native   │
    │  provider format       │
    │  → "accounts/fireworks/│
    │     models/deepseek-r1"│
    └───────────┬───────────┘
                ▼
         Provider API call
```

- **120+ aliases** map shorthand names to canonical model IDs (`"r1"` → `"deepseek/deepseek-r1"`, `"gpt-4o"` → `"openai/gpt-4o"`)
- **Provider detection** follows a strict priority: explicit overrides → format-based rules → mapping tables → org-prefix fallbacks
- **Model ID transformation** translates canonical IDs to each provider's native format (every provider has different naming conventions)

#### Intelligent Routing (Auto-Select)

When the user doesn't specify a model, Gatewayz picks the optimal one:

| Router | Syntax | What it does |
|--------|--------|-------------|
| **General Router** | `router:general:quality` | ML-powered model selection (via NotDiamond). Analyzes the prompt content and picks the best model for: `quality`, `cost`, `latency`, or `balanced`. |
| **Code Router** | `router:code:agentic` | Benchmark-driven code model selection. Classifies task complexity, matches to tiered models scored by SWE-bench and code benchmarks. Modes: `auto`, `price`, `quality`, `agentic`. |

#### Provider Failover

When a provider fails, the request automatically retries with the next provider in a prioritized chain:

```
Primary (Fireworks) ──FAIL──► OpenRouter ──FAIL──► Together ──SUCCESS──► Response
```

- **14-provider failover chain** ordered by reliability
- **Triggers on**: 401, 402 (provider out of credits), 403, 404, 502, 503, 504
- **Does not trigger on**: 400 (user error), 429 (rate limit — retry with backoff instead)
- **Circuit breakers** per provider: after 5 consecutive failures, the provider is temporarily removed from the chain. Auto-recovers after 1 minute (60 seconds) of cool-down.
- **Model-aware rules**: OpenAI models only failover to OpenAI → OpenRouter. Anthropic models only to Anthropic → OpenRouter. Open-source models can failover across all providers.

#### Load Balancing

For models available on multiple providers simultaneously:

- **Health-weighted routing** — Before attempting a request, check the primary provider's health. If uptime < threshold, promote a healthier provider to the front of the chain.
- **Latency-optimal selection** — For the same model on multiple providers, route to the provider with the lowest current P50 latency.
- **Cost-optimal selection** — When the user requests cost optimization, select the cheapest provider that serves the model and meets minimum quality/latency thresholds.
- **Traffic splitting** — Distribute load across providers to prevent over-reliance on any single one (e.g., 70/30 split) and to continuously gather performance data from all providers.

---

### 2.4 Intelligence Layer — Knowing What's Healthy and What's Good

#### Health Monitoring

A continuous, tiered monitoring system that watches every model across every provider:

| Tier | Coverage | Check interval | Examples |
|------|----------|---------------|----------|
| **Critical** | Top 5% by usage | Every 5 minutes | GPT-4o, Claude Sonnet, Gemini Pro |
| **Popular** | Next 20% | Every 30 minutes | Llama-3.3-70B, Mistral Large |
| **Standard** | Remaining 75% | Every 2-4 hours | Long-tail models |
| **On-Demand** | New/rare models | Only when requested | Niche or newly added models |

- **Passive health capture**: Every real inference request contributes health data as a background task — zero overhead on the request path.
- **Circuit breaker states**: CLOSED (healthy) → OPEN (failing, blocked) → HALF_OPEN (testing recovery).
- **Incident management**: Severity levels (Critical/High/Medium/Low) with automatic incident creation.

#### Model Quality Scoring & Benchmarks

Every model in the catalog should carry quality scores that help users and the routing engine make informed decisions:

- **Benchmark integration** — Pull scores from standardized benchmarks: MMLU, HumanEval, MATH, MT-Bench, LMSYS Arena ELO, LiveBench, SWE-bench.
- **Task-specific quality priors** — Per-model scores for: code generation, reasoning, creative writing, summarization, translation, data extraction, simple Q&A.
- **Real-time quality signals** — Blend static benchmarks with live data: success rate, retry rate, format compliance rate, average response time.
- **Per-customer quality tracking** — Track whether a model performs well for a specific customer's use case over time, enabling personalized routing recommendations.

#### Provider Credit Monitoring

- Track upstream provider credit balances continuously.
- When a provider's credits are low, preemptively deprioritize it in the failover chain before it starts returning 402 errors.

---

### 2.5 Caching System — Speed and Cost Reduction

A multi-layer caching architecture that minimizes latency, reduces costs, and never blocks a request if a cache layer fails.

```
Request
  │
  ▼
┌─ Semantic Cache ──────────────────────────────────────────┐
│  "What's the capital of France?" ≈ "Tell me France's      │
│   capital city" → same cached response                     │
│  (Vector similarity, cosine threshold > 0.95)              │
└──────────────┬────────────────────────────────────────────┘
               │ miss
               ▼
┌─ Exact-Match Response Cache ──────────────────────────────┐
│  SHA-256 hash of {messages + model + params}               │
│  20K entries, 60-min TTL, LRU eviction                     │
└──────────────┬────────────────────────────────────────────┘
               │ miss
               ▼
┌─ External Cache (Butter.dev) ─────────────────────────────┐
│  Third-party LLM response caching proxy                    │
│  Identical prompts across all customers → shared cache     │
│  Sub-100ms response on hit vs 1-5s from provider           │
└──────────────┬────────────────────────────────────────────┘
               │ miss
               ▼
         Provider API call
```

**Supporting caches:**

| Cache | What it stores | TTL | Purpose |
|-------|---------------|-----|---------|
| Auth cache | API key → user data | 5-10 min | Reduces auth latency from 50-150ms to 1-5ms |
| Catalog cache (L1) | Full serialized catalog HTTP response | 5 min | Sub-10ms catalog responses with stampede protection |
| Catalog cache (L2) | Per-provider model lists in Redis | 15-30 min | Avoids rebuilding catalog on every request |
| DB query cache | User, plan, pricing, rate limit lookups | 1-30 min | 60-80% database load reduction |
| Health cache | Model health data | 6 min | Feeds health-based routing decisions |
| Local memory cache | Redis fallback (LRU, 500 entries) | 15 min | Ensures system works when Redis is down |

**Design principle**: Every cache layer degrades gracefully. If Redis goes down, local memory takes over. If all caches miss, the request goes to the database or provider directly. No cache failure ever blocks a user request.

---

### 2.6 Model Catalog — Discovery, Metadata, and Requirements

The model catalog is the system's inventory — it knows what models exist, where they're hosted, what they cost, and what they can do.

#### Model Discovery & Sync

Models are **not fetched from providers on each user request**. Instead:

```
Background sync (scheduled) ──► Provider APIs ──► models_catalog DB table
                                                         │
User request ──► Cache L1 ──► Cache L2 ──► Database ─────┘
```

- A scheduled background process calls each provider's API to refresh the catalog.
- Results are stored in the database.
- User-facing requests only read from cache → database, never hitting provider APIs on the hot path.
- If a provider's API is down, the system serves the last successfully synced catalog.

#### Model Metadata — What Every Model Carries

Every model in the catalog has:

| Field | Description | Example |
|-------|------------|---------|
| `id` | Canonical identifier | `meta-llama/Llama-3.3-70B-Instruct` |
| `name` | Display name | `Llama 3.3 70B Instruct` |
| `provider_slug` | Which gateway serves it | `fireworks` |
| `context_length` | Maximum token window | `131072` |
| `modality` | Input → output type | `text→text`, `text→image`, `image→text` |
| `pricing` | Cost per token (prompt + completion) | `$0.00000055 / token` |
| `supports_streaming` | SSE streaming support | `true` |
| `supports_function_calling` | Tool/function use | `true` |
| `supports_vision` | Image input support | `false` |
| `health_status` | Current health | `healthy`, `degraded`, `down` |
| `benchmark_scores` | Quality scores by task | `{code: 92, reasoning: 88, ...}` |
| `huggingface_metrics` | Downloads, likes, parameters | Community engagement data |

#### Model Requirements for Catalog Inclusion

A model must meet these requirements to appear in the catalog:

1. **Resolvable pricing** — Models without pricing data from any source (database, manual file, cross-reference) are excluded. This prevents users from running expensive models at default rates.
2. **Active provider** — The model's provider must be registered and reachable.
3. **Valid modality** — The model must have a known input/output modality.
4. **Not duplicate** — When the same model is available from multiple providers, the catalog supports both a unique (deduplicated) view and a full (all providers) view.

#### HuggingFace Enrichment

Models with a HuggingFace ID receive additional community data:
- Download count, likes, parameter count
- Pipeline tag (text-generation, text-to-image, etc.)
- Author information and avatar
- Available inference providers

---

### 2.7 Business Layer — Credits, Plans, and Revenue

#### Credit System

The atomic unit of billing. Every API request consumes credits based on token usage.

```
Cost = (prompt_tokens × prompt_price) + (completion_tokens × completion_price)
```

**Deduction order:**
1. Subscription allowance (monthly credits included in plan) — used first
2. Purchased credits (top-ups) — used after allowance is exhausted

**Safety rails:**
- **Pre-flight credit check**: Before calling any provider, estimate max cost. If insufficient credits → 402 immediately (no wasted provider call).
- **Idempotent deduction**: Every deduction carries a unique request ID. Retries never double-charge.
- **Atomic transactions**: Balance update and transaction record happen in a single database transaction.
- **Auto-refund**: Provider errors (5xx, timeouts, empty streams) are automatically refunded. User errors (4xx) are not.
- **High-value model protection**: Premium models (GPT-4, Claude, Gemini, o1/o3/o4) are blocked from serving if pricing falls through to default — prevents massive under-billing.
- **Daily usage cap**: Safety limit to prevent runaway costs.

#### Plans & Tiers

| Tier | Billing | Allowance | Limits | Target |
|------|---------|-----------|--------|--------|
| **Trial** | Free, 14 days | $5 credit cap, 1M tokens, 10K requests | Strict | New users evaluating the platform |
| **Dev** | Pay-as-you-go | Optional monthly allowance | Standard | Individual developers |
| **Team** | Subscription | Monthly credit allowance | Higher concurrency, higher rate limits | Teams and startups |
| **Enterprise** | Custom | Negotiated | Custom SLAs, dedicated support | Large organizations |

- Trial users can still access `:free` suffix models after trial expiration.
- Unused subscription allowance does not roll over — it resets monthly.
- Purchased credits never expire and survive plan changes.

#### Customer Usage Analytics

Customers should have full visibility into their usage:

- **Usage breakdown** — Spend by model, by API key, by day. Token counts, request counts, error rates.
- **Cost attribution** — Which API key, which team member, which application consumed what.
- **Latency percentiles** — P50, P95, P99 response times per model.
- **Time-series data** — Hourly and daily usage trends for dashboard rendering.
- **Exportable** — CSV/JSON export for finance teams and internal reporting.

#### Customer Webhooks

Programmatic event notifications so customers can build automations:

| Event | Trigger |
|-------|---------|
| `credits.low` | Balance drops below configurable threshold |
| `credits.depleted` | Balance reaches zero |
| `credits.added` | Credits purchased or granted |
| `model.degraded` | A model the customer uses becomes unhealthy |
| `rate_limit.approaching` | Usage approaching rate limit threshold |
| `batch.completed` | Async batch job finished |

- Delivery with retry logic and exponential backoff.
- HMAC-SHA256 signed payloads for verification.
- Delivery log for debugging.

#### SLA Tracking

- **Uptime calculation** per provider, per model, per customer plan tier.
- **Historical incident log** — customer-visible timeline of outages and degradations.
- **SLA breach alerting** — notify customer when P99 latency or error rate exceeds their plan's SLA.
- **Credit-back** — automatic compensation when SLA thresholds are violated.

---

### 2.8 Developer Platform — Tools Beyond Inference

#### Prompt Management

A centralized system for managing, versioning, and testing prompts:

- **Template library** — Store and version system prompts. Retrieve by ID or name.
- **Template variables** — `{{customer_name}}`, `{{context}}`, `{{language}}` — filled at request time.
- **A/B testing** — Run two prompt variants side by side, measure which produces better outcomes.
- **Per-key defaults** — Attach a default system prompt to an API key so it's injected on every request.

#### Batch / Async Inference

For workloads that don't need real-time responses:

```
POST /v1/batch/jobs
  → Submit list of prompts
  → Job runs off-peak (cheaper)
  → Poll status or receive webhook on completion
  → Download results
```

- Typically 50% cheaper than synchronous inference.
- Essential for: document processing, data extraction, bulk evaluation, dataset generation.

#### Evaluation & Testing

- **Model comparison** — Send the same prompt to multiple models, compare outputs side-by-side.
- **Regression testing** — Define test cases, run them against model updates, flag quality regressions.
- **Playground** — Interactive web UI for testing prompts against any model in the catalog.

---

### 2.9 Observability — Full Visibility Into Everything

#### For the Gatewayz Team (Internal)

| Layer | Tool | What it tracks |
|-------|------|---------------|
| **Metrics** | Prometheus + Grafana | Request rates, latencies, error rates, cache hit rates, credit usage, provider health, token throughput |
| **Tracing** | OpenTelemetry | Full request lifecycle traces across all services |
| **Error tracking** | Sentry | Exceptions, stack traces, breadcrumbs with automatic alerting |
| **AI-specific tracing** | Arize Phoenix + Braintrust | LLM-specific observability: prompt/response pairs, token usage, quality scoring |
| **Profiling** | Pyroscope | CPU and memory profiling of hot paths (cache operations, auth, routing) |

#### For Customers

- **Usage dashboard** — Real-time and historical view of spend, tokens, requests, errors.
- **Model health status** — Which models are healthy, degraded, or down right now.
- **Status page** — Historical uptime, incident timeline, SLA compliance.
- **Request logs** — Per-request detail: model used, provider, tokens, cost, latency, status.

---

### 2.10 API Compatibility — Drop-In Replacement

Gatewayz exposes two API-compatible interfaces:

| Format | Endpoint | What it means |
|--------|----------|--------------|
| **OpenAI-compatible** | `POST /v1/chat/completions` | Any application built for the OpenAI API works with Gatewayz by changing the base URL. No code changes. |
| **Anthropic-compatible** | `POST /v1/messages` | Any application built for the Anthropic API works with Gatewayz by changing the base URL. No code changes. |

Both formats support streaming (SSE) and non-streaming responses. Responses are normalized to the expected format regardless of which provider actually served the request.

---

### 2.11 Infrastructure & Deployment

#### Multi-Region

- **Geo-aware routing** — Route requests to the nearest provider region for lowest latency.
- **Data residency** — EU customers' requests routed to EU-based providers for GDPR compliance.
- **Multi-region Redis** — Cache replication across regions for consistent performance.
- **Edge deployment** — HTTP termination at the edge, application logic in regional clusters.

#### Deployment Targets

| Target | Use case |
|--------|----------|
| **Vercel** (serverless) | Quick deployment, auto-scaling |
| **Railway / Docker** (container) | Full control, persistent connections |
| **Self-hosted** | Enterprise on-prem deployment |

---

## Part 3: Summary — The Complete Picture

```
┌─────────────────────────────────────────────────────────────────┐
│                      THE CUSTOMER                                │
│                                                                  │
│  "I want to use any AI model, reliably, at the best price,     │
│   with full visibility, through one integration."                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        GATEWAYZ                                  │
│                                                                  │
│  PROTECT        ROUTE           OPTIMIZE        BILL             │
│  ───────        ─────           ────────        ────             │
│  Auth           Model resolve   Health monitor  Credits          │
│  Rate limit     Provider detect Smart routing   Plans            │
│  Guardrails     Failover chain  Caching (7+     Usage analytics  │
│  Validation     Load balancing   layers)        Webhooks         │
│                 Smart routing   Benchmarks       SLA tracking    │
│                                 Cost optimize                    │
│                                                                  │
│  CATALOG        PLATFORM        OBSERVE                          │
│  ───────        ────────        ───────                          │
│  10,000+ models Prompt mgmt     Metrics          Status page     │
│  Auto-sync      Batch inference Tracing          Customer logs   │
│  Pricing        Eval & testing  Alerts           Dashboards      │
│  Enrichment     Playgrounds     Profiling                        │
│                                                                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   30+ AI PROVIDER GATEWAYS                       │
│                                                                  │
│  OpenAI  Anthropic  Google  Groq  Fireworks  Together  Meta     │
│  DeepInfra  Cerebras  HuggingFace  Featherless  Cloudflare     │
│  xAI  Alibaba  NEAR  Fal  Helicone  AiHubMix  Morpheus  ...   │
└─────────────────────────────────────────────────────────────────┘
```

### The Vision

Any developer or company can use **any AI model** from **any provider** through **one API key** and **one bill** — with automatic reliability, cost optimization, quality-aware routing, full visibility, and enterprise-grade security.

Gatewayz becomes the **default infrastructure layer** through which the world consumes AI — not by locking anyone into a single provider, but by making every provider accessible, reliable, and observable through one unified gateway.
