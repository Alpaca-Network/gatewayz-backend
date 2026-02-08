# Langfuse LLM Observability Integration

This document describes how to configure and use Langfuse for LLM observability in Gatewayz.

## Overview

[Langfuse](https://langfuse.com) is an open-source LLM engineering platform that provides:
- Tracing for LLM applications
- Token usage and cost tracking
- Model performance analytics
- Scoring and evaluation
- User/session tracking
- Prompt management

Gatewayz integrates Langfuse alongside existing observability tools (OpenTelemetry/Tempo, Arize, Prometheus) to provide comprehensive LLM observability.

## Setup

### 1. Create a Langfuse Account

**Cloud-hosted (recommended for getting started):**
1. Sign up at [https://cloud.langfuse.com](https://cloud.langfuse.com)
2. Create a new project
3. Get your API keys from Settings > API Keys

**Self-hosted:**
1. Deploy Langfuse using Docker: [https://github.com/langfuse/langfuse](https://github.com/langfuse/langfuse)
2. Get your API keys from your self-hosted instance

### 2. Configure Environment Variables

Add the following environment variables to your deployment:

```bash
# Required: Enable Langfuse
LANGFUSE_ENABLED=true

# Required: API Keys (from Langfuse dashboard)
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx

# Optional: Host URL (default: https://cloud.langfuse.com)
# Use this for self-hosted Langfuse instances
LANGFUSE_HOST=https://cloud.langfuse.com

# Optional: Debug mode (default: false)
# Enables verbose logging of Langfuse SDK operations
LANGFUSE_DEBUG=false

# Optional: Flush interval in seconds (default: 1.0)
# How often to send batched traces to Langfuse
LANGFUSE_FLUSH_INTERVAL=1.0

# Optional: OpenAI SDK auto-instrumentation (default: false)
# Automatically traces OpenAI SDK calls
LANGFUSE_OPENAI_INSTRUMENTATION=false
```

### 3. Verify Configuration

After deploying with Langfuse enabled, verify the integration:

```bash
# Check instrumentation health (public endpoint)
curl https://your-api.example.com/api/instrumentation/health

# Check detailed Langfuse status (requires admin key)
curl -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  https://your-api.example.com/api/instrumentation/langfuse/status
```

Expected response:
```json
{
  "enabled": true,
  "initialized": true,
  "host": "https://cloud.langfuse.com",
  "public_key_configured": true,
  "secret_key_configured": true,
  "environment": "production"
}
```

## Usage

### Automatic Tracing

When Langfuse is enabled, traces are automatically created for:
- Chat completion requests via `/v1/chat/completions`
- Anthropic Messages API requests via `/v1/messages`

Traces include:
- Model name and provider
- Input messages
- Output response
- Token usage (input/output/total)
- Cost in USD
- Latency

### Manual Tracing

For custom tracing, use the `AITracer` utility:

```python
from src.utils.ai_tracing import AITracer

async with AITracer.trace_inference(
    provider="openrouter",
    model="gpt-4",
    user_id="user_123",  # Optional: for user-level analytics
    session_id="session_456",  # Optional: for session grouping
) as ctx:
    # Make your LLM call
    response = await call_model(messages)

    # Set input/output for Langfuse
    ctx.set_input(messages)
    ctx.set_output(response)

    # Set token usage
    ctx.set_token_usage(
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
    )

    # Set cost
    ctx.set_cost(0.0023)
```

### Using Langfuse Directly

For more advanced use cases, access the Langfuse client directly:

```python
from src.config.langfuse_config import LangfuseConfig, LangfuseTracer

# Get the Langfuse client
client = LangfuseConfig.get_client()

# Create a trace manually
if client:
    trace = client.trace(
        name="my_operation",
        user_id="user_123",
        metadata={"custom_field": "value"},
    )

    # Create a generation span
    generation = trace.generation(
        name="gpt-4",
        model="gpt-4",
        input=messages,
    )

    # ... do work ...

    # End the generation
    generation.end(
        output=response,
        usage={
            "input": prompt_tokens,
            "output": completion_tokens,
        },
    )

# Flush pending traces
LangfuseConfig.flush()
```

## Viewing Traces

1. Log in to your Langfuse dashboard
2. Navigate to Traces
3. Filter by:
   - Model name
   - User ID
   - Date range
   - Tags (e.g., provider name)

## Scoring and Evaluation

Langfuse supports adding scores to traces for quality evaluation:

```python
from src.config.langfuse_config import LangfuseTracer

async with LangfuseTracer.trace_generation("openrouter", "gpt-4") as ctx:
    response = await call_model(messages)
    ctx.set_output(response)

    # Add a score for quality evaluation
    ctx.score(
        name="user_rating",
        value=4.5,
        comment="User gave positive feedback",
    )
```

## Integration with Other Observability Tools

Langfuse works alongside:
- **OpenTelemetry/Tempo**: Distributed tracing (spans include both OTel and Langfuse context)
- **Prometheus**: Metrics are still collected independently
- **Arize**: Both Arize and Langfuse can be enabled simultaneously
- **Loki**: Logs can be correlated with Langfuse traces

## Troubleshooting

### Traces not appearing

1. Verify Langfuse is enabled:
   ```bash
   curl -H "Authorization: Bearer YOUR_ADMIN_KEY" \
     https://your-api.example.com/api/instrumentation/langfuse/status
   ```

2. Check that `initialized` is `true`

3. Flush traces manually:
   ```bash
   curl -X POST -H "Authorization: Bearer YOUR_ADMIN_KEY" \
     https://your-api.example.com/api/instrumentation/langfuse/flush
   ```

4. Enable debug mode temporarily:
   ```bash
   LANGFUSE_DEBUG=true
   ```

### Connection errors

1. Verify your API keys are correct
2. Check network connectivity to Langfuse host
3. For self-hosted instances, ensure the host URL is correct

### High latency

- Langfuse uses batching to minimize latency impact
- Traces are sent asynchronously in the background
- Adjust `LANGFUSE_FLUSH_INTERVAL` if needed

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANGFUSE_ENABLED` | Yes | `false` | Enable Langfuse integration |
| `LANGFUSE_PUBLIC_KEY` | Yes* | - | Public API key from Langfuse |
| `LANGFUSE_SECRET_KEY` | Yes* | - | Secret API key from Langfuse |
| `LANGFUSE_HOST` | No | `https://cloud.langfuse.com` | Langfuse API host |
| `LANGFUSE_DEBUG` | No | `false` | Enable debug logging |
| `LANGFUSE_FLUSH_INTERVAL` | No | `1.0` | Batch flush interval (seconds) |
| `LANGFUSE_OPENAI_INSTRUMENTATION` | No | `false` | Auto-instrument OpenAI SDK |

*Required when `LANGFUSE_ENABLED=true`

## Resources

- [Langfuse Documentation](https://langfuse.com/docs)
- [Langfuse Python SDK](https://langfuse.com/docs/sdk/python)
- [Self-hosting Langfuse](https://langfuse.com/docs/deployment/self-host)
- [Langfuse GitHub](https://github.com/langfuse/langfuse)
