# Multi-Provider Model Routing

## Overview

The Gatewayz backend now supports **multi-provider model routing**, allowing the same logical model to be accessed through multiple providers with automatic failover, priority-based selection, and intelligent model ID transformation.

## Key Concepts

### Canonical Model ID

Each model has a **canonical ID** that serves as the standardized identifier across the system. For example:
- `llama-3.3-70b-instruct` - The canonical ID for Llama 3.3 70B
- `deepseek-v3` - The canonical ID for DeepSeek V3
- `gemini-2.5-flash` - The canonical ID for Gemini 2.5 Flash

### Model Aliases

Models can have multiple **aliases** that all resolve to the same canonical ID. For example, all of these resolve to `llama-3.3-70b-instruct`:
- `llama-3.3-70b`
- `meta-llama/llama-3.3-70b`
- `meta-llama/llama-3.3-70b-instruct`
- `meta-llama/Llama-3.3-70B-Instruct`

Alias resolution is **case-insensitive**.

### Provider-Specific Model IDs

Each provider may use a different ID for the same logical model:
- OpenRouter: `meta-llama/llama-3.3-70b-instruct`
- Fireworks: `accounts/fireworks/models/llama-v3p3-70b-instruct`
- Together: `meta-llama/Llama-3.3-70B-Instruct`
- HuggingFace: `meta-llama/Llama-3.3-70B-Instruct`

The system automatically transforms the canonical ID to the correct provider-specific format.

### Provider Priority

Each provider is assigned a **priority** (lower number = higher priority). When a request comes in:
1. The system selects the highest-priority provider that meets requirements
2. If that provider fails, it automatically fails over to the next priority
3. Circuit breakers prevent repeated attempts to failing providers

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   User Request                              │
│              model: "llama-3.3-70b"                         │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│          MultiProviderRegistry                              │
│   ┌──────────────────────────────────────────────────┐     │
│   │  Canonical Models:                                │     │
│   │  • llama-3.3-70b-instruct                        │     │
│   │  • deepseek-v3                                   │     │
│   │  • gemini-2.5-flash                              │     │
│   └──────────────────────────────────────────────────┘     │
│   ┌──────────────────────────────────────────────────┐     │
│   │  Alias Index:                                     │     │
│   │  "llama-3.3-70b" → "llama-3.3-70b-instruct"     │     │
│   │  "deepseek-v3" → "deepseek-v3"                  │     │
│   └──────────────────────────────────────────────────┘     │
│   ┌──────────────────────────────────────────────────┐     │
│   │  Provider Index:                                  │     │
│   │  (openrouter, meta-llama/...) → llama-3.3-...   │     │
│   │  (fireworks, accounts/...) → llama-3.3-...      │     │
│   └──────────────────────────────────────────────────┘     │
└─────────────────────┬──────────────────────────────────────┘
                      │
           ┌──────────▼──────────┐
           │ Provider Selection   │
           │ Priority: 1,2,3...   │
           └──────────┬──────────┘
                      │
         ┌────────────┴─────────────┐
         │                           │
    ┌────▼─────┐              ┌─────▼────┐
    │OpenRouter│              │Fireworks │
    │ (Pri 1)  │              │ (Pri 2)  │
    └──────────┘              └──────────┘
