# Model Provider Integration Guide

**Version**: 1.0
**Last Updated**: 2025-02-03
**Target Audience**: AI Model Providers integrating with Gatewayz Universal Inference API

---

## Overview

This guide outlines the requirements and information needed from AI model providers to successfully integrate with the Gatewayz Universal Inference API. Gatewayz is a production-grade API gateway that provides unified access to 100+ AI models from 30+ providers through OpenAI-compatible and Anthropic-compatible endpoints.

**Integration Benefits**:
- Exposure to Gatewayz's enterprise customer base
- Unified billing and credit management
- Advanced monitoring and observability
- Automatic failover and health monitoring
- Multi-provider routing for increased reliability

---

## Table of Contents

1. [Technical API Requirements](#1-technical-api-requirements)
2. [Model Metadata Requirements](#2-model-metadata-requirements)
3. [Pricing Information Requirements](#3-pricing-information-requirements)
4. [API Key & Authentication](#4-api-key--authentication)
5. [Response Format Standards](#5-response-format-standards)
6. [Testing & Validation](#6-testing--validation)
7. [Integration Checklist](#7-integration-checklist)
8. [Support & Contact](#8-support--contact)

---

## 1. Technical API Requirements

### 1.1 API Endpoint Information

Please provide the following information about your API:

**Required**:
- **Base URL**: Complete API endpoint URL (e.g., `https://api.yourprovider.com/v1`)
- **API Version**: Current API version and versioning scheme
- **Authentication Method**: How API keys are provided (e.g., Bearer token, custom header)
- **Request Timeout**: Typical response times for planning connection timeouts

**Example**:
```
Base URL: https://api.yourprovider.com/v1
Authentication: Bearer token in Authorization header
Typical Response Time: 2-5 seconds (non-streaming), < 500ms first token (streaming)
```

### 1.2 API Compatibility

Indicate your API's compatibility level:

- **OpenAI-Compatible**: Does your API follow OpenAI's request/response format for `/chat/completions`?
  - ✅ **Yes** - We can use standard OpenAI SDK integration
  - ❌ **No** - We'll need custom client implementation (please provide API documentation)

- **Streaming Support**: Do you support Server-Sent Events (SSE) streaming?
  - ✅ **Yes** - Preferred for better user experience
  - ❌ **No** - We'll use non-streaming requests

### 1.3 Request Format

If **not OpenAI-compatible**, provide detailed request format:

```json
{
  "model": "your-model-id",
  "messages": [
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7,
  "max_tokens": 1024
}
```

**Specify**:
- Required fields
- Optional parameters
- Parameter value ranges and defaults
- Any custom headers needed

### 1.4 Response Format

If **not OpenAI-compatible**, provide detailed response format:

**Non-Streaming Response**:
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "your-model-id",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello! How can I assist you today?"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30
  }
}
```

**Streaming Response** (SSE format):
```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1677652288,"model":"your-model-id","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1677652288,"model":"your-model-id","choices":[{"index":0,"delta":{"content":"!"},"finish_reason":null}]}

data: [DONE]
```

### 1.5 Error Format

Provide your error response format:

```json
{
  "error": {
    "message": "Invalid API key provided",
    "type": "invalid_request_error",
    "code": "invalid_api_key"
  }
}
```

**Document**:
- HTTP status codes used (400, 401, 403, 429, 500, etc.)
- Error message structure
- Rate limit error format (if different)

### 1.6 Supported Capabilities

Check all that apply to your API:

**Message Roles**:
- [ ] `user` - User messages
- [ ] `assistant` - AI assistant responses
- [ ] `system` - System instructions
- [ ] `developer` - Developer instructions (separate from system)
- [ ] `tool` - Tool/function call results

**Multimodal Support**:
- [ ] Text input/output (required)
- [ ] Image input (URL)
- [ ] Image input (base64)
- [ ] Image output/generation
- [ ] Audio input
- [ ] Audio output
- [ ] Video input

**Advanced Features**:
- [ ] Function/tool calling
- [ ] Streaming responses (SSE)
- [ ] Response caching
- [ ] Reasoning/chain-of-thought tokens
- [ ] Web search integration
- [ ] Document understanding

### 1.7 Rate Limits

Provide rate limiting information:

- **Requests per minute**: ___________
- **Tokens per minute**: ___________
- **Requests per day**: ___________
- **Rate limit headers**: What headers indicate rate limit status?
  ```
  X-RateLimit-Limit: 1000
  X-RateLimit-Remaining: 999
  X-RateLimit-Reset: 1677652288
  ```

---

## 2. Model Metadata Requirements

For each model you want to offer through Gatewayz, provide the following information:

### 2.1 Essential Model Information

**Required Fields**:

| Field | Description | Example |
|-------|-------------|---------|
| `id` | Unique model identifier | `"yourprovider/llama-3-70b"` |
| `name` | Human-readable display name | `"Llama 3 70B"` |
| `description` | Brief model description (1-2 sentences) | `"Meta's Llama 3 70B model optimized for instruction following and chat."` |
| `context_length` | Maximum context window in tokens | `8192` |

**Example**:
```json
{
  "id": "yourprovider/llama-3-70b",
  "name": "Llama 3 70B",
  "description": "Meta's Llama 3 70B model optimized for instruction following and chat.",
  "context_length": 8192
}
```

### 2.2 Model Architecture Information

**Required**:

| Field | Description | Values |
|-------|-------------|--------|
| `modality` | Input/output type | `"text->text"`, `"text->image"`, `"multimodal"` |
| `input_modalities` | Supported input types | `["text"]`, `["text", "image"]`, etc. |
| `output_modalities` | Supported output types | `["text"]`, `["image"]`, etc. |

**Optional**:

| Field | Description | Example |
|-------|-------------|---------|
| `tokenizer` | Tokenizer used | `"tiktoken/cl100k_base"`, `"sentencepiece"` |
| `instruct_type` | Instruction format | `"chat"`, `"completion"`, `"instruct"` |

**Modality Types**:
- `"text->text"` - Standard text language model
- `"text->image"` - Image generation model
- `"text->audio"` - Text-to-speech / audio generation
- `"audio->text"` - Speech-to-text / transcription
- `"multimodal"` - Multiple input/output types

**Example**:
```json
{
  "architecture": {
    "modality": "text->text",
    "input_modalities": ["text"],
    "output_modalities": ["text"],
    "tokenizer": "sentencepiece",
    "instruct_type": "chat"
  }
}
```

### 2.3 Supported Parameters

List all request parameters your model supports:

**Common Parameters**:
- [ ] `max_tokens` - Maximum tokens to generate
- [ ] `temperature` - Sampling temperature (0-2)
- [ ] `top_p` - Nucleus sampling parameter
- [ ] `top_k` - Top-k sampling parameter
- [ ] `frequency_penalty` - Frequency penalty (-2 to 2)
- [ ] `presence_penalty` - Presence penalty (-2 to 2)
- [ ] `stop` - Stop sequences
- [ ] `stream` - Enable streaming responses
- [ ] `n` - Number of completions to generate
- [ ] `logprobs` - Return log probabilities
- [ ] `seed` - Random seed for reproducibility

**Default Values** (if applicable):
```json
{
  "default_parameters": {
    "temperature": 0.7,
    "max_tokens": 1024,
    "top_p": 1.0
  }
}
```

### 2.4 Model Metadata Template

Please provide this information for **each model**:

```json
{
  "id": "yourprovider/model-name",
  "name": "Display Name",
  "description": "Brief model description (1-2 sentences).",
  "context_length": 8192,
  "architecture": {
    "modality": "text->text",
    "input_modalities": ["text"],
    "output_modalities": ["text"],
    "tokenizer": "tiktoken/cl100k_base",
    "instruct_type": "chat"
  },
  "supported_parameters": [
    "max_tokens",
    "temperature",
    "top_p",
    "stream",
    "stop"
  ],
  "default_parameters": {
    "temperature": 0.7,
    "max_tokens": 1024
  }
}
```

---

## 3. Pricing Information Requirements

### 3.1 Pricing Format

**CRITICAL**: All pricing must be provided in **per-token format** (dollars per single token), **NOT** per-million-tokens.

**Required Pricing Fields**:

| Field | Description | Example |
|-------|-------------|---------|
| `prompt` | Cost per input token (USD) | `"0.0000001"` ($0.10 per 1M tokens) |
| `completion` | Cost per output token (USD) | `"0.0000002"` ($0.20 per 1M tokens) |
| `request` | Cost per request (USD, if applicable) | `"0"` or `"0.001"` |
| `image` | Cost per image (USD, if applicable) | `"0"` or `"0.02"` |

**Optional Pricing Fields**:

| Field | Description | Example |
|-------|-------------|---------|
| `web_search` | Cost per web search operation | `"0"` or `"0.005"` |
| `internal_reasoning` | Cost per reasoning token | `"0"` or `"0.0000001"` |

**Important Notes**:
- Use **string values** (not numbers) to maintain precision: `"0.0000001"` not `0.0000001`
- All pricing fields must be present (use `"0"` if not applicable)
- **Never use `-1` or dynamic pricing** - provide actual fixed rates
- Pricing should be consistent and not change based on usage patterns

### 3.2 Pricing Conversion

If your pricing is in different units, convert to per-token format:

**From per-1M tokens** (most common):
```
per_token = per_million_tokens / 1,000,000

Example: $0.10 per 1M tokens = 0.10 / 1,000,000 = 0.0000001
```

**From cents per token**:
```
per_token = cents / 100

Example: $0.01 per token = 0.01 / 100 = 0.0001
```

**Image generation** (per-second or per-image):
```
per_image = cents_per_second * average_generation_seconds / 100

Example: $0.02 per image = "0.02"
```

### 3.3 Pricing Example

```json
{
  "id": "yourprovider/llama-3-70b",
  "pricing": {
    "prompt": "0.0000001",
    "completion": "0.0000002",
    "request": "0",
    "image": "0",
    "web_search": "0",
    "internal_reasoning": "0"
  }
}
```

### 3.4 Free Models

**Important**: Only OpenRouter has legitimately free models (with `:free` suffix). If you offer free models:

1. Set all pricing fields to `"0"`
2. Clearly indicate this is a **promotional/trial tier** with limitations
3. Provide documentation on limitations (rate limits, usage caps, etc.)
4. Notify Gatewayz team for manual verification

**Free Model Example**:
```json
{
  "id": "yourprovider/free-model",
  "pricing": {
    "prompt": "0",
    "completion": "0",
    "request": "0",
    "image": "0",
    "web_search": "0",
    "internal_reasoning": "0"
  }
}
```

---

## 4. API Key & Authentication

### 4.1 API Key Provisioning

To integrate your provider, we need:

**API Key Details**:
- **Format**: How are API keys formatted? (e.g., `sk-...`, UUID, custom)
- **Header Name**: How is the key sent? (e.g., `Authorization: Bearer <key>`)
- **Rotation Policy**: Do keys expire? How often should they be rotated?
- **Test Key**: Please provide a test/sandbox API key for integration testing

**Example**:
```
Format: sk-yourprovider-32-character-string
Header: Authorization: Bearer sk-yourprovider-...
Expiration: Keys do not expire (manual rotation recommended every 90 days)
Test Key: sk-yourprovider-test-abc123xyz789
```

### 4.2 Authentication Requirements

Specify any additional authentication requirements:

- **IP Allowlisting**: Do we need to provide IPs to allowlist?
- **Domain Restrictions**: Any domain restrictions on API usage?
- **User-Agent Requirements**: Do you require specific User-Agent headers?
- **Custom Headers**: Any custom headers required for all requests?

**Example Custom Headers**:
```http
Authorization: Bearer sk-yourprovider-...
X-Provider-Client-ID: gatewayz
User-Agent: Gatewayz/2.0.3
```

---

## 5. Response Format Standards

### 5.1 Token Usage Reporting

**CRITICAL**: Accurate token usage is required for billing. Your API must return:

```json
{
  "usage": {
    "prompt_tokens": 50,
    "completion_tokens": 100,
    "total_tokens": 150
  }
}
```

**Required**:
- `prompt_tokens` - Input tokens consumed
- `completion_tokens` - Output tokens generated
- `total_tokens` - Sum of prompt and completion tokens

**Optional but Recommended**:
- `internal_reasoning_tokens` - Tokens used for internal reasoning (e.g., o1 models)
- `cached_tokens` - Tokens served from cache (if caching supported)

### 5.2 Finish Reasons

Standard finish reasons your API should return:

| Finish Reason | Description |
|---------------|-------------|
| `stop` | Natural completion (hit stop sequence or completed) |
| `length` | Max tokens reached |
| `content_filter` | Content filtered by safety systems |
| `tool_calls` | Model wants to call a function/tool |
| `error` | Error occurred during generation |

**Example**:
```json
{
  "choices": [{
    "finish_reason": "stop",
    "message": {
      "role": "assistant",
      "content": "Response text"
    }
  }]
}
```

### 5.3 Streaming Response Format

For streaming responses, use Server-Sent Events (SSE) format:

```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1677652288,"model":"yourprovider/model","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1677652288,"model":"yourprovider/model","choices":[{"index":0,"delta":{"content":" there"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1677652288,"model":"yourprovider/model","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}

data: [DONE]
```

**Requirements**:
- Each chunk prefixed with `data: `
- JSON-formatted chunk data
- `delta` field contains incremental content
- Final chunk includes `usage` and `finish_reason`
- End with `data: [DONE]`

---

## 6. Testing & Validation

### 6.1 Test Scenarios

Before integration, please validate these scenarios:

**Basic Chat Completion**:
```bash
curl https://api.yourprovider.com/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "yourprovider/model-name",
    "messages": [{"role": "user", "content": "Say hello"}],
    "max_tokens": 50
  }'
```

**Streaming Chat Completion**:
```bash
curl https://api.yourprovider.com/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "yourprovider/model-name",
    "messages": [{"role": "user", "content": "Say hello"}],
    "stream": true,
    "max_tokens": 50
  }'
```

**Error Handling** (invalid API key):
```bash
curl https://api.yourprovider.com/v1/chat/completions \
  -H "Authorization: Bearer invalid-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "yourprovider/model-name",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 6.2 Validation Checklist

Before submitting for integration, verify:

**API Functionality**:
- [ ] Chat completion endpoint working
- [ ] Streaming endpoint working (if supported)
- [ ] Token usage accurately reported
- [ ] Error responses properly formatted
- [ ] Rate limiting headers present

**Model Metadata**:
- [ ] All models have complete metadata
- [ ] Context lengths accurate
- [ ] Model descriptions clear and concise
- [ ] Supported parameters documented

**Pricing**:
- [ ] All pricing in per-token format
- [ ] All pricing fields present
- [ ] Pricing matches your public rates
- [ ] No dynamic pricing (`-1` values)

**Authentication**:
- [ ] Test API key provided
- [ ] Authentication method documented
- [ ] Custom headers specified (if any)

---

## 7. Integration Checklist

Use this checklist to ensure you've provided all required information:

### Technical Requirements
- [ ] Base URL provided
- [ ] Authentication method documented
- [ ] API compatibility level specified (OpenAI-compatible vs. custom)
- [ ] Request format documented (if custom)
- [ ] Response format documented (if custom)
- [ ] Error format documented
- [ ] Rate limits specified
- [ ] Test API key provided

### Model Metadata
- [ ] Model IDs provided for all models
- [ ] Model names and descriptions provided
- [ ] Context lengths specified
- [ ] Architecture/modality information provided
- [ ] Supported parameters documented
- [ ] Default parameters specified (if applicable)

### Pricing Information
- [ ] Pricing in per-token format (not per-1M)
- [ ] All pricing fields present for each model
- [ ] String values used (not numbers)
- [ ] No dynamic pricing (no `-1` values)
- [ ] Free models clearly marked (if applicable)

### Validation
- [ ] Basic chat completion tested
- [ ] Streaming tested (if supported)
- [ ] Token usage accurate
- [ ] Error handling validated
- [ ] Rate limiting tested

---

## 8. Support & Contact

### Integration Process

1. **Submit Integration Request**: Email all required information to integrations@gatewayz.ai
2. **Technical Review**: Our team will review your submission (1-2 business days)
3. **Integration Development**: We'll implement and test the integration (3-5 business days)
4. **Validation**: You'll validate the integration in our staging environment
5. **Production Deployment**: We'll deploy to production and monitor initial traffic
6. **Documentation**: We'll add your provider to our public documentation

### Required Information Format

Please submit information as:
- **JSON files** for model metadata and pricing
- **API documentation** (OpenAPI/Swagger preferred, or PDF/markdown)
- **Test credentials** (API keys, sandbox environment access)

### Questions?

For integration support:
- **Email**: integrations@gatewayz.ai
- **Technical Documentation**: https://docs.gatewayz.ai
- **API Reference**: https://api.gatewayz.ai/docs

---

## Appendix: Complete Integration Template

Use this template to provide all required information:

```json
{
  "provider_info": {
    "name": "Your Provider Name",
    "slug": "yourprovider",
    "website": "https://yourprovider.com",
    "contact_email": "support@yourprovider.com"
  },
  "api_info": {
    "base_url": "https://api.yourprovider.com/v1",
    "authentication": {
      "type": "bearer_token",
      "header": "Authorization",
      "format": "Bearer {token}"
    },
    "openai_compatible": true,
    "streaming_supported": true,
    "typical_response_time_ms": 3000,
    "rate_limits": {
      "requests_per_minute": 1000,
      "tokens_per_minute": 100000
    }
  },
  "models": [
    {
      "id": "yourprovider/model-name",
      "name": "Model Display Name",
      "description": "Brief model description.",
      "context_length": 8192,
      "architecture": {
        "modality": "text->text",
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": "sentencepiece",
        "instruct_type": "chat"
      },
      "pricing": {
        "prompt": "0.0000001",
        "completion": "0.0000002",
        "request": "0",
        "image": "0",
        "web_search": "0",
        "internal_reasoning": "0"
      },
      "supported_parameters": [
        "max_tokens",
        "temperature",
        "top_p",
        "stream",
        "stop"
      ],
      "default_parameters": {
        "temperature": 0.7,
        "max_tokens": 1024
      }
    }
  ],
  "test_credentials": {
    "api_key": "sk-yourprovider-test-...",
    "test_model": "yourprovider/test-model"
  }
}
```

---

**Thank you for integrating with Gatewayz!** We're excited to bring your models to our enterprise customers.
