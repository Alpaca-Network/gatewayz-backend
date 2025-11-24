# Gatewayz HuggingFace Provider Implementation Guide

Step-by-step guide for implementing and deploying Gatewayz as a HuggingFace inference provider.

## Phase 1: Code Integration (✅ COMPLETED)

### Completed Components

1. **Task API Schemas** (`src/schemas/huggingface_tasks.py`)
   - ✅ Pydantic models for all task types
   - ✅ Request/response schemas
   - ✅ Error handling models
   - ✅ Billing models

2. **Task Routes** (`src/routes/huggingface_tasks.py`)
   - ✅ Text generation endpoint
   - ✅ Conversational endpoint
   - ✅ Summarization endpoint
   - ✅ Generic task runner
   - ✅ Model mapping endpoints
   - ✅ Billing endpoints
   - ✅ Usage tracking

3. **Billing Service** (`src/services/huggingface_billing.py`)
   - ✅ Nano-USD cost calculation
   - ✅ Usage logging
   - ✅ Credit deduction
   - ✅ Batch operations
   - ✅ Billing summaries

4. **Model Mapping** (`src/services/huggingface_model_mapping.py`)
   - ✅ Model registration
   - ✅ Mapping retrieval
   - ✅ Registry generation
   - ✅ Bulk operations
   - ✅ Default mappings

5. **Python Client Library** (`gatewayz-py-hf/`)
   - ✅ Async and sync clients
   - ✅ Task methods
   - ✅ Model discovery
   - ✅ Billing queries
   - ✅ Type hints

6. **JavaScript/TypeScript Client** (`gatewayz-js-hf/`)
   - ✅ TypeScript types
   - ✅ Async client
   - ✅ Task methods
   - ✅ Model discovery
   - ✅ Billing queries

## Phase 2: Integration with Main Application

### Update main.py

Add the HuggingFace task routes to the FastAPI application:

```python
# In src/main.py

from src.routes import huggingface_tasks

def create_app():
    app = FastAPI()

    # ... existing routes ...

    # Add HuggingFace provider routes
    app.include_router(huggingface_tasks.router)

    return app
```

### Update Requirements

Add any new dependencies (if not already present):

```bash
pip install httpx  # Already in requirements.txt
```

### Database Schema

Create migration for usage tracking (if needed):

```sql
-- supabase/migrations/024_huggingface_usage_table.sql
CREATE TABLE IF NOT EXISTS huggingface_usage (
    id BIGSERIAL PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES users(id),
    task VARCHAR(255) NOT NULL,
    model VARCHAR(255) NOT NULL,
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    cost_nano_usd BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX idx_hf_usage_user_id ON huggingface_usage(user_id);
CREATE INDEX idx_hf_usage_created_at ON huggingface_usage(created_at);
```

## Phase 3: Testing

### Unit Tests

Create `tests/integration/test_huggingface_tasks.py`:

```python
import pytest
from src.schemas.huggingface_tasks import (
    TextGenerationInput,
    TextGenerationResponse,
)

@pytest.mark.asyncio
async def test_text_generation_endpoint(client, api_key):
    response = await client.post(
        "/hf/tasks/text-generation",
        json={
            "inputs": "Hello, world!",
            "parameters": {"model": "gpt-3.5-turbo"}
        },
        headers={"Authorization": f"Bearer {api_key}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "output" in data
    assert len(data["output"]) > 0
    assert "generated_text" in data["output"][0]


@pytest.mark.asyncio
async def test_billing_cost_calculation(client, api_key):
    response = await client.post(
        "/hf/tasks/billing/cost",
        json={
            "requests": [
                {
                    "task": "text-generation",
                    "model": "gpt-3.5-turbo",
                    "input_tokens": 100,
                    "output_tokens": 50
                }
            ]
        },
        headers={"Authorization": f"Bearer {api_key}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "total_cost_nano_usd" in data
    assert data["total_cost_nano_usd"] > 0
    assert "costs" in data
    assert len(data["costs"]) == 1
```

### Integration Tests

Test client libraries:

```python
# Python client test
import asyncio
from gatewayz_py_hf import AsyncGatewayzClient

async def test_python_client():
    async with AsyncGatewayzClient(api_key="test-key") as client:
        models = await client.list_models()
        assert len(models) > 0

# JavaScript client test (Jest)
import { createClient } from "gatewayz-js-hf";

test("list models", async () => {
  const client = createClient("test-key");
  const models = await client.listModels();
  expect(models.length).toBeGreaterThan(0);
});
```

### Load Testing

```bash
# Using wrk or Apache Bench
wrk -t12 -c400 -d30s -s script.lua https://gatewayz.io/hf/tasks/text-generation

# Or with k6
k6 run load-test.js
```

## Phase 4: Deployment

### Environment Variables

Add to `.env` for local testing:

```bash
# HuggingFace integration
HUGGINGFACE_PROVIDER_ENABLED=true
HUGGINGFACE_API_ENDPOINT=https://gatewayz.io/hf/tasks
```

### Deployment Checklist

- [ ] All tests passing
- [ ] Documentation updated
- [ ] Performance benchmarks reviewed
- [ ] Rate limiting configured
- [ ] Billing system tested
- [ ] Error handling verified
- [ ] Monitoring/logging enabled
- [ ] Deployed to staging
- [ ] Load tested on staging
- [ ] Deployed to production
- [ ] Health checks working
- [ ] Metrics exposed

