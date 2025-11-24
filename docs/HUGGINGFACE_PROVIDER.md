# Gatewayz as HuggingFace Inference Provider

This document outlines the implementation of Gatewayz as an inference provider on the HuggingFace Hub.

## Overview

Gatewayz implements the HuggingFace Inference Provider API standard, enabling users to access Gatewayz models directly through HuggingFace Hub's unified inference interface. This integration provides:

- **Multi-provider access**: Route requests across 15+ AI providers (OpenRouter, Portkey, Featherless, etc.)
- **Standard task APIs**: Support for text-generation, conversational, summarization, translation, and more
- **Billing integration**: Nano-USD precision billing and usage tracking
- **Model mapping**: Discover and map models between Gatewayz and HuggingFace catalogs
- **Client libraries**: Official Python and JavaScript/TypeScript SDKs

## Architecture

### API Endpoints

All HuggingFace task endpoints are available under the `/hf/tasks/` namespace:

```
/hf/tasks/
├── text-generation        # Text generation endpoint
├── conversational         # Conversational/chat endpoint
├── summarization          # Text summarization
├── translation            # Text translation
├── question-answering     # QA endpoint
├── run                    # Generic task runner
├── models                 # List available models
├── models/map             # Register model mappings
├── billing/cost           # Calculate request costs
└── billing/usage          # Get usage records
```

### Task Types Supported

1. **text-generation** - Generate text from prompts
2. **conversational** - Multi-turn conversation support
3. **summarization** - Summarize documents
4. **translation** - Translate between languages
5. **question-answering** - Answer questions based on context
6. **text-classification** - Classify text by category
7. **token-classification** - Named entity recognition
8. **image-generation** - Generate images from text
9. **embedding** - Generate embeddings for text

## API Implementation Details

### 1. Text Generation

**Endpoint**: `POST /hf/tasks/text-generation`

**Request**:
```json
{
  "inputs": "The future of AI is",
  "parameters": {
    "max_tokens": 100,
    "temperature": 0.7,
    "model": "gpt-3.5-turbo"
  }
}
```

**Response**:
```json
{
  "output": [
    {
      "generated_text": "The future of AI is..."
    }
  ]
}
```

**Streaming**: Pass `stream=true` to enable streaming responses.

### 2. Conversational

**Endpoint**: `POST /hf/tasks/conversational`

Maintains conversation history automatically:

**Request**:
```json
{
  "text": "What is AI?",
  "past_user_inputs": ["Hello"],
  "generated_responses": ["Hi there!"]
}
```

**Response**:
```json
{
  "conversation": {
    "past_user_inputs": ["Hello", "What is AI?"],
    "generated_responses": ["Hi there!", "AI is..."]
  }
}
```

### 3. Summarization

**Endpoint**: `POST /hf/tasks/summarization`

**Request**:
```json
{
  "inputs": "Long document text here..."
}
```

**Response**:
```json
{
  "output": {
    "summary_text": "Summarized text..."
  }
}
```

### 4. Generic Task Runner

**Endpoint**: `POST /hf/tasks/run`

**Request**:
```json
{
  "task": "text-generation",
  "model": "gpt-3.5-turbo",
  "inputs": "Hello",
  "parameters": {...}
}
```

## Billing System

### Nano-USD Precision

All costs are tracked in **nano-USD** (1 nano-USD = 10^-9 USD) for maximum precision:

- Prevents floating-point rounding errors
- Ensures accurate billing down to the nanosecond dollar
- Standard across HuggingFace provider network

### Cost Calculation Endpoint

**Endpoint**: `POST /hf/tasks/billing/cost`

Calculate costs before making requests:

**Request**:
```json
{
  "requests": [
    {
      "task": "text-generation",
      "model": "gpt-3.5-turbo",
      "input_tokens": 100,
      "output_tokens": 50
    }
  ]
}
```

**Response**:
```json
{
  "total_cost_nano_usd": 500000000,
  "costs": [
    {
      "task": "text-generation",
      "model": "gpt-3.5-turbo",
      "input_tokens": 100,
      "output_tokens": 50,
      "cost_nano_usd": 500000000,
      "cost_usd": 0.0000005
    }
  ],
  "currency": "USD"
}
```

### Usage Tracking

**Endpoint**: `GET /hf/tasks/billing/usage`

Retrieve usage records for billing:

**Query Parameters**:
- `limit`: Max records (default: 100)
- `offset`: Pagination offset (default: 0)

**Response**:
```json
{
  "records": [
    {
      "request_id": "uuid",
      "timestamp": "2025-01-15T10:30:00Z",
      "task": "text-generation",
      "model": "gpt-3.5-turbo",
      "input_tokens": 100,
      "output_tokens": 50,
      "cost_nano_usd": 500000000
    }
  ],
  "total_records": 1,
  "total_cost_nano_usd": 500000000
}
```

## Model Mapping

### Why Model Mapping?

Gatewayz supports multiple provider models, while HuggingFace Hub uses a unified model registry. Model mapping allows:

1. **Discovery** - Users find Gatewayz models on Hub
2. **Routing** - Requests route to correct provider/model
3. **Compatibility** - Map provider models to Hub equivalents

### Model Mapping Endpoint

**Endpoint**: `POST /hf/tasks/models/map`

**Request**:
```json
{
  "provider_model_id": "gpt-3.5-turbo",
  "hub_model_id": "meta-llama/Llama-2-7b-chat",
  "task_type": "text-generation",
  "parameters": {
    "temperature": 0.7,
    "max_tokens": 2048
  }
}
```

