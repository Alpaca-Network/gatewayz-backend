# Gatewayz HuggingFace Task API - Complete Reference

## Request/Response Format

All requests use JSON format with Bearer token authentication.

### Request Headers

```http
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json
```

### Response Format

**Success (200)**:
```json
{
  "output": {...},
  "model": "model-id"
}
```

**Error (4xx, 5xx)**:
```json
{
  "error": "Error description",
  "error_type": "ErrorType",
  "message": "Detailed message"
}
```

---

## Text Generation

### Endpoint
```
POST /hf/tasks/text-generation
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inputs` | string | Yes | Prompt text to generate from |
| `parameters` | object | No | Generation parameters |
| `parameters.model` | string | No | Model ID (default: gpt-3.5-turbo) |
| `parameters.max_tokens` | integer | No | Max tokens to generate |
| `parameters.temperature` | float | No | Sampling temperature (0-2) |
| `parameters.top_p` | float | No | Nucleus sampling parameter |
| `stream` | boolean | No | Stream response (default: false) |

### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/text-generation \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": "The future of AI is",
    "parameters": {
      "model": "gpt-3.5-turbo",
      "max_tokens": 100,
      "temperature": 0.7
    }
  }'
```

### Response

```json
{
  "output": [
    {
      "generated_text": "The future of AI is likely to be characterized by continued advances in machine learning and artificial intelligence technologies..."
    }
  ]
}
```

### Streaming

Add `stream=true` parameter for streaming responses:

```bash
curl -X POST https://gatewayz.io/hf/tasks/text-generation?stream=true \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -N \
  -d '{...}'
```

Responses stream as newline-delimited JSON (NDJSON).

---

## Conversational

### Endpoint
```
POST /hf/tasks/conversational
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | Yes | Current user input |
| `past_user_inputs` | array | No | Previous user messages |
| `generated_responses` | array | No | Previous model responses |

### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/conversational \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "What is machine learning?",
    "past_user_inputs": ["Hello!", "How are you?"],
    "generated_responses": ["Hi there!", "I am doing great, thanks for asking!"]
  }'
```

### Response

```json
{
  "conversation": {
    "past_user_inputs": [
      "Hello!",
      "How are you?",
      "What is machine learning?"
    ],
    "generated_responses": [
      "Hi there!",
      "I am doing great, thanks for asking!",
      "Machine learning is a branch of artificial intelligence..."
    ]
  }
}
```

---

## Summarization

### Endpoint
```
POST /hf/tasks/summarization
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inputs` | string | Yes | Text to summarize |
| `parameters` | object | No | Optional parameters |

### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/summarization \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": "Natural language processing (NLP) is a subfield of linguistics, computer science, and artificial intelligence concerned with the interactions between computers and human language. NLP is used to apply machine learning algorithms to text and speech..."
  }'
```

### Response

```json
{
  "output": {
    "summary_text": "NLP is a subfield of linguistics and computer science that applies machine learning to text and speech."
  }
}
```

---

## Translation

### Endpoint
```
POST /hf/tasks/translation
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `inputs` | string | Yes | Text to translate |
| `target_language` | string | No | Target language (default: English) |

### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/translation \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": "Bonjour, comment allez-vous?",
    "target_language": "English"
  }'
```

### Response

```json
{
  "output": {
    "translation_text": "Hello, how are you?"
  }
}
```

---

## Question Answering

### Endpoint
```
POST /hf/tasks/question-answering
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `question` | string | Yes | Question to answer |
| `context` | string | Yes | Context to answer from |

### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/question-answering \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is AI?",
    "context": "Artificial intelligence (AI) is intelligence demonstrated by machines. AI research has explored machine learning, deep learning, and natural language processing."
  }'
```

### Response

```json
{
  "output": {
    "answer": "intelligence demonstrated by machines",
    "score": 0.95
  }
}
```

---

## Generic Task Runner

