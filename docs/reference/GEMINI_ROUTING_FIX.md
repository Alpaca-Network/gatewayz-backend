# Google Gemini Model Routing Fix

## Problem Summary

When requesting `google/gemini-*` models (e.g., `google/gemini-2.0-flash-001`) without Google Vertex AI credentials, the system was incorrectly routing requests to **HuggingFace** instead of **OpenRouter**, resulting in the error:

```json
{
  "detail": "Provider 'huggingface' rejected request for model 'google/gemini-2.0-flash-001' (HTTP 400) | Response: {\"error\":{\"message\":\"The requested model 'google/gemini-2.0-flash-001' does not exist.\",\"type\":\"invalid_request_error\",\"param\":\"model\",\"code\":\"model_not_found\"}}"
}
```

## Root Cause

The issue was in the provider routing logic in `src/routes/chat.py` (lines ~674-730):

1. When no provider is specified, the system defaults to `"openrouter"`
2. `detect_provider_from_model_id()` is called to check if the model requires a specific provider
3. For `google/gemini-*` models, this function checks for Google Vertex AI credentials:
   - If credentials exist → returns `"google-vertex"`
   - If no credentials → returns `"openrouter"` (correct fallback)
4. **Bug**: The override logic only set `req_provider_missing = False` if the detected provider **differed** from the default provider
5. Since both were `"openrouter"`, the condition `override_provider != provider` was False
6. This left `req_provider_missing = True`, triggering the fallback logic
7. The fallback logic iterated through providers in order: `["huggingface", "featherless", ...]`
8. HuggingFace was checked first and incorrectly matched, causing the wrong routing

## The Fix

**File**: `src/routes/chat.py` (lines 684-696 and 1508-1520)

**Change**: Set `req_provider_missing = False` whenever `detect_provider_from_model_id()` returns a provider, **even if it matches the default provider**.

### Before:
```python
override_provider = detect_provider_from_model_id(original_model)
if override_provider:
    override_provider = override_provider.lower()
    if override_provider == "hug":
        override_provider = "huggingface"
    if override_provider != provider:  # Only set if different
        logger.info(f"Provider override applied...")
        provider = override_provider
        req_provider_missing = False  # Only set here!

if req_provider_missing:  # This still runs!
    # Fallback logic - checks huggingface first
    ...
```

### After:
```python
override_provider = detect_provider_from_model_id(original_model)
if override_provider:
    override_provider = override_provider.lower()
    if override_provider == "hug":
        override_provider = "huggingface"
    if override_provider != provider:
        logger.info(f"Provider override applied...")
        provider = override_provider
    # Mark provider as determined even if it matches the default
    # This prevents the fallback logic from incorrectly routing to wrong providers
    req_provider_missing = False  # Always set when provider is detected

if req_provider_missing:  # This no longer runs!
    # Fallback logic would have incorrectly routed to huggingface
    ...
```

## How It Works Now

### Without Google Vertex Credentials (your case):

1. User requests `google/gemini-2.0-flash-001` without specifying a provider
2. System defaults to `provider = "openrouter"`
3. `detect_provider_from_model_id()` checks for credentials
4. No credentials found → returns `"openrouter"` (correct fallback)
5. **Fix**: `req_provider_missing = False` is set
6. Fallback logic **does not run**
7. ✅ Request routes to OpenRouter (correct)

### With Google Vertex Credentials:

1. User requests `google/gemini-2.0-flash-001`
2. System defaults to `provider = "openrouter"`
3. `detect_provider_from_model_id()` checks for credentials
4. Credentials found → returns `"google-vertex"`
5. Provider changes: `"openrouter"` → `"google-vertex"`
6. `req_provider_missing = False` is set
7. ✅ Request routes to Google Vertex AI (correct)

## Verification

### Run the Tests

```bash
# Test the fix
pytest tests/routes/test_gemini_routing_fix.py -v

# All 7 tests should pass:
# ✅ test_gemini_provider_detection_logic
# ✅ test_gemini_with_vertex_credentials
# ✅ test_various_gemini_models_route_correctly[google/gemini-2.0-flash-001]
# ✅ test_various_gemini_models_route_correctly[google/gemini-2.5-flash]
# ✅ test_various_gemini_models_route_correctly[google/gemini-1.5-pro]
# ✅ test_various_gemini_models_route_correctly[google/gemini-2.0-pro]
# ✅ test_various_gemini_models_route_correctly[google/gemini-1.5-flash]
```

### Test Manually

```bash
# Without credentials (should route to OpenRouter)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "google/gemini-2.0-flash-001",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 10
  }'

# Check logs - should show:
# "⚠️ Routing google/gemini-2.0-flash-001 to openrouter (no Vertex credentials found)"
```

## Affected Models

This fix applies to all Google Gemini models:

- `google/gemini-2.5-flash`
- `google/gemini-2.5-pro`
- `google/gemini-2.0-flash-001`
- `google/gemini-2.0-flash`
- `google/gemini-2.0-pro`
- `google/gemini-1.5-pro`
- `google/gemini-1.5-flash`
- `google/gemini-1.0-pro`

## Files Changed

1. **src/routes/chat.py** (2 locations)
   - Lines ~684-696: Provider detection for non-streaming requests
   - Lines ~1508-1520: Provider detection for streaming requests

2. **tests/routes/test_gemini_routing_fix.py** (new file)
   - Comprehensive test coverage for the fix
   - Documents the bug and expected behavior

## Impact

- ✅ **No breaking changes** - All existing tests pass
- ✅ **Correct routing** - Gemini models now route to OpenRouter when no Vertex credentials
- ✅ **Fallback works** - System correctly falls back to OpenRouter instead of failing
- ✅ **Vertex still works** - When credentials are present, routes to Google Vertex AI

## Related Issues

- Credential detection logic: `src/services/model_transformations.py:582-604`
- Provider detection: `src/services/model_transformations.py:510-686`

## Date

2025-11-07