```

## Curated Multi-Provider Models

The system includes curated configurations for popular open-source models:

### Meta Llama Family
- `llama-3.3-70b-instruct` - Llama 3.3 70B (OpenRouter, Fireworks, Together, HuggingFace)
- `llama-3.1-70b-instruct` - Llama 3.1 70B (OpenRouter, Fireworks, Together, HuggingFace)
- `llama-3.1-8b-instruct` - Llama 3.1 8B (OpenRouter, Fireworks, HuggingFace)

### DeepSeek Family
- `deepseek-v3` - DeepSeek V3 (Fireworks, OpenRouter, Featherless, Together, HuggingFace)
- `deepseek-r1` - DeepSeek R1 (Fireworks, OpenRouter, HuggingFace)

### Qwen Family
- `qwen-2.5-72b-instruct` - Qwen 2.5 72B (OpenRouter, HuggingFace)
- `qwen-2.5-7b-instruct` - Qwen 2.5 7B (OpenRouter, HuggingFace)

### Mistral Family
- `mixtral-8x7b-instruct` - Mixtral 8x7B (OpenRouter, HuggingFace)
- `mistral-7b-instruct` - Mistral 7B (OpenRouter, HuggingFace)

### Google Gemma Family
- `gemma-2-27b-it` - Gemma 2 27B (Google Vertex, OpenRouter)
- `gemma-2-9b-it` - Gemma 2 9B (Google Vertex, OpenRouter)

### Google Gemini Family
- `gemini-2.5-flash` - Gemini 2.5 Flash (Google Vertex, OpenRouter)
- `gemini-2.5-pro` - Gemini 2.5 Pro (Google Vertex, OpenRouter)
- `gemini-2.0-flash` - Gemini 2.0 Flash (Google Vertex, OpenRouter)
- And more...

## How It Works

### 1. Request Processing

When a chat completion request comes in:

```python
POST /v1/chat/completions
{
  "model": "llama-3.3-70b",
  "messages": [...]
}
```

### 2. Model Resolution

The system resolves the model ID:
1. Check if input matches a canonical ID or alias
2. If found, retrieve the multi-provider model configuration
3. If not found, fall back to legacy single-provider routing

### 3. Provider Selection

For multi-provider models:
1. Select primary provider based on:
   - User's preferred provider (if specified)
   - Required features (e.g., streaming)
   - Provider priority
2. Build failover chain with 2-3 fallback providers

### 4. Model ID Transformation

For each provider in the chain:
1. Look up provider-specific model ID in registry
2. If found, use it directly
3. If not found, fall back to legacy transformation rules

### 5. Request Execution with Failover

```
Try OpenRouter (priority 1)
  ↓ (if fails with 503/504/404)
Try Fireworks (priority 2)
  ↓ (if fails)
Try Together (priority 3)
  ↓ (if fails)
Return error
```

## Adding New Multi-Provider Models

To add a new model to the multi-provider registry:

### 1. Define the Model Configuration

Create or update a configuration file in `src/services/`:

```python
# src/services/my_models_config.py

from src.services.multi_provider_registry import (
    MultiProviderModel,
    ProviderConfig,
    get_registry,
)

def get_my_models():
    return [
        MultiProviderModel(
            id="my-model-canonical-id",
            name="My Model Display Name",
            description="Description of the model",
            context_length=8192,
            modalities=["text"],
            aliases=[
                "model-alias-1",
                "model-alias-2",
                "org/model-name",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="org/model-name",
                    priority=1,  # Highest priority
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.20,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/model-name",
                    priority=2,  # Lower priority
                    cost_per_1k_input=0.12,
                    cost_per_1k_output=0.22,
                    features=["streaming"],
                ),
            ],
        ),
    ]

def initialize_my_models():
    registry = get_registry()
    models = get_my_models()
    for model in models:
        registry.register_model(model)
```

### 2. Register at Startup

Add to `src/services/startup.py`:

```python
from src.services.my_models_config import initialize_my_models

# In lifespan function:
try:
    initialize_my_models()
    logger.info("My models initialized")
except Exception as e:
    logger.warning(f"My models initialization warning: {e}")
```

### 3. Test the Configuration

```python
# Test model resolution
from src.services.multi_provider_registry import get_registry

registry = get_registry()
canonical = registry.resolve_canonical_id("model-alias-1")
assert canonical == "my-model-canonical-id"

# Test provider selection
provider = registry.select_provider("my-model-canonical-id")
assert provider.name == "openrouter"  # Highest priority

