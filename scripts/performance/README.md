# Google Vertex AI Performance Testing

This directory contains performance testing scripts for comparing Google Vertex AI endpoints.

## Overview

The `test_google_vertex_endpoints.py` script performs comprehensive performance comparisons between **regional** and **global** Google Vertex AI endpoints for Gemini models.

### What It Tests

- **Time To First Chunk (TTFC)**: How quickly the API starts responding
- **Total Response Time**: Complete request duration
- **Success Rate**: Reliability across multiple attempts
- **Token Generation**: Output consistency

### Why This Matters

Preview models like `gemini-3-pro-preview` are only officially available on **global endpoints**, which can experience 20-30+ second cold start times. This test helps determine if regional endpoints offer better performance for certain models.

## Prerequisites

### Environment Variables

```bash
# Required
export GOOGLE_PROJECT_ID="your-gcp-project-id"
export GOOGLE_VERTEX_LOCATION="us-central1"  # Your preferred regional endpoint

# Authentication (choose one)
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
# OR
export GOOGLE_VERTEX_CREDENTIALS_JSON='{"type":"service_account",...}'
```

### Dependencies

All dependencies are in the main project's `requirements.txt`:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Run

```bash
python scripts/performance/test_google_vertex_endpoints.py
```

### What Happens

1. **Tests 3 Gemini models** (configurable):
   - `gemini-3-pro-preview` (preview model)
   - `gemini-2.5-flash-lite` (standard model)
   - `gemini-1.5-pro` (standard model)

2. **5 iterations per endpoint** (configurable):
   - 5 requests to global endpoint
   - 5 requests to regional endpoint

3. **Collects metrics**:
   - TTFC (Time To First Chunk)
   - Total response time
   - Success rate
   - Token generation count
   - Error messages

4. **Generates outputs**:
   - JSON results file: `test_results/google_vertex_performance_results.json`
   - Markdown report printed to console
   - Detailed logs for each request

### Expected Output

```
================================================================================
Google Vertex AI Endpoint Performance Comparison
================================================================================
Regional location: us-central1
Models to test: ['gemini-3-pro-preview', 'gemini-2.5-flash-lite', 'gemini-1.5-pro']
Iterations per test: 5
================================================================================

--- Testing GLOBAL endpoint for gemini-3-pro-preview ---
Iteration 1/5
✓ gemini-3-pro-preview on global: 29.43s, 42 tokens
Iteration 2/5
✓ gemini-3-pro-preview on global: 31.27s, 45 tokens
...

--- Testing REGIONAL endpoint for gemini-3-pro-preview ---
Iteration 1/5
✓ gemini-3-pro-preview on regional: 12.85s, 43 tokens
Iteration 2/5
✓ gemini-3-pro-preview on regional: 11.92s, 44 tokens
...

================================================================================
SUMMARY for gemini-3-pro-preview
================================================================================
Global endpoint: 30.15s avg
Regional endpoint: 12.34s avg
Improvement: +59.0%
Winner: regional
```

## Configuration

Edit `TEST_CONFIG` in the script to customize:

```python
TEST_CONFIG = {
    "models": [
        "gemini-3-pro-preview",
        "gemini-2.5-flash-lite",
        "gemini-1.5-pro",
    ],
    "iterations_per_test": 5,  # Number of tests per endpoint
    "test_prompt": "Write a short haiku about artificial intelligence.",
    "max_tokens": 100,
    "temperature": 0.7,
}
```

## Output Files

### JSON Results

Location: `test_results/google_vertex_performance_results.json`

Structure:
```json
{
  "test_config": { ... },
  "regional_location": "us-central1",
  "timestamp": "2026-01-18T...",
  "raw_results": [ ... ],
  "comparison_reports": [
    {
      "model": "gemini-3-pro-preview",
      "global_endpoint": {
        "location": "global",
        "avg_ttfc": "29.34s",
        "success_rate": "100.0%"
      },
      "regional_endpoint": {
        "location": "us-central1",
        "avg_ttfc": "12.45s",
        "success_rate": "100.0%"
      },
      "performance_improvement": {
        "ttfc_improvement": "+57.6%",
        "winner": "regional"
      }
    }
  ]
}
```