**Response**:
```json
{
  "success": true,
  "provider_model_id": "gpt-3.5-turbo",
  "hub_model_id": "meta-llama/Llama-2-7b-chat",
  "message": "Model mapping registered successfully"
}
```

### List Models

**Endpoint**: `GET /hf/tasks/models`

**Query Parameters**:
- `task_type`: Filter by task type (optional)

**Response**:
```json
{
  "models": [
    {
      "model_id": "gpt-3.5-turbo",
      "hub_model_id": "meta-llama/Llama-2-7b-chat",
      "task_type": "text-generation"
    }
  ],
  "count": 50,
  "task_types": ["text-generation", "image-generation"]
}
```

## Client Libraries

### Python Client

**Installation**:
```bash
pip install gatewayz-py-hf
```

**Usage**:
```python
from gatewayz_py_hf import AsyncGatewayzClient

async with AsyncGatewayzClient(api_key="your-key") as client:
    # Text generation
    response = await client.text_generation(
        inputs="Hello, world!",
        model="gpt-3.5-turbo"
    )

    # Calculate cost
    cost = await client.calculate_cost([
        {
            "task": "text-generation",
            "model": "gpt-3.5-turbo",
            "input_tokens": 10,
            "output_tokens": 20
        }
    ])

    # Get usage records
    usage = await client.get_usage(limit=50)
```

### JavaScript/TypeScript Client

**Installation**:
```bash
npm install gatewayz-js-hf
```

**Usage**:
```typescript
import { createClient } from "gatewayz-js-hf";

const client = createClient("your-api-key");

// Text generation
const response = await client.textGeneration("Hello, world!");

// Calculate cost
const cost = await client.calculateCost([
  {
    task: "text-generation",
    model: "gpt-3.5-turbo",
    input_tokens: 10,
    output_tokens: 20,
  },
]);

// Get usage
const usage = await client.getUsage(50, 0);
```

## Authentication

All requests must include the API key:

**Header**:
```
Authorization: Bearer YOUR_API_KEY
```

The API key is used to:
- Authenticate requests
- Track usage per user
- Enforce rate limits
- Manage billing

## Error Handling

### HTTP Status Codes

- `200` - Success
- `400` - Bad request (invalid parameters)
- `401` - Unauthorized (invalid API key)
- `403` - Forbidden (insufficient permissions)
- `404` - Not found
- `429` - Too many requests (rate limited)
- `500` - Server error

### Error Response Format

```json
{
  "error": "Error description",
  "error_type": "InvalidRequest",
  "message": "Additional details about the error"
}
```

## Rate Limiting

Gatewayz implements multi-level rate limiting:

1. **Per-API-Key**: Configurable limit per key
2. **Per-User**: Aggregate limit across all keys
3. **Global**: System-wide limit

Rate limit headers:

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1642339200
```

When rate limited (HTTP 429), retry after the `X-RateLimit-Reset` time.

## Integration Steps

To submit Gatewayz as a HuggingFace inference provider:

### 1. Account Setup
- [ ] Upgrade HuggingFace Hub account to Team or Enterprise plan
- [ ] Create organization profile on Hub

### 2. Implementation
- [ ] Implement task API endpoints (✅ Completed)
- [ ] Implement billing endpoint (✅ Completed)
- [ ] Implement model mapping (✅ Completed)
- [ ] Create Python client library (✅ Completed)
- [ ] Create JavaScript/TypeScript client library (✅ Completed)

### 3. Pull Requests
- [ ] Submit PR to HuggingFace `huggingface/transformers` with Python client integration
- [ ] Submit PR to HuggingFace `huggingface/chat-ui` with JavaScript client integration
- [ ] Link to official client library repositories

### 4. Documentation
- [ ] Create provider documentation (this file)
- [ ] Add provider to HuggingFace Hub docs
- [ ] Create integration guides for users
- [ ] Document all supported tasks and models

### 5. Branding
- [ ] Create SVG logo (light and dark versions)
- [ ] Prepare provider information/description
- [ ] Create provider assets

### 6. Testing & Validation
- [ ] Set up automated endpoint validation
- [ ] Test all task types
- [ ] Verify billing calculations
- [ ] Load test and performance validation
- [ ] Coordinate server-side registration with HuggingFace

## Monitoring & Analytics

### Health Checks

**Endpoint**: `GET /health`

Provides system status:

```json
{
  "status": "healthy",
  "uptime": 3600,
  "models_cached": 50,
  "provider_status": {
    "openrouter": "healthy",
    "portkey": "healthy"
  }
}
```

### Metrics

Exposed on `/metrics` (Prometheus format):
- Request count per task type
- Latency percentiles (p50, p95, p99)
- Error rates
- Provider availability
- Billing metrics

## Pricing

Gatewayz offers transparent pricing based on underlying provider rates:

- **OpenRouter**: Market rate aggregation
- **Portkey**: Provider selection
- **Other Providers**: Direct pricing

Prices are dynamically fetched and updated in real-time.

## Support & Contact

For questions or issues regarding HuggingFace integration:

- **Email**: support@gatewayz.io
- **GitHub Issues**: https://github.com/terragon-labs/gatewayz/issues
- **Discussion**: HuggingFace Hub discussions

## License

Gatewayz and its client libraries are released under the MIT License.

## Changelog

### v0.1.0 (2025-01-15)
- Initial HuggingFace provider implementation
- Task API endpoints
- Billing system with nano-USD precision
- Model mapping API
- Python and JavaScript client libraries
- Documentation

---

**Last Updated**: 2025-01-15
**Status**: Ready for HuggingFace Hub Integration
