# Infron AI Integration Guide

> **Note**: Infron AI was formerly known as OneRouter. Internal code identifiers still use `onerouter` for backward compatibility.

## Overview

Infron AI is a unified AI gateway that provides access to multiple AI models with automatic fallbacks, prompt caching, and multimodal support. It implements the OpenAI API specification, making it a drop-in replacement for OpenAI's endpoints.

## Key Features

- **OpenAI-Compatible API**: Fully compatible with OpenAI SDK and API format
- **Automatic Fallbacks**: Transparent failover to alternative providers if primary is unavailable
- **Prompt Caching**: Automatically enabled by default to reduce costs and improve performance
- **Multimodal Support**: Handles text, images (URLs or base64), with PDF and audio support coming soon
- **Streaming Support**: Real-time token delivery via Server-Sent Events (SSE)
- **Usage Tracking**: Optional detailed token usage statistics

## Architecture

### API Endpoints

- **Base URL**: `https://llm.infron.ai/v1`
- **Chat Completions**: `/chat/completions`
- **Models List**: `https://app.infron.ai/api/display_models/` (public, no auth required)

### Authentication

Infron AI uses Bearer token authentication:

```
Authorization: Bearer YOUR_API_KEY
```

## Installation

Infron AI client is integrated using the OpenAI Python SDK (already included in requirements.txt):

```bash
pip install openai>=1.44.0
```

## Configuration

### Environment Variables

Set your Infron AI API key:

```bash
export ONEROUTER_API_KEY=your_api_key_here
```

Or add it to your `.env` file:

```env
ONEROUTER_API_KEY=your_api_key_here
```

### Getting Your API Key

1. Create an account at [https://app.infron.ai/index](https://app.infron.ai/index)
2. Navigate to the API Keys section
3. Generate a new API key
4. Copy and securely store your key

## Usage

### 1. Basic Chat Completion

```python
from src.services.onerouter_client import (
    make_onerouter_request_openai,
    process_onerouter_response
)

messages = [
    {"role": "user", "content": "What is the capital of France?"}
]

# Make request
response = make_onerouter_request_openai(
    messages=messages,
    model="claude-3-5-sonnet@20240620",
    max_tokens=100,
    temperature=0.7
)

# Process response
processed = process_onerouter_response(response)
print(processed["choices"][0]["message"]["content"])
```

### 2. Streaming Chat Completion

```python
from src.services.onerouter_client import make_onerouter_request_openai_stream

messages = [
    {"role": "user", "content": "Write a short poem about coding"}
]

# Make streaming request
stream = make_onerouter_request_openai_stream(
    messages=messages,
    model="claude-3-5-sonnet@20240620",
    max_tokens=200
)

# Process stream
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### 3. Multimodal Request (Text + Image)

```python
messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "What's in this image?"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://example.com/image.jpg"
                }
            }
        ]
    }
]

response = make_onerouter_request_openai(
    messages=messages,
    model="claude-3-5-sonnet@20240620",
    max_tokens=300
)
```

### 4. Using with Additional Parameters

```python
response = make_onerouter_request_openai(
    messages=messages,
    model="gpt-4",
    max_tokens=500,
    temperature=0.8,
    top_p=0.9,
    frequency_penalty=0.5,
    presence_penalty=0.5,
    stop=["\n\n", "END"],
    user="user-123"
)
```

### 5. Fetching Available Models

```python
from src.services.onerouter_client import fetch_models_from_onerouter

models = fetch_models_from_onerouter()

for model in models:
    print(f"{model['id']}: {model['description']}")
```

## Model Format

Infron AI uses a specific model identifier format:

```
model-name@version
```

Examples:
- `claude-3-5-sonnet@20240620`
- `gpt-4@latest`
- `gpt-3.5-turbo@1106`

## Pricing

Infron AI pricing structure:
- **Base fee**: $0.35 + 5% on credit purchases
- **Per-model pricing**: Varies by model (prompt/completion token rates)
- **Discounts**: 20-80% available depending on provider
- **Caching costs**: Apply by default for all requests

View detailed pricing:
- In the Models tab at [https://app.infron.ai/models](https://app.infron.ai/models)
- In the Logs dashboard after making requests

## Special Features

### Prompt Caching

Prompt caching is **automatically enabled** by default across all API calls and **cannot be disabled**. This feature:
- Reduces costs for repeated prompts
- Improves response times
- Caching costs apply regardless of cache hits or misses

### Automatic Fallbacks

If a provider is unavailable, Infron AI automatically:
1. Detects the failure
2. Transparently switches to the next available provider
3. Completes the request without user intervention
4. Enhances production reliability

### Usage Tracking

To include token usage in responses:

```python
response = make_onerouter_request_openai(
    messages=messages,
    model="gpt-4",
    usage={"include": True}
)

