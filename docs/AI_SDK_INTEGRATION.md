# Vercel AI SDK Integration

## Overview

This document describes the integration of Vercel AI SDK support with the Gatewayz Universal Inference API. The integration provides a dedicated endpoint for AI SDK-compatible requests, routing them through our OpenRouter integration for model execution.

## What is the Vercel AI SDK?

The [Vercel AI SDK](https://ai-sdk.dev/) is a TypeScript/JavaScript toolkit for building AI-powered applications. It provides:

- **Unified Interface**: Single API for accessing models from multiple providers
- **Framework Support**: Works with React, Next.js, Vue, Svelte, Node.js, and more
- **Features**: Chat completions, structured data generation, tool calling, streaming, and more
- **Provider Agnostic**: Supports OpenAI, Anthropic, Google, xAI, and 15+ others

Since the Vercel AI SDK is primarily a TypeScript/JavaScript library, this Python backend provides compatibility through a dedicated endpoint.

## Architecture

### Request Flow

```
┌─────────────────────┐
│  AI SDK Client      │
│  (TypeScript/JS)    │
└──────────┬──────────┘
           │ POST /api/chat/ai-sdk
           ▼
┌──────────────────────────────────┐
│  Gatewayz API Gateway (Python)   │
├──────────────────────────────────┤
│ Route: /api/chat/ai-sdk          │
│ Handler: routes/ai_sdk.py        │
│ Service: services/ai_sdk_client  │
└──────────┬───────────────────────┘
           │ OpenAI-compatible request
           ▼
┌──────────────────────────────────┐
│  OpenRouter (OpenAI-compatible)   │
│  Base URL: api.openrouter.ai      │
└──────────┬───────────────────────┘
           │ Model inference
           ▼
┌──────────────────────────────────┐
│  AI Model Providers              │
│  - OpenAI (GPT-4, GPT-3.5)       │
│  - Anthropic (Claude)            │
│  - Meta (Llama)                  │
│  - And 15+ more                  │
└──────────────────────────────────┘
```

## Endpoint

### POST /api/chat/ai-sdk

**Description**: AI SDK-compatible chat completion endpoint

**URL**: `https://api.gatewayz.ai/api/chat/ai-sdk`

**Authentication**: Via `AI_SDK_API_KEY` environment variable (backend configuration)

### Request Format

```json
{
  "model": "gpt-4",
  "messages": [
    {
      "role": "user",
      "content": "Hello, how can you help me?"
    }
  ],
  "max_tokens": 1024,
  "temperature": 0.7,
  "top_p": 0.9,
  "frequency_penalty": 0,
  "presence_penalty": 0,
  "stream": false
}
```

**Request Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | Yes | Model identifier (e.g., "gpt-4", "claude-3-opus") |
| `messages` | array | Yes | Array of message objects with `role` and `content` |
| `max_tokens` | integer | No | Maximum tokens to generate (1-4096) |
| `temperature` | number | No | Sampling temperature (0.0-2.0), default 1.0 |
| `top_p` | number | No | Top-p sampling (0.0-1.0) |
| `frequency_penalty` | number | No | Frequency penalty (-2.0 to 2.0) |
| `presence_penalty` | number | No | Presence penalty (-2.0 to 2.0) |
| `stream` | boolean | No | Enable streaming response, default false |

### Response Format (Non-Streaming)

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "I'd be happy to help! What would you like assistance with?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 18,
    "total_tokens": 33
  }
}
```

### Response Format (Streaming)

Streaming responses use Server-Sent Events (SSE) format:

```
data: {"choices":[{"delta":{"role":"assistant","content":"I"}}]}

data: {"choices":[{"delta":{"role":"assistant","content":"'d"}}]}

data: {"choices":[{"delta":{"role":"assistant","content":" be"}}]}

...

data: {"choices":[{"finish_reason":"stop"}]}

data: [DONE]
```

## Configuration

### Environment Variables

**Required**:
- `AI_SDK_API_KEY`: Your OpenRouter API key for model access

**Optional**:
- Set in your deployment environment (Railway, Vercel, Docker, etc.)

### Examples

#### Local Development
```bash
export AI_SDK_API_KEY="sk-or-..."
python src/main.py
```

#### Railway
Add environment variable in Railway dashboard:
```
AI_SDK_API_KEY=sk-or-...
```

#### Vercel
Add to `vercel.json` or environment variables:
```json
{
  "env": {
    "AI_SDK_API_KEY": "@ai_sdk_api_key"
  }
}
```

#### Docker
```dockerfile
ENV AI_SDK_API_KEY=sk-or-...
```

## Usage Examples

### Python Backend Example

```python
import httpx
import asyncio

async def chat_with_ai_sdk():
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.gatewayz.ai/api/chat/ai-sdk",
            json={
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": "Explain quantum computing"}
                ],
                "max_tokens": 500,
                "temperature": 0.7
            }
        )

        result = response.json()
        print(result["choices"][0]["message"]["content"])

asyncio.run(chat_with_ai_sdk())
```

### Node.js/TypeScript Example

Since the Vercel AI SDK is primarily TypeScript, you'd typically use it directly:

```typescript
import { openai } from '@ai-sdk/openai';
import { generateText } from 'ai';

const { text } = await generateText({
  model: openai('gpt-4'),
  messages: [
    { role: 'user', content: 'Explain quantum computing' }
  ],
});

console.log(text);
```

However, you can also call the Gatewayz endpoint directly:

```typescript
const response = await fetch(
  'https://api.gatewayz.ai/api/chat/ai-sdk',
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'gpt-4',
      messages: [
        { role: 'user', content: 'Explain quantum computing' }
      ],
      max_tokens: 500
    })
  }
);