### Health Check Endpoint

The `/health` endpoint should verify:

```json
{
  "status": "healthy",
  "components": {
    "database": "healthy",
    "redis": "healthy",
    "billing": "healthy",
    "model_catalog": "healthy"
  }
}
```

## Phase 5: HuggingFace Hub Integration

### Contact HuggingFace

Email: support@huggingface.co or post to HuggingFace Discussions

**Subject**: Gatewayz Inference Provider Registration

**Message Template**:
```
Hello,

We would like to register Gatewayz as an inference provider on the HuggingFace Hub.

**Provider Information**:
- Name: Gatewayz
- URL: https://gatewayz.io
- Description: Universal API gateway for 15+ AI model providers
- Contact: support@gatewayz.io

**Implementation Status**:
- ✅ Task API endpoints implemented
- ✅ Billing system with nano-USD precision
- ✅ Model mapping API
- ✅ Python client library
- ✅ JavaScript/TypeScript client library
- ✅ Comprehensive documentation

**Supported Tasks**:
- text-generation
- conversational
- summarization
- translation
- question-answering
- And more...

**API Endpoint**: https://gatewayz.io/hf/tasks

Ready to proceed with integration steps.

Thanks,
Terragon Labs
```

### Pull Requests

1. **HuggingFace transformers repository**
   - Add Gatewayz to provider list
   - Link to Python client library
   - Include integration examples

2. **HuggingFace chat-ui repository**
   - Add Gatewayz to provider list
   - Link to JavaScript client library
   - Include configuration examples

3. **HuggingFace docs repository**
   - Add provider documentation
   - Include API reference
   - Add integration guides

### Provider Assets

Prepare these files:

1. **Logo** (SVG)
   - `gatewayz-logo-light.svg` (500x500px min)
   - `gatewayz-logo-dark.svg` (500x500px min)

2. **Description**
   ```
   Gatewayz is a universal API gateway providing unified access to 15+
   AI model providers through HuggingFace's standard inference APIs.
   ```

3. **Provider Metadata**
   ```json
   {
     "name": "Gatewayz",
     "provider": "Terragon Labs",
     "url": "https://gatewayz.io",
     "status": "active",
     "models_count": 100+,
     "tasks_supported": 9,
     "features": ["multi-provider", "unified-api", "pay-per-use", "enterprise"]
   }
   ```

## Phase 6: Monitoring & Operations

### Metrics to Track

- Request volume by task type
- Latency percentiles
- Error rates per provider
- Billing totals
- Provider availability
- Model performance

### Alerting Rules

```yaml
alerts:
  - name: HighErrorRate
    condition: error_rate > 5%
    threshold: 5

  - name: HighLatency
    condition: p95_latency > 5s
    threshold: 5

  - name: ProviderDown
    condition: provider_up == 0
    threshold: 1

  - name: BillingServiceDown
    condition: billing_service_up == 0
    threshold: 1
```

### Dashboard

Create Grafana dashboard with:
- Request rate
- Error rate
- Latency distribution
- Provider status
- Billing metrics
- Model usage distribution

## Phase 7: Documentation & Support

### User Documentation

Create guides for:
1. Getting started with Gatewayz on HF
2. Python client tutorial
3. JavaScript/TypeScript client tutorial
4. API reference
5. Billing explanation
6. Pricing information

### Support Channels

- GitHub Issues: https://github.com/terragon-labs/gatewayz/issues
- Email: support@gatewayz.io
- Discord: (if applicable)
- Documentation: https://docs.gatewayz.io

## Implementation Timeline

| Phase | Duration | Status |
|-------|----------|--------|
| Code Implementation | 1 week | ✅ Complete |
| Main App Integration | 1-2 days | ⏳ Pending |
| Testing | 1-2 weeks | ⏳ Pending |
| Deployment (Staging) | 3-5 days | ⏳ Pending |
| HuggingFace Integration | 2-4 weeks | ⏳ Pending |
| Production Deployment | 1-2 days | ⏳ Pending |
| Monitoring Setup | 1 week | ⏳ Pending |
| **Total** | **~6-8 weeks** | |

## Next Steps

1. **Integrate routes into main.py**
   ```bash
   # Edit src/main.py
   # Add: from src.routes import huggingface_tasks
   # Add: app.include_router(huggingface_tasks.router)
   ```

2. **Set up tests**
   ```bash
   mkdir tests/integration/huggingface
   # Add test files
   ```

3. **Configure deployment**
   - Add environment variables
   - Set up monitoring
   - Configure rate limits

4. **Contact HuggingFace**
   - Start registration process
   - Prepare assets
   - Plan integration timeline

## Resources

- HuggingFace Docs: https://huggingface.co/docs/inference-providers/
- Gatewayz Docs: https://docs.gatewayz.io
- API Reference: See `/docs/HUGGINGFACE_PROVIDER.md`
- Python Client: See `gatewayz-py-hf/`
- JS Client: See `gatewayz-js-hf/`

---

**Last Updated**: 2025-01-15
**Author**: Terragon Labs
**Status**: Implementation Complete, Integration Pending