# Test model ID transformation
from src.services.model_transformations import transform_model_id
model_id = transform_model_id("my-model-canonical-id", "fireworks")
assert model_id == "accounts/fireworks/models/model-name"
```

## Best Practices

### Choosing Canonical IDs
- Use lowercase with hyphens: `model-name-version`
- Include major version but not patch: `llama-3.3-70b`, not `llama-3.3.1-70b`
- Add instruction suffix if important: `qwen-2.5-72b-instruct`
- Keep it simple and memorable

### Setting Aliases
- Include common variations users might type
- Include org-prefixed versions: `meta-llama/llama-3.3-70b`
- Include both lowercase and official casing (system handles case-insensitive)
- Don't over-alias - focus on likely user inputs

### Provider Priorities
- Priority 1: Most reliable, lowest latency
- Priority 2-3: Good alternatives for failover
- Consider cost when setting priorities
- Test failover paths in staging

### Provider Features
- Always specify: `["streaming"]` if supported
- Add `"function_calling"` if available
- Add `"multimodal"` for vision/audio models
- Features affect provider selection

## Backward Compatibility

The multi-provider routing system maintains **full backward compatibility**:

1. **Legacy model IDs still work**: Requests with provider-specific IDs (e.g., `accounts/fireworks/models/llama-v3p3-70b-instruct`) work as before
2. **Fallback to legacy routing**: Models not in the registry use the existing transformation logic
3. **Existing provider detection**: The old provider detection still runs if registry lookup fails
4. **API compatibility**: No changes to request/response schemas

## Monitoring and Debugging

### Logs to Watch

Multi-provider routing generates detailed logs:

```
INFO: Using multi-provider routing for model 'llama-3.3-70b' (canonical: 'llama-3.3-70b-instruct')
INFO: Multi-provider chain for llama-3.3-70b-instruct: ['openrouter', 'fireworks', 'together']
DEBUG: Registry lookup: llama-3.3-70b-instruct -> meta-llama/llama-3.3-70b-instruct for provider openrouter
INFO: ✓ Request successful with openrouter for llama-3.3-70b-instruct (attempt 1/3)
```

Failover logs:

```
WARNING: Provider 'openrouter' failed with status 503 (Upstream service unavailable). Falling back to 'fireworks'.
DEBUG: Registry lookup: llama-3.3-70b-instruct -> accounts/fireworks/models/llama-v3p3-70b-instruct for provider fireworks
INFO: ✓ Request successful with fireworks for llama-3.3-70b-instruct (attempt 2/3)
```

### Metrics

The system tracks:
- Provider selection counts per model
- Failover frequency per model/provider pair
- Success rates by provider and model
- Average latency by provider and model

## Future Enhancements

Planned improvements:
1. **Background sync**: Automatically update provider catalogs
2. **Cost-aware routing**: Select provider based on price optimization
3. **Latency-aware routing**: Prefer faster providers for time-sensitive requests
4. **User credential routing**: Prefer Vertex AI if user has credentials
5. **Circuit breaker refinement**: Smart cooldown and recovery
6. **Admin UI**: Manage models and providers through dashboard

## Troubleshooting

### Model Not Found in Registry

**Symptom**: Log shows "Model not in registry, use legacy failover chain"

**Solution**: Either add the model to `curated_models_config.py` or ensure it works with legacy transformation

### Provider Always Fails

**Symptom**: Provider consistently returns errors

**Check**:
1. Provider API credentials are configured
2. Model ID is correct for that provider
3. Model is actually available on that provider
4. Rate limits or quota issues

### Wrong Provider Selected

**Symptom**: System uses provider B when you want provider A

**Solution**: 
1. Check provider priorities in model configuration
2. Use `preferred_provider` parameter in request
3. Verify provider has required features (e.g., streaming)

## API Reference

See full API documentation in:
- `src/services/multi_provider_registry.py` - Registry and model definitions
- `src/services/curated_models_config.py` - Curated model configurations
- `src/services/provider_selector.py` - Provider selection logic
- `src/routes/chat.py` - Request routing implementation