### Endpoint
```
POST /hf/tasks/run
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | Yes | Task type (text-generation, etc) |
| `model` | string | No | Model ID |
| `inputs` | string/object | Yes | Task inputs (format depends on task) |
| `parameters` | object | No | Task-specific parameters |

### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/run \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "task": "text-generation",
    "model": "gpt-3.5-turbo",
    "inputs": "Once upon a time",
    "parameters": {
      "max_tokens": 50
    }
  }'
```

### Response

```json
{
  "output": "Once upon a time, there was a young programmer...",
  "task": "text-generation",
  "model": "gpt-3.5-turbo"
}
```

---

## Model Management

### List Models

#### Endpoint
```
GET /hf/tasks/models
```

#### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `task_type` | string | Optional: Filter by task type |

#### Request

```bash
curl https://gatewayz.io/hf/tasks/models?task_type=text-generation \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Response

```json
{
  "models": [
    {
      "model_id": "gpt-3.5-turbo",
      "hub_model_id": "meta-llama/Llama-2-7b-chat",
      "task_type": "text-generation"
    },
    {
      "model_id": "gpt-4",
      "hub_model_id": "meta-llama/Llama-2-70b-chat",
      "task_type": "text-generation"
    }
  ],
  "count": 50,
  "task_types": ["text-generation", "image-generation"]
}
```

### Register Model Mapping

#### Endpoint
```
POST /hf/tasks/models/map
```

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `provider_model_id` | string | Yes | Model ID in provider catalog |
| `hub_model_id` | string | Yes | Equivalent HuggingFace Hub model |
| `task_type` | string | Yes | Task type |
| `parameters` | object | No | Default parameters |

#### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/models/map \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "provider_model_id": "gpt-3.5-turbo",
    "hub_model_id": "meta-llama/Llama-2-7b-chat",
    "task_type": "text-generation",
    "parameters": {
      "temperature": 0.7,
      "max_tokens": 2048
    }
  }'
```

#### Response

```json
{
  "success": true,
  "provider_model_id": "gpt-3.5-turbo",
  "hub_model_id": "meta-llama/Llama-2-7b-chat",
  "message": "Model mapping registered successfully"
}
```

---

## Billing

### Calculate Cost

#### Endpoint
```
POST /hf/tasks/billing/cost
```

#### Request Body

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

#### Request

```bash
curl -X POST https://gatewayz.io/hf/tasks/billing/cost \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "requests": [
      {
        "task": "text-generation",
        "model": "gpt-3.5-turbo",
        "input_tokens": 100,
        "output_tokens": 50
      }
    ]
  }'
```

#### Response

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

### Get Usage Records

#### Endpoint
```
GET /hf/tasks/billing/usage
```

#### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | 100 | Max records to return |
| `offset` | integer | 0 | Pagination offset |

#### Request