const data = await response.json();
console.log(data.choices[0].message.content);
```

### Streaming Example

```python
import httpx
import json

def stream_chat():
    with httpx.stream(
        "POST",
        "https://api.gatewayz.ai/api/chat/ai-sdk",
        json={
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Tell a story"}],
            "stream": True
        }
    ) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "delta" in data.get("choices", [{}])[0]:
                    print(data["choices"][0]["delta"]["content"], end="", flush=True)
```

## Supported Models

Through the OpenRouter integration, the following model categories are supported:

### OpenAI Models
- `gpt-4`
- `gpt-4-turbo`
- `gpt-4-32k`
- `gpt-3.5-turbo`
- `gpt-3.5-turbo-16k`

### Anthropic Models
- `claude-3-opus`
- `claude-3-sonnet`
- `claude-3-haiku`
- `claude-2.1`
- `claude-2`
- `claude-instant`

### Meta Models
- `llama-2-70b`
- `llama-2-13b`
- `llama-2-7b`

### Other Models
- `mistral-large`
- `mistral-medium`
- `mistral-small`
- `mixtral-8x7b`
- `neural-chat-7b`
- `and 100+ more..`

See `/v1/catalog/models` endpoint for a complete, up-to-date list of available models.

## Error Handling

### Configuration Error (500)
```json
{
  "detail": "AI_SDK_API_KEY not configured"
}
```

**Fix**: Ensure `AI_SDK_API_KEY` environment variable is set.

### Invalid Request (422)
```json
{
  "detail": [
    {
      "loc": ["body", "messages"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

**Fix**: Ensure all required fields (`model`, `messages`) are provided.

### Model Not Found (400)
```json
{
  "detail": "Model not found in provider"
}
```

**Fix**: Check `/v1/catalog/models` for available models.

### API Error (500)
```json
{
  "detail": "Failed to process AI SDK request"
}
```

**Fix**: Check logs for detailed error information. May indicate provider API issues.

## Features

### ✅ Supported
- [x] Chat completions (non-streaming and streaming)
- [x] Multiple message roles (system, user, assistant)
- [x] Token counting (via usage field)
- [x] Temperature and top-p sampling
- [x] Frequency and presence penalties
- [x] Max tokens parameter
- [x] Server-Sent Events (SSE) streaming
- [x] Error handling with detailed messages

### 🔄 In Development
- [ ] Function calling / Tool use
- [ ] Vision/image support
- [ ] Embeddings endpoint
- [ ] Batch processing

### ❌ Not Supported (API SDK limitation)
- File uploads (use /v1/images for image generation instead)
- Fine-tuning (use provider directly)
- Organization-level API keys

## Implementation Details

### Files Modified/Created

1. **src/config/config.py**
   - Added `AI_SDK_API_KEY` configuration variable

2. **src/services/ai_sdk_client.py** (new)
   - Core AI SDK client implementation
   - OpenRouter integration
   - Request/response processing

3. **src/routes/ai_sdk.py** (new)
   - HTTP endpoint handler
   - Request validation
   - Streaming support
   - Error handling

4. **src/main.py**
   - Registered AI SDK route with app

5. **tests/routes/test_ai_sdk.py** (new)
   - Comprehensive test suite
   - Mocking and validation tests

### Design Decisions

1. **OpenRouter as Backend**: OpenRouter provides the widest selection of models and best compatibility with the OpenAI chat completion format
2. **Dedicated Endpoint**: Separate endpoint allows specific AI SDK handling and makes debugging easier
3. **Streaming Support**: Full streaming support via SSE for real-time responses
4. **Error Messages**: Detailed error messages help users quickly identify configuration issues

## Troubleshooting

### Issue: "AI_SDK_API_KEY not configured"
**Solution**: Ensure the environment variable is set in your deployment:
```bash
export AI_SDK_API_KEY="your-openrouter-api-key"
```

### Issue: 500 Error on Request
**Solution**: Check application logs for detailed error message. Common causes:
- Invalid API key
- Model not available through OpenRouter
- Network connectivity issues

### Issue: Streaming Not Working
**Solution**: Ensure `stream: true` is set in request and client supports Server-Sent Events.

## Performance Considerations

- **Latency**: Typically 1-5 seconds depending on model
- **Timeout**: Set to 60 seconds by default
- **Rate Limiting**: Depends on OpenRouter plan
- **Concurrency**: Unlimited concurrent requests (depends on backend)

## Security Notes

- The `AI_SDK_API_KEY` should never be exposed in frontend code
- Keep API key secret in environment variables
- Monitor usage regularly via the OpenRouter dashboard
- Consider rate limiting for public deployments

## Testing

Run the test suite:

```bash
# All AI SDK tests
pytest tests/routes/test_ai_sdk.py -v

# Specific test
pytest tests/routes/test_ai_sdk.py::TestAISDKEndpoint::test_ai_sdk_chat_completion_success -v

# With coverage
pytest tests/routes/test_ai_sdk.py --cov=src.routes.ai_sdk --cov=src.services.ai_sdk_client
```

## Related Documentation

- [OpenRouter Documentation](https://openrouter.ai/docs)
- [Vercel AI SDK Docs](https://ai-sdk.dev/docs)
- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat/create)
- [Gatewayz API Documentation](./api.md)

## Support

For issues or questions:
1. Check the [troubleshooting](#troubleshooting) section above
2. Review application logs for error details
3. Verify environment variables are correctly set
4. Check OpenRouter provider status dashboard

---

**Last Updated**: November 2024
**Version**: 1.0
**Status**: Production Ready
