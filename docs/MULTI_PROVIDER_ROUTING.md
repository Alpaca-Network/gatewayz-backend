# Multi-Provider Routing System

## Overview

The Gatewayz API now supports **multi-provider routing**, allowing logical models (like `gpt-4o`, `claude-sonnet-4.5`, or `llama-3.3-70b`) to be accessed through multiple providers with automatic failover, cost optimization, and circuit breaker patterns.

### Key Benefits

- **Automatic Failover**: If one provider is down, requests automatically route to alternative providers
- **Cost Optimization**: Configure provider priorities based on cost, performance, or availability
- **Higher Reliability**: 99.9%+ uptime through provider redundancy
- **Backward Compatible**: Existing API clients continue to work without changes
- **Centralized Configuration**: Single source of truth for model metadata and provider configurations

---

## Architecture

### Components

1. **Canonical Model Registry** (`src/services/canonical_model_registry.py`)
   - Central registry maintaining logical model definitions
   - Aggregates metadata from multiple providers
   - Handles provider priority and circuit breaking

2. **Registry Sync Service** (`src/services/registry_sync_service.py`)
   - Synchronizes registry with provider catalogs
   - Updates pricing, availability, and capabilities
   - Detects new models across providers

3. **Registry Router** (`src/services/registry_router.py`)
   - Intelligent provider selection and failover
   - Circuit breaker pattern for unhealthy providers
   - Logging and observability for routing decisions

4. **Model Configurations**
   - `src/services/google_models_config.py`: Google/Gemini models
   - `src/services/popular_models_config.py`: Claude, GPT, Llama, DeepSeek, Qwen

---

## How It Works

### 1. Model Registration

Models are registered in the canonical registry with their provider configurations:

```python
from src.services.canonical_model_registry import CanonicalModel, get_canonical_registry
from src.services.multi_provider_registry import ProviderConfig

model = CanonicalModel(
    id="llama-3.3-70b",  # Canonical ID users specify
    name="Llama 3.3 70B",
    description="Meta's latest 70B parameter model",
    context_length=128000,
    providers=[
        ProviderConfig(
            name="fireworks",
            model_id="accounts/fireworks/models/llama-v3p3-70b-instruct",
            priority=1,  # Lower = higher priority
            cost_per_1k_input=0.90,
            cost_per_1k_output=0.90,
            features=["streaming", "function_calling"],
        ),
        ProviderConfig(
            name="together",
            model_id="meta-llama/Llama-3.3-70B-Instruct",
            priority=2,
            cost_per_1k_input=0.88,
            cost_per_1k_output=0.88,
            features=["streaming"],
        ),
        ProviderConfig(
            name="huggingface",
            model_id="meta-llama/Llama-3.3-70B-Instruct",
            priority=3,
            cost_per_1k_input=0.70,
            cost_per_1k_output=0.70,
            features=["streaming"],
        ),
    ],
)

registry = get_canonical_registry()
registry.register_model(model)
```

### 2. Request Routing

When a user makes a request:

```bash
curl https://api.gatewayz.com/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "llama-3.3-70b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

The system:

1. **Detects the model** in the canonical registry
2. **Selects the primary provider** (Fireworks, priority=1)
3. **Transforms the model ID** to `accounts/fireworks/models/llama-v3p3-70b-instruct`
4. **Makes the request** to Fireworks
5. **On failure**, automatically retries with Together (priority=2), then HuggingFace (priority=3)

### 3. Circuit Breaking

If a provider fails repeatedly:

- After 5 consecutive failures, the provider is temporarily disabled for 5 minutes
- Requests skip the disabled provider and use the next available one
- After the timeout, the provider is re-enabled and tested again

---

## Pre-Configured Models

### Google/Gemini Models

Available through **Google Vertex AI** (priority 1) and **OpenRouter** (priority 2):

- `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-pro`
- `gemini-2.0-flash`, `gemini-2.0-flash-exp`
- `gemini-1.5-pro`, `gemini-1.5-flash`
- `gemma-2-9b-it`, `gemma-2-27b-it`

### Claude Models

Available through **OpenRouter** (priority 1) and **Portkey** (priority 2):

- `claude-sonnet-4.5`: Most capable Claude model
- `claude-3-opus`: Most powerful Claude 3 model
- `claude-3-sonnet`: Balanced Claude 3 model
- `claude-3-haiku`: Fast and cost-effective

### GPT Models

Available through **OpenRouter** (priority 1) and **Vercel AI Gateway** (priority 2):

- `gpt-4o`: OpenAI's most advanced multimodal model
- `gpt-4-turbo`: Fast GPT-4 with 128K context
- `gpt-3.5-turbo`: Fast and cost-effective

### Llama Models

Available through **Fireworks** (priority 1), **Together** (priority 2), **HuggingFace** (priority 3), and **OpenRouter** (priority 4):

- `llama-3.3-70b`: Meta's latest 70B parameter model
- `llama-3.1-70b`: 70B model with 128K context
- `llama-3.1-8b`: Efficient 8B parameter model

### DeepSeek Models

Available through **Fireworks** (priority 1), **OpenRouter** (priority 2), and **Together** (priority 3):

- `deepseek-v3`: Latest and most capable DeepSeek model
- `deepseek-r1`: Reasoning-focused model

### Qwen Models

Available through **HuggingFace** (priority 1) and **OpenRouter** (priority 2):

- `qwen-2.5-72b`: Alibaba's 72B parameter model

---

## API Endpoints

### List Multi-Provider Models

```bash
GET /v1/multi-provider/models
```

Query parameters:
- `provider`: Filter by provider availability (e.g., `fireworks`, `openrouter`)
- `query`: Search by name or description
- `min_providers`: Minimum number of providers (e.g., `2` for models with failover)
- `limit`, `offset`: Pagination

Response:
```json
{
  "data": [
    {
      "id": "llama-3.3-70b",
      "name": "Llama 3.3 70B",
      "description": "Meta's latest 70B parameter model",
      "context_length": 128000,
      "modalities": ["text"],
      "providers": [
        {
          "name": "fireworks",
          "model_id": "accounts/fireworks/models/llama-v3p3-70b-instruct",
          "priority": 1,
          "enabled": true,
          "cost_per_1k_input": 0.90,
          "cost_per_1k_output": 0.90,
          "features": ["streaming", "function_calling"]
        },
        {
          "name": "together",
          "model_id": "meta-llama/Llama-3.3-70B-Instruct",
          "priority": 2,
          "enabled": true,
          "cost_per_1k_input": 0.88,
          "cost_per_1k_output": 0.88,
          "features": ["streaming"]
        }
      ],
      "primary_provider": "fireworks",
      "supports_streaming": true,
      "supports_function_calling": true
    }
  ],
  "total": 25,
  "limit": 100,
  "offset": 0
}
```

### Get Specific Model

```bash
GET /v1/multi-provider/models/{model_id}
```

Example:
```bash
curl https://api.gatewayz.com/v1/multi-provider/models/llama-3.3-70b
```

### Get Registry Statistics

```bash
GET /v1/multi-provider/stats
```

Response:
```json
{
  "total_models": 25,
  "multi_provider_models": 15,
  "single_provider_models": 10,
  "total_providers": 10,
  "providers": [
    "fireworks",
    "google-vertex",
    "huggingface",
    "openrouter",
    "together",
    ...
  ]
}
```

---

## Adding New Models

### Method 1: Static Configuration

Create a configuration file (e.g., `src/services/my_models_config.py`):

```python
from src.services.canonical_model_registry import CanonicalModel
from src.services.multi_provider_registry import ProviderConfig

def get_my_models():
    return [
        CanonicalModel(
            id="my-model",
            name="My Custom Model",
            description="A custom model configuration",
            context_length=8192,
            providers=[
                ProviderConfig(
                    name="provider-a",
                    model_id="provider-a/my-model",
                    priority=1,
                    cost_per_1k_input=1.0,
                    cost_per_1k_output=2.0,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="provider-b",
                    model_id="provider-b/my-model",
                    priority=2,
                    cost_per_1k_input=1.5,
                    cost_per_1k_output=2.5,
                    features=["streaming", "function_calling"],
                ),
            ],
        ),
    ]

def initialize_my_models():
    from src.services.canonical_model_registry import get_canonical_registry
    registry = get_canonical_registry()
    for model in get_my_models():
        registry.register_model(model)
```

Then call `initialize_my_models()` in `src/services/startup.py`.

### Method 2: Dynamic Sync from Provider

Use the registry sync service to populate models from a provider catalog:

```python
from src.services.registry_sync_service import get_sync_service
from src.services.models import get_cached_models

sync_service = get_sync_service()

# Register a fetcher function
sync_service.register_provider_fetcher(
    "my-provider",
    lambda: get_cached_models("my-provider")
)

# Sync the provider catalog
stats = sync_service.sync_provider_catalog("my-provider")
print(f"Synced {stats['models_processed']} models")
```

---

## Migration Guide

### For Existing API Clients

**No changes required!** The multi-provider system is backward compatible:

```python
# This still works exactly as before
response = requests.post(
    "https://api.gatewayz.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}]
    }
)
```

The system will automatically:
1. Detect `gpt-4o` is in the canonical registry
2. Route to OpenRouter (priority 1)
3. Failover to Vercel AI Gateway (priority 2) if OpenRouter fails

### For Adding Provider Preferences

Specify a provider to override automatic selection:

```python
response = requests.post(
    "https://api.gatewayz.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {API_KEY}"},
    json={
        "model": "llama-3.3-70b",
        "provider": "together",  # Force Together instead of Fireworks
        "messages": [{"role": "user", "content": "Hello!"}]
    }
)
```

---

## Testing

Run the integration tests:

```bash
pytest tests/integration/test_multi_provider_routing.py -v
```

Test specific functionality:

```bash
# Test registry
pytest tests/integration/test_multi_provider_routing.py::TestCanonicalModelRegistry -v

# Test routing
pytest tests/integration/test_multi_provider_routing.py::TestRegistryRouter -v

# Test transformations
pytest tests/integration/test_multi_provider_routing.py::TestModelTransformations -v

# End-to-end test
pytest tests/integration/test_multi_provider_routing.py::test_end_to_end_multi_provider_routing -v
```

---

## Monitoring & Observability

### Logs

Multi-provider routing adds detailed logging:

```
INFO: Canonical model llama-3.3-70b: selected fireworks (priority 1)
INFO: Attempt 1/3 for model 'llama-3.3-70b': Trying provider 'fireworks' (model: 'accounts/fireworks/models/llama-v3p3-70b-instruct') from canonical registry
INFO: ✓ Request successful with primary provider 'fireworks' for model 'llama-3.3-70b'
```

On failover:

```
WARNING: Provider 'fireworks' failed for model 'llama-3.3-70b' (attempt 1/3): Service unavailable. Will retry with next provider...
INFO: Attempt 2/3 for model 'llama-3.3-70b': Trying provider 'together' (model: 'meta-llama/Llama-3.3-70B-Instruct') from canonical registry
INFO: ✓ Request successful with failover provider 'together' for model 'llama-3.3-70b' (attempt 2/3)
```

### Metrics

The system tracks:
- Provider selection decisions
- Failover occurrences
- Circuit breaker state changes
- Per-provider success/failure rates

Access via `/v1/multi-provider/stats`.

---

## Configuration

### Provider Priority

Lower priority number = higher priority:

```python
ProviderConfig(
    name="premium-provider",
    model_id="premium/model",
    priority=1,  # Try first
)

ProviderConfig(
    name="backup-provider",
    model_id="backup/model",
    priority=2,  # Try second
)
```

### Circuit Breaker Settings

Edit `src/services/provider_selector.py`:

```python
class ProviderHealthTracker:
    def __init__(
        self,
        failure_threshold: int = 5,  # Failures before circuit opens
        timeout_seconds: int = 300,  # 5 minutes cooldown
    ):
        ...
```

### Features

Specify what a provider supports:

```python
ProviderConfig(
    name="my-provider",
    model_id="my-provider/model",
    features=[
        "streaming",          # Server-sent events
        "function_calling",   # Function/tool calling
        "tools",             # Tools API
        "vision",            # Image inputs
        "audio",             # Audio inputs
        "multimodal",        # Multiple modalities
    ],
)
```

---

## Troubleshooting

### Model Not Found in Registry

**Issue**: `Model 'my-model' not found in canonical registry`

**Solution**: The model hasn't been registered. Either:
1. Add it to a configuration file (see "Adding New Models")
2. Wait for automatic sync if using the sync service
3. Check spelling/capitalization (model IDs are case-sensitive)

### All Providers Failed

**Issue**: `All providers failed for model 'my-model'`

**Solution**:
1. Check provider API keys are configured
2. Verify providers support the model
3. Check provider status pages for outages
4. Review logs for specific error details

### Circuit Breaker Open

**Issue**: Provider keeps getting skipped

**Solution**: The circuit breaker detected repeated failures and temporarily disabled the provider. Either:
1. Wait 5 minutes for automatic re-enable
2. Fix the underlying issue (credentials, network, etc.)
3. Manually re-enable: `registry.enable_provider(model_id, provider_name)`

---

## Best Practices

1. **Configure Multiple Providers**: Always register 2-3 providers per model for reliability
2. **Set Priorities by Cost/Performance**: Put cheaper or faster providers at higher priority
3. **Use Features Wisely**: Only specify features the provider actually supports
4. **Monitor Failover Rates**: High failover indicates provider issues
5. **Keep Pricing Updated**: Use the sync service to keep costs current
6. **Test Failover**: Periodically test that failover works as expected

---

## Future Enhancements

- **Automatic Cost Optimization**: Route to cheapest available provider
- **Load Balancing**: Distribute requests across providers for performance
- **Provider Health Checks**: Proactive health monitoring
- **Dynamic Provider Addition**: Add new providers without code changes
- **Per-User Provider Preferences**: Let users specify preferred providers
- **Smart Retries**: Exponential backoff with jitter

---

## Support

For questions or issues:
- GitHub Issues: https://github.com/terragonlabs/gatewayz/issues
- Documentation: https://docs.gatewayz.com
- Email: support@gatewayz.com

---

## Changelog

### v2.1.0 (2025-01-08)

- **Added**: Canonical model registry for multi-provider support
- **Added**: Registry sync service for automatic provider catalog updates
- **Added**: Pre-configured models: Claude, GPT, Llama, DeepSeek, Qwen, Gemini
- **Added**: New API endpoints: `/v1/multi-provider/models`, `/v1/multi-provider/stats`
- **Added**: Circuit breaker pattern for provider health management
- **Updated**: Model transformations to use canonical registry first
- **Updated**: Provider detection to support multi-provider models
- **Maintained**: Full backward compatibility with existing API clients
