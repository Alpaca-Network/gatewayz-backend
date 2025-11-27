# OpenRouter Auto Model - Testing Guide

This guide provides instructions for testing the `openrouter/auto` model with your Gatewayz API.

## Quick Test (No Dependencies)

### Using curl to test OpenRouter directly:

```bash
# Set your OpenRouter API key
export OPENROUTER_API_KEY='your-key-here'

# Run the test script
./test_openrouter_auto_curl.sh
```

Or manually:

```bash
curl https://openrouter.ai/api/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -d '{
    "model": "openrouter/auto",
    "messages": [
      {
        "role": "user",
        "content": "Say hello and tell me which model you are."
      }
    ],
    "max_tokens": 100
  }'
```

### Expected Response:

```json
{
  "id": "gen-xxxxxxxxxxxxx",
  "model": "anthropic/claude-sonnet-4-0",  // The actual model it routed to
  "created": 1732713600,
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! I'm Claude Sonnet 4.0..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 25,
    "total_tokens": 40
  }
}
```

**Key Point**: The `model` field in the response shows which model OpenRouter Auto selected!

## Testing Through Gatewayz API

### Prerequisites

1. Have your Gatewayz API key
2. Ensure OpenRouter is configured as a provider
3. Set `OPENROUTER_API_KEY` in your environment

### Using the Gatewayz API:

```bash
# Using your Gatewayz API
curl https://api.gatewayz.ai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_GATEWAYZ_API_KEY" \
  -d '{
    "model": "openrouter/auto",
    "messages": [
      {
        "role": "user",
        "content": "Hello, what model are you?"
      }
    ]
  }'
```

### Using Python (with dependencies installed):

```python
from openai import OpenAI

# Using Gatewayz API
client = OpenAI(
    api_key="YOUR_GATEWAYZ_API_KEY",
    base_url="https://api.gatewayz.ai/v1"
)

response = client.chat.completions.create(
    model="openrouter/auto",
    messages=[
        {"role": "user", "content": "What is 2+2?"}
    ]
)

print(f"Model used: {response.model}")
print(f"Response: {response.choices[0].message.content}")
```

## What to Expect

### Auto-Routing Behavior

OpenRouter's `auto` model will intelligently route your request to the most appropriate model based on:

1. **Prompt complexity**: Simple questions might go to faster models, complex reasoning to more capable ones
2. **Required capabilities**: Tasks needing vision, code, or math route to specialized models
3. **Context length**: Long prompts route to models with larger context windows
4. **Cost optimization**: Balances performance with cost efficiency

### Model Pool (as of November 2025)

The auto router may select from:
- OpenAI: GPT-5, GPT-5-mini, GPT-5-nano, GPT-4.1 series, GPT-4o-mini
- Anthropic: Claude Opus 4.1, Claude Sonnet 4.0, Claude 3.7 Sonnet, Claude 3.5 Haiku
- Google: Gemini 2.5 Pro, Gemini 2.5 Flash
- Mistral: Large, Medium, Small variants
- X.AI: Grok 3, Grok 3-mini, Grok 4
- DeepSeek: R1
- Meta: Llama 3.1 70B, Llama 3.1 405B
- And more...

### Pricing

- The response will be billed at the rate of whichever model it routes to
- You can see which model was used in the `model` field of the response
- Check [OpenRouter Activity](https://openrouter.ai/activity) for detailed routing logs

## Validation Scripts

### Available Test Scripts

1. **`test_openrouter_auto_curl.sh`** (Recommended)
   - No Python dependencies needed
   - Tests OpenRouter API directly
   - Shows which models are selected

   ```bash
   export OPENROUTER_API_KEY='your-key'
   ./test_openrouter_auto_curl.sh
   ```

2. **`scripts/validation/test_openrouter_auto_simple.py`**
   - Validates model exists in OpenRouter catalog
   - No API key needed

   ```bash
   python3 scripts/validation/test_openrouter_auto_simple.py
   ```

3. **`scripts/validation/test_openrouter_auto_transformations.py`**
   - Tests model transformation logic
   - Tests provider detection
   - Tests fallback routing

   ```bash
   python3 scripts/validation/test_openrouter_auto_transformations.py
   ```

4. **`scripts/validation/validate_openrouter_auto.py`**
   - Comprehensive end-to-end validation
   - Requires full dependencies and API key

   ```bash
   python3 scripts/validation/validate_openrouter_auto.py
   ```

## Troubleshooting

### "Model not found" Error

If you get a model not found error:
1. Verify `openrouter/auto` is in the model catalog: `GET /v1/models`
2. Check that OpenRouter provider is enabled
3. Ensure `OPENROUTER_API_KEY` is set

### "Invalid API key" Error

1. Get a valid API key from https://openrouter.ai/keys
2. Set it in your environment: `export OPENROUTER_API_KEY='sk-or-...'`
3. For Gatewayz, ensure the key is in your config/environment

### Model ID Format Issues

The correct formats are:
- ✅ `openrouter/auto` (lowercase)
- ✅ `OpenRouter/Auto` (will be normalized)
- ❌ `auto` (needs the prefix)
- ❌ `or/auto` (wrong prefix)

## Integration Examples

### Example 1: Simple Chat

```bash
curl https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAYZ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/auto",
    "messages": [{"role": "user", "content": "Explain quantum computing simply."}]
  }'
```

### Example 2: Code Generation

```bash
curl https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAYZ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/auto",
    "messages": [
      {"role": "user", "content": "Write a Python function to calculate fibonacci numbers."}
    ]
  }'
```

### Example 3: Reasoning Task

```bash
curl https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAYZ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/auto",
    "messages": [
      {"role": "user", "content": "Solve this logic puzzle: Three boxes, one contains gold..."}
    ]
  }'
```

## Benefits of Using OpenRouter Auto

1. **Optimal Selection**: Always get routed to the best model for your specific task
2. **Cost Efficiency**: Automatically balances performance with cost
3. **Latest Models**: Access to newest models as they become available
4. **Simplified API**: One model ID works for all use cases
5. **Transparent Routing**: Response shows which model was selected

## Learn More

- OpenRouter Auto Documentation: https://openrouter.ai/docs/model-routing
- Not Diamond (routing engine): https://docs.notdiamond.ai/
- Model Activity Logs: https://openrouter.ai/activity

---

**Last Updated**: November 27, 2025