### Markdown Report

Automatically printed to console in markdown format, ready for documentation or PR descriptions.

## Interpreting Results

### Key Metrics

1. **Avg TTFC**: Average time to first chunk
   - **Good**: <5s
   - **Acceptable**: 5-15s
   - **Slow**: >15s
   - **Critical**: >30s

2. **Success Rate**: Percentage of successful requests
   - **Target**: 100%
   - **Warning**: <95%

3. **Std Dev**: Standard deviation (consistency)
   - **Low**: <2s (consistent)
   - **High**: >5s (inconsistent/unreliable)

### Performance Improvement Calculation

```
improvement = (global_time - regional_time) / global_time * 100
```

- **Positive %**: Regional is faster
- **Negative %**: Global is faster

### Recommendations

- **>20% improvement**: Strong case for regional endpoint
- **10-20% improvement**: Consider regional with monitoring
- **<10% improvement**: Marginal difference, keep global for official support
- **Negative improvement**: Stick with global endpoint

## Common Issues

### Model Not Available on Regional Endpoint

**Symptom**: `404 Not Found` or `Model not found` errors on regional endpoint

**Solution**: Some preview models are truly global-only. Test results will show failures, confirming official documentation.

### Authentication Errors

**Symptom**: `401 Unauthorized` or credential errors

**Solution**:
```bash
# Verify credentials are valid
gcloud auth application-default print-access-token

# Or check service account key
cat $GOOGLE_APPLICATION_CREDENTIALS
```

### Timeout Errors

**Symptom**: Requests timing out before completion

**Solution**: Increase timeout in `src/config/config.py`:
```python
GOOGLE_VERTEX_TIMEOUT = float(os.environ.get("GOOGLE_VERTEX_TIMEOUT", "240"))  # 4 minutes
```

## Advanced Usage

### Custom Model Testing

Test your own model list:

```python
TEST_CONFIG["models"] = [
    "your-custom-model-1",
    "your-custom-model-2",
]
```

### Streaming Support (Future Enhancement)

The current script tests non-streaming requests. For streaming TTFC measurement, modify:

```python
# TODO: Add streaming test support
# Use make_google_vertex_request_openai_stream() instead
```

### Multi-Region Testing

Test multiple regional endpoints:

```bash
# Test us-central1
GOOGLE_VERTEX_LOCATION=us-central1 python scripts/performance/test_google_vertex_endpoints.py

# Test europe-west4
GOOGLE_VERTEX_LOCATION=europe-west4 python scripts/performance/test_google_vertex_endpoints.py

# Compare results
```

## Integration with CI/CD

### GitHub Actions Example

```yaml
name: Google Vertex Performance Test

on:
  schedule:
    - cron: '0 0 * * 0'  # Weekly on Sunday
  workflow_dispatch:

jobs:
  performance-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r requirements.txt
      - run: python scripts/performance/test_google_vertex_endpoints.py
        env:
          GOOGLE_PROJECT_ID: ${{ secrets.GOOGLE_PROJECT_ID }}
          GOOGLE_VERTEX_CREDENTIALS_JSON: ${{ secrets.GOOGLE_VERTEX_CREDENTIALS_JSON }}
      - uses: actions/upload-artifact@v3
        with:
          name: performance-results
          path: test_results/
```

## Contributing

When adding new performance tests:

1. Follow the existing metrics structure
2. Add documentation for new metrics
3. Ensure tests are idempotent (can run multiple times safely)
4. Include expected output examples

## Related Documentation

- [Google Vertex AI Locations](https://cloud.google.com/vertex-ai/docs/general/locations)
- [Gemini Model Documentation](https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini)
- [Performance Optimization Guide](../../docs/PERFORMANCE.md)

## Support

For issues or questions:
- Check [GitHub Issues](https://github.com/Alpaca-Network/gatewayz-backend/issues)
- Review Railway deployment logs
- Check Sentry for error patterns
