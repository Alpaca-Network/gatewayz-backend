# Multi-Provider Model Registry

The Gatewayz backend now supports routing logical models across multiple providers with automatic failover, intelligent selection, and health monitoring.

## Overview

The multi-provider registry maintains a canonical view of models with their provider configurations, enabling intelligent routing and failover across multiple providers for the same logical model.

## Key Components

### 1. MultiProviderModel
Represents a logical model that can be accessed through multiple providers.

```python
from src.services.multi_provider_registry import MultiProviderModel, ProviderConfig

model = MultiProviderModel(
    id="gemini-1.5-flash",  # Canonical model ID
    name="Gemini 1.5 Flash",
    description="Fast multimodal model",
    context_length=1000000,
    modalities=["text", "image"],
    categories=["chat", "multimodal"],
    capabilities=["streaming", "function_calling"],
    providers=[
        ProviderConfig(
            name="google-vertex",
            model_id="gemini-1.5-flash",
            priority=1,  # Lower number = higher priority
            cost_per_1k_input=0.075,
            cost_per_1k_output=0.30,
            features=["streaming", "multimodal", "function_calling"],
            availability=True
        ),
        ProviderConfig(
            name="openrouter",
            model_id="google/gemini-flash-1.5",
            priority=2,
            cost_per_1k_input=0.10,
            cost_per_1k_output=0.40,
            features=["streaming", "multimodal"],
            availability=True
        )
    ]
)
```

### 2. Provider Selection and Failover
The system automatically selects the best provider based on priority, availability, performance, and cost.

```python
from src.services.provider_selector import get_selector

selector = get_selector()

# Execute with automatic failover
result = selector.execute_with_failover(
    model_id="gemini-1.5-flash",
    execute_fn=lambda provider_name, model_id: call_provider(provider_name, model_id),
    max_retries=3
)

if result["success"]:
    response = result["response"]
    provider_used = result["provider"]
```

## Registering New Models

### 1. Define the Model
Create a `MultiProviderModel` with all provider configurations:

```python
# In src/services/your_provider_config.py
from src.services.multi_provider_registry import MultiProviderModel, ProviderConfig

def get_your_models():
    return [
        MultiProviderModel(
            id="your-model-name",
            name="Your Model Name",
            description="Description of your model",
            context_length=8192,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="your-provider",
                    model_id="provider-specific-model-id",
                    priority=1,
                    cost_per_1k_input=0.05,
                    cost_per_1k_output=0.15,
                    features=["streaming"],
                    availability=True
                ),
                ProviderConfig(
                    name="fallback-provider",
                    model_id="fallback-model-id",
                    priority=2,
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.20,
                    features=["streaming"],
                    availability=True
                )
            ]
        )
    ]
```

### 2. Register During Startup
Add your model registration to the startup process:

```python
# In src/services/startup.py
from src.services.your_provider_config import get_your_models

async def lifespan(app):
    # ... existing startup code ...
    
    # Register your models
    try:
        registry = get_registry()
        models = get_your_models()
        registry.register_models(models)
        logger.info(f"Registered {len(models)} of your models")
    except Exception as e:
        logger.warning(f"Failed to register your models: {e}")
```

## Provider Selection Criteria

The system selects providers based on multiple criteria:

1. **Priority** - Lower numbers have higher priority
2. **Availability** - Providers marked as unavailable are deprioritized
3. **Response Time** - Faster providers are preferred
4. **Cost** - Cheaper providers are preferred (when cost constraints are set)

## Health Monitoring and Circuit Breaker

The system automatically monitors provider health and implements circuit breaker patterns:

- Providers that fail repeatedly are temporarily disabled
- Response times are tracked for performance-based selection
- Success rates are calculated for reliability metrics

## Testing Multi-Provider Routing

Run the integration test to verify routing works:

```bash
python tests/test_multi_provider_routing.py
```

## Migration from Single-Provider Models

The system is backward compatible - models not in the multi-provider registry continue to use the existing routing logic. New models should be added to the registry for enhanced routing capabilities.

## Best Practices

1. **Priority Assignment**: Assign priority 1 to your preferred provider
2. **Cost Accuracy**: Keep cost information up to date
3. **Feature Documentation**: Accurately document supported features
4. **Health Monitoring**: Implement proper error handling to enable health tracking
5. **Fallback Providers**: Always provide at least one fallback provider

## Example: Adding a New Model

Here's a complete example of adding a new model:

```python
# src/services/claude_config.py
from src.services.multi_provider_registry import MultiProviderModel, ProviderConfig

def get_claude_models():
    return [
        MultiProviderModel(
            id="claude-3-5-sonnet",
            name="Claude 3.5 Sonnet",
            description="Most intelligent Claude model",
            context_length=200000,
            modalities=["text", "image"],
            categories=["chat", "reasoning"],
            capabilities=["function_calling", "multimodal"],
            providers=[
                ProviderConfig(
                    name="anthropic",
                    model_id="claude-3-5-sonnet-20240620",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.003,
                    cost_per_1k_output=0.015,
                    max_tokens=8192,
                    features=["streaming", "multimodal", "function_calling"],
                    availability=True
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-3.5-sonnet",
                    priority=2,
                    requires_credentials=False,
                    cost_per_1k_input=0.0035,
                    cost_per_1k_output=0.0175,
                    max_tokens=8192,
                    features=["streaming", "multimodal"],
                    availability=True
                )
            ]
        )
    ]

# In src/services/startup.py
from src.services.claude_config import get_claude_models

async def lifespan(app):
    # ... existing code ...
    
    # Register Claude models
    try:
        registry = get_registry()
        models = get_claude_models()
        registry.register_models(models)
        logger.info(f"Registered {len(models)} Claude models")
    except Exception as e:
        logger.warning(f"Failed to register Claude models: {e}")
```