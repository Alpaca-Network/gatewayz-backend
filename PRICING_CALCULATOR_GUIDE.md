# Provider Pricing Standards & Calculator Guide

This guide explains how to save and use provider pricing standards to accurately calculate costs for any AI model.

## Overview

The system consists of two main components:

1. **`provider_pricing_standards.json`** - Configuration file with all provider pricing standards
2. **`pricing_calculator.py`** - Python module that uses the standards to calculate costs

## Quick Start

### 1. Calculate Cost for a Text Model

```python
from pricing_calculator import calculate_model_cost

# Model data from API
model = {
    "id": "openai/gpt-4",
    "architecture": {"modality": "text->text"},
    "pricing": {"prompt": "0.00003", "completion": "0.00006"}
}

# Your usage
usage = {
    "prompt_tokens": 100,
    "completion_tokens": 50
}

# Calculate cost
cost = calculate_model_cost("openrouter", model, usage)

print(f"Total cost: ${cost['total_cost']:.6f}")
# Output: Total cost: $0.006000
```

### 2. Calculate Cost for an Image Model

```python
# Image model data
model = {
    "id": "simplismart/flux-1.1-pro",
    "architecture": {"modality": "text->image"},
    "pricing": {"image": "0.05"}
}

# Your usage
usage = {
    "num_images": 3,
    "dimensions": "1024x1024"
}

# Calculate cost
cost = calculate_model_cost("simplismart", model, usage)

print(f"Total cost: ${cost['total_cost']:.2f}")
# Output: Total cost: $0.15
```

### 3. Calculate Cost for an Audio Model

```python
# Audio model data
model = {
    "id": "simplismart/whisper-large-v3",
    "architecture": {"modality": "audio->text"},
    "pricing": {"request": "0.0030"}  # Per minute
}

# Your usage
usage = {
    "duration": 5,  # 5 minutes
    "unit": "minutes"
}

# Calculate cost
cost = calculate_model_cost("simplismart", model, usage)

print(f"Total cost: ${cost['total_cost']:.4f}")
# Output: Total cost: $0.0150
```

## Provider Pricing Standards Structure

### Text Models (text->text)

```json
{
  "openrouter": {
    "pricing_unit": "per_token",
    "api_format": "per_token",
    "supported_modalities": {
      "text->text": {
        "pricing_fields": ["prompt", "completion"],
        "unit": "USD per token",
        "calculation": "prompt_tokens * prompt_price + completion_tokens * completion_price"
      }
    }
  }
}
```

### Image Models (text->image)

```json
{
  "simplismart": {
    "supported_modalities": {
      "text->image": {
        "pricing_fields": ["image"],
        "unit": "USD per image (1024x1024)",
        "calculation": "num_images * image_price"
      }
    }
  }
}
```

### Audio Models (audio->text or text->audio)

```json
{
  "simplismart": {
    "supported_modalities": {
      "audio->text": {
        "pricing_fields": ["request"],
        "unit": "USD per minute",
        "calculation": "duration_minutes * request_price"
      }
    }
  }
}
```

## Normalization Rules

All pricing is normalized to **USD per single token** for consistent calculations:

| Provider Format | Example | Normalized |
|----------------|---------|------------|
| Per token | $0.000001 | $0.000001 |
| Per 1K tokens | $0.50 | $0.0005 |
| Per 1M tokens | $1.00 | $0.000001 |
| Amount × Scale | amount=1, scale=-6 | $0.000001 |

### Conversion Examples

```python
from pricing_calculator import normalize_to_per_token

# Per 1K tokens (AiHubMix)
price = normalize_to_per_token(0.50, "per_1K_tokens")
# Result: 0.0005 per token

# Per 1M tokens (DeepInfra)
price = normalize_to_per_token(1.00, "per_1M_tokens")
# Result: 0.000001 per token

# Scientific notation (Near AI)
price = normalize_to_per_token(
    price=None,  # Not used for amount_scale
    api_format="amount_scale",
    amount=1,
    scale=-6
)
# Result: 0.000001 per token
```

## Working with Different Providers

### OpenRouter (per token format)

```python
# Already in per-token format, no conversion needed
model = {
    "pricing": {
        "prompt": "0.00003",      # $0.03 per 1M tokens
        "completion": "0.00006"    # $0.06 per 1M tokens
    }
}
```

### DeepInfra (per 1M tokens format)

```python
# Listed as per 1M tokens on their website
# Stored in manual_pricing.json as per 1M
model = {
    "pricing": {
        "prompt": "0.055",      # $0.055 per 1M tokens
        "completion": "0.055"   # $0.055 per 1M tokens
    }
}

# Calculator automatically converts: 0.055 / 1,000,000 = 0.000000055 per token
```

### AiHubMix (per 1K tokens format)

```python
# API returns per 1K tokens
model = {
    "pricing": {
        "input": "1.25",        # $1.25 per 1K tokens
        "output": "2.50"        # $2.50 per 1K tokens
    }
}

# Calculator automatically converts: 1.25 / 1000 = 0.00125 per token
```

### Near AI (scientific notation format)

```python
# API returns amount and scale
model = {
    "inputCostPerToken": {
        "amount": 1,
        "scale": -6           # 10^-6
    },
    "outputCostPerToken": {
        "amount": 2.5,
        "scale": -6
    }
}

# Calculator converts: 1 × 10^-6 = 0.000001 per token
```

## Utility Functions

### Get Provider Information

```python
from pricing_calculator import get_provider_info

info = get_provider_info("openrouter")
print(info)
```

Output:
```json
{
  "provider": "openrouter",
  "name": "OpenRouter",
  "pricing_unit": "per_token",
  "api_format": "per_token",
  "conversion_factor": 1,
  "supported_modalities": ["text->text", "text->image"],
  "special_features": {
    "free_models": true,
    "free_model_suffix": ":free",
    "dynamic_pricing": true
  }
}
```

### List All Providers

```python
from pricing_calculator import list_all_providers

providers = list_all_providers()
for p in providers:
    print(f"{p['name']}: {p['pricing_unit']}")
```

Output:
```
OpenRouter: per_token
DeepInfra: per_1M_tokens
AiHubMix: per_1K_tokens
SimpliSmart: per_1M_tokens
...
```

### Get Provider Standard

```python
from pricing_calculator import get_provider_standard

standard = get_provider_standard("deepinfra")
print(standard['api_format'])  # 'per_1M_tokens'
print(standard['conversion_factor'])  # 0.000001
```

## Adding a New Provider

To add a new provider, update `provider_pricing_standards.json`:

```json
{
  "providers": {
    "new-provider": {
      "name": "New Provider",
      "pricing_unit": "per_token",
      "api_format": "per_token",
      "conversion_factor": 1,
      "supported_modalities": {
        "text->text": {
          "pricing_fields": ["prompt", "completion"],
          "unit": "USD per token",
          "calculation": "prompt_tokens * prompt_price + completion_tokens * completion_price"
        }
      }
    }
  }
}
```

The calculator will automatically use the new provider standard!

## Real-World Examples

### Example 1: Calculate Cost for Multiple Requests

```python
from pricing_calculator import calculate_model_cost

# OpenRouter GPT-4
model = {
    "id": "openai/gpt-4",
    "architecture": {"modality": "text->text"},
    "pricing": {"prompt": "0.00003", "completion": "0.00006"}
}

# Simulate 10 requests
total_cost = 0
for i in range(10):
    usage = {
        "prompt_tokens": 150,
        "completion_tokens": 200
    }
    cost = calculate_model_cost("openrouter", model, usage)
    total_cost += cost['total_cost']

print(f"Total cost for 10 requests: ${total_cost:.4f}")
# Output: Total cost for 10 requests: $0.1650
```

### Example 2: Compare Costs Across Providers

```python
# Same model on different providers
models = [
    {
        "provider": "openrouter",
        "pricing": {"prompt": "0.00003", "completion": "0.00006"}
    },
    {
        "provider": "deepinfra",
        "pricing": {"prompt": "0.055", "completion": "0.055"}  # Per 1M
    }
]

usage = {"prompt_tokens": 1000, "completion_tokens": 500}

for m in models:
    model_data = {
        "architecture": {"modality": "text->text"},
        "pricing": m["pricing"]
    }
    cost = calculate_model_cost(m["provider"], model_data, usage)
    print(f"{m['provider']}: ${cost['total_cost']:.6f}")
```

Output:
```
openrouter: $0.060000
deepinfra: $0.000083
```

### Example 3: Budget Tracking

```python
from pricing_calculator import calculate_model_cost

# Your monthly budget
budget = 100.00  # $100
spent = 0

# Track requests
requests = []

def make_request(model, usage):
    global spent
    cost = calculate_model_cost("openrouter", model, usage)
    spent += cost['total_cost']
    requests.append(cost)

    print(f"Request cost: ${cost['total_cost']:.4f}")
    print(f"Remaining budget: ${budget - spent:.2f}")

    if spent > budget:
        print("⚠️  Budget exceeded!")

    return cost

# Example usage
model = {
    "id": "openai/gpt-4",
    "architecture": {"modality": "text->text"},
    "pricing": {"prompt": "0.00003", "completion": "0.00006"}
}

make_request(model, {"prompt_tokens": 100, "completion_tokens": 50})
```

## API Integration Example

### Fetch Model Data and Calculate Cost

```python
import httpx
from pricing_calculator import calculate_model_cost

# Fetch model data from Gatewayz API
response = httpx.get("https://api.gatewayz.ai/models?gateway=openrouter&limit=1")
models = response.json()

# Get the first model
model = models[0]
provider = model.get("source_gateway", "openrouter")

# Your usage
usage = {
    "prompt_tokens": 100,
    "completion_tokens": 50
}

# Calculate cost
cost = calculate_model_cost(provider, model, usage)

print(f"Model: {model['id']}")
print(f"Total cost: ${cost['total_cost']:.6f}")
```

## Best Practices

1. **Always use the calculator** - Don't manually calculate costs, the calculator handles all conversion logic

2. **Check modality** - Different modalities use different pricing fields

3. **Update standards regularly** - Provider pricing changes, update `provider_pricing_standards.json` periodically

4. **Log calculations** - Keep records of cost calculations for auditing

5. **Handle errors** - Always check if provider standard exists:
   ```python
   from pricing_calculator import get_provider_standard

   standard = get_provider_standard(provider)
   if not standard:
       print(f"Warning: Unknown provider {provider}")
   ```

## Troubleshooting

### Issue: Wrong cost calculated

**Solution**: Check the provider's API format in `provider_pricing_standards.json` and ensure conversion_factor is correct.

### Issue: Provider not found

**Solution**: Add the provider to `provider_pricing_standards.json` with their pricing format.

### Issue: Modality not recognized

**Solution**: The calculator checks the `architecture.modality` field. Ensure your model data includes this field.

## Summary

- **Configuration**: `provider_pricing_standards.json` stores all provider formats
- **Calculator**: `pricing_calculator.py` handles all conversions automatically
- **Normalization**: Everything is converted to USD per single token
- **Modalities**: Text, image, and audio models are all supported
- **Extensible**: Easy to add new providers by updating the JSON file

You now have a complete system to accurately calculate costs for any AI model from any provider! 🎉