# Access usage data
print(response.usage.prompt_tokens)
print(response.usage.completion_tokens)
print(response.usage.total_tokens)
```

## Integration with Gatewayz

Infron AI is integrated into Gatewayz as a provider client:

### File Structure

```
src/services/
├── onerouter_client.py          # Infron AI client implementation
└── connection_pool.py            # Connection pooling (get_onerouter_pooled_client)

src/config/
└── config.py                     # Configuration (ONEROUTER_API_KEY)

tests/services/
└── test_onerouter_client.py     # Unit tests
```

### Connection Pooling

Infron AI client uses connection pooling for improved performance:

```python
from src.services.connection_pool import get_onerouter_pooled_client

client = get_onerouter_pooled_client()
```

Benefits:
- ~10-20ms performance improvement per request
- Persistent HTTP connections
- Automatic keepalive
- Optimized timeout settings

## Error Handling

The Infron AI client includes comprehensive error handling:

```python
from src.services.onerouter_client import make_onerouter_request_openai

try:
    response = make_onerouter_request_openai(
        messages=messages,
        model="claude-3-5-sonnet@20240620"
    )
except ValueError as e:
    # Configuration error (missing API key)
    print(f"Config error: {e}")
except Exception as e:
    # API error (network, rate limit, etc.)
    print(f"API error: {e}")
```

Errors are automatically logged and sent to Sentry (if configured) with provider context.

## Testing

Run Infron AI client tests:

```bash
pytest tests/services/test_onerouter_client.py -v
```

Test coverage includes:
- Client initialization (with/without API key)
- Standard requests
- Streaming requests
- Response processing
- Error handling

## SDK Compatibility

Infron AI works with any OpenAI-compatible SDK:
- **OpenAI Python SDK** ✅ (used by Gatewayz)
- **LangChain** ✅
- **Anthropic SDK** ✅
- **LlamaIndex** ✅
- Any other OpenAI-compatible client ✅

## Monitoring & Observability

Infron AI integration includes:
- **Logging**: Comprehensive request/response logging
- **Sentry**: Error tracking with provider context
- **Metrics**: Request timing and success rates
- **Health checks**: Provider availability monitoring

## Best Practices

1. **API Key Security**: Never commit API keys to version control
2. **Error Handling**: Always wrap requests in try/except blocks
3. **Rate Limiting**: Implement appropriate rate limiting for your use case
4. **Model Selection**: Choose appropriate models for your task complexity
5. **Streaming**: Use streaming for long responses to improve user experience
6. **Caching**: Be aware that caching costs apply to all requests

## Troubleshooting

### API Key Not Configured

```
ValueError: Infron AI API key not configured
```

**Solution**: Set the `ONEROUTER_API_KEY` environment variable.

### HTTP Errors

```
HTTPStatusError: 401 Unauthorized
```

**Solution**: Verify your API key is valid and has sufficient credits.

### Timeout Errors

```
ReadTimeout: Request timed out
```

**Solution**: Infron AI uses 60s read timeout. For longer requests, responses may need adjustment or use streaming.

### Model Not Found

```
Error: Model not found
```

**Solution**: Verify the model identifier format (`model@version`) and check available models.

## Resources

- **Website**: [https://infron.ai/](https://infron.ai/)
- **Dashboard**: [https://app.infron.ai/](https://app.infron.ai/)
- **Models List**: [https://app.infron.ai/models](https://app.infron.ai/models)
- **API Keys**: [https://app.infron.ai/api-keys](https://app.infron.ai/api-keys)

## Support

For issues specific to:
- **Infron AI API**: Contact Infron AI support
- **Gatewayz Integration**: Open an issue in the Gatewayz repository

## Changelog

### 2026-01-24
- Rebranded from OneRouter to Infron AI
- Updated all API endpoints to infron.ai domain

### 2025-11-27
- Initial OneRouter integration
- Added client implementation with connection pooling
- Added streaming support
- Added model fetching capability
- Added comprehensive unit tests
- Added integration documentation