```bash
curl "https://gatewayz.io/hf/tasks/billing/usage?limit=50&offset=0" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

#### Response

```json
{
  "records": [
    {
      "request_id": "550e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2025-01-15T10:30:00Z",
      "task": "text-generation",
      "model": "gpt-3.5-turbo",
      "input_tokens": 100,
      "output_tokens": 50,
      "cost_nano_usd": 500000000
    }
  ],
  "total_records": 150,
  "total_cost_nano_usd": 75000000000
}
```

---

## Error Codes

### 4xx Client Errors

| Code | Error | Description |
|------|-------|-------------|
| 400 | BadRequest | Invalid request parameters |
| 401 | Unauthorized | Missing or invalid API key |
| 403 | Forbidden | Insufficient permissions |
| 404 | NotFound | Resource not found |
| 429 | TooManyRequests | Rate limit exceeded |

### 5xx Server Errors

| Code | Error | Description |
|------|-------|-------------|
| 500 | InternalServerError | Server error |
| 502 | BadGateway | Provider unavailable |
| 503 | ServiceUnavailable | Service temporarily unavailable |
| 504 | GatewayTimeout | Request timeout |

### Error Response Format

```json
{
  "error": "Invalid request parameters",
  "error_type": "BadRequest",
  "message": "Missing required parameter: inputs"
}
```

---

## Rate Limiting

### Rate Limit Headers

All responses include rate limit information:

```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1642339200
```

### Rate Limit Response

When rate limited (HTTP 429):

```json
{
  "error": "Rate limit exceeded",
  "error_type": "TooManyRequests",
  "message": "You have exceeded your rate limit. Try again after 60 seconds."
}
```

**Retry-After Header**: Indicates seconds to wait before retrying

---

## Task Types Reference

| Task | Input Type | Output Type | Description |
|------|-----------|-----------|-------------|
| text-generation | string | string | Generate text from prompt |
| conversational | string | object | Multi-turn conversation |
| summarization | string | string | Summarize text |
| translation | string | string | Translate text |
| question-answering | object | string | Answer question |
| text-classification | string | object | Classify text |
| token-classification | string | array | NER/token tagging |
| image-generation | string | base64 | Generate image |
| embedding | string | array | Generate embedding |

---

## Authentication Examples

### Python

```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "https://gatewayz.io/hf/tasks/text-generation",
        headers={"Authorization": "Bearer YOUR_API_KEY"},
        json={"inputs": "Hello"}
    )
    print(response.json())
```

### JavaScript

```javascript
const response = await fetch(
  "https://gatewayz.io/hf/tasks/text-generation",
  {
    method: "POST",
    headers: {
      "Authorization": "Bearer YOUR_API_KEY",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ inputs: "Hello" })
  }
);
const data = await response.json();
```

### cURL

```bash
curl -X POST https://gatewayz.io/hf/tasks/text-generation \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"inputs": "Hello"}'
```

---

## Billing Units

### Nano-USD (nano-usd)

1 nano-USD = 10^-9 USD

**Examples**:
- 1,000,000,000 nano-USD = $1.00 (one dollar)
- 500,000,000 nano-USD = $0.50 (fifty cents)
- 1,000,000 nano-USD = $0.001 (one thousandth)
- 1 nano-USD = $0.000000001 (one billionth)

**Conversion**:
```
cost_usd = cost_nano_usd / 1_000_000_000
cost_nano_usd = cost_usd * 1_000_000_000
```

---

## Common Use Cases

### 1. Simple Text Generation

```bash
curl -X POST https://gatewayz.io/hf/tasks/text-generation \
  -H "Authorization: Bearer KEY" \
  -d '{"inputs": "Once upon a time"}'
```

### 2. Multi-turn Chat

```python
async with AsyncGatewayzClient(api_key="KEY") as client:
    # Initial message
    response = await client.conversational("Hello")

    # Follow-up
    response = await client.conversational(
        "What is AI?",
        past_user_inputs=response.conversation["past_user_inputs"],
        generated_responses=response.conversation["generated_responses"]
    )
```

### 3. Cost Estimation

```bash
curl -X POST https://gatewayz.io/hf/tasks/billing/cost \
  -H "Authorization: Bearer KEY" \
  -d '{
    "requests": [
      {"task": "text-generation", "model": "gpt-3.5-turbo", "input_tokens": 100, "output_tokens": 50}
    ]
  }'
```

### 4. Batch Processing

```python
requests = [
    {"task": "summarization", "model": "bart", "input_tokens": 1000, "output_tokens": 200},
    {"task": "translation", "model": "t5", "input_tokens": 500, "output_tokens": 550},
]
cost = await client.calculate_cost(requests)
```

---

## Best Practices

1. **Batch requests** when possible for cost estimation
2. **Cache models list** to avoid repeated calls
3. **Handle rate limits** with exponential backoff
4. **Monitor usage** regularly via `/billing/usage`
5. **Use streaming** for long-running text generation
6. **Validate inputs** before sending requests
7. **Log request IDs** for debugging

---

**Last Updated**: 2025-01-15
**API Version**: 1.0.0
**Status**: Production Ready
