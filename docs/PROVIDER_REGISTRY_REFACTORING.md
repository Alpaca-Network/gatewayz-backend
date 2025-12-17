# Provider Registry Refactoring

## Overview

This document describes the new provider registry system that replaces 600+ lines of hard-coded provider logic in `chat.py`.

## What Changed

### Before (Old System)
```python
# chat.py had 283 lines of repetitive imports like this:
_openrouter = _safe_import_provider("openrouter", [...])
make_openrouter_request_openai = _openrouter.get(...)
process_openrouter_response = _openrouter.get(...)
# ... repeated 22 times for each provider

# Plus 330+ lines of if/elif chains like this:
if attempt_provider == "featherless":
    stream = await _to_thread(make_featherless_request_openai_stream, ...)
elif attempt_provider == "fireworks":
    stream = await _to_thread(make_fireworks_request_openai_stream, ...)
# ... repeated 20+ times
```

### After (New System)
```python
# Load all providers at startup (replaces 283 lines of imports)
from src.services.provider_loader import load_all_providers
load_all_providers()

# Use any provider (replaces 330+ lines of if/elif)
from src.services.provider_registry import get_provider_registry
registry = get_provider_registry()
provider = registry.get("openrouter")
result = await provider.make_request(messages, model, **options)
```

## New Files Created

### 1. `src/services/provider_registry.py`
- `ProviderConfig`: Dataclass holding provider configuration
- `ProviderRegistry`: Central registry for all providers
- `get_provider_registry()`: Get the global registry instance

### 2. `src/config/providers.py`
- `PROVIDER_CONFIGS`: All provider configurations in one place
- `DEFAULT_PROVIDER`: Default provider setting
- `AUTO_DETECT_PROVIDERS`: Provider auto-detection order
- Helper functions for timeouts and normalization

### 3. `src/services/provider_loader.py`
- `load_all_providers()`: Load all providers at startup
- `load_provider()`: Load a single provider
- `get_provider_or_error()`: Get provider with error handling

## Benefits

### 1. Massive Code Reduction
- **Before**: 600+ lines of provider logic
- **After**: ~50 lines using registry
- **Savings**: 92% reduction!

### 2. Easy to Add New Provider
**Before** (edit 4+ locations):
```python
# 1. Add import (13 lines)
_newprovider = _safe_import_provider(...)
make_newprovider_request_openai = ...
# ... etc

# 2. Add to streaming if/elif (8 lines)
elif attempt_provider == "newprovider":
    stream = await _to_thread(...)

# 3. Add to non-streaming if/elif (10 lines)
elif attempt_provider == "newprovider":
    resp_raw = await asyncio.wait_for(...)

# 4. Add to timeouts dict
PROVIDER_TIMEOUTS = {
    "newprovider": 30,
}
```

**After** (1 location):
```python
# Just add to src/config/providers.py:
PROVIDER_CONFIGS = {
    ...
    "newprovider": {
        "timeout": 30,
        "priority": 23,
        "auto_detect": False,
        "module_name": "newprovider",
    },
}
```

### 3. Type Safety
All provider functions are strongly typed in the `ProviderConfig` dataclass.

### 4. Centralized Configuration
All provider settings (timeouts, priorities, auto-detect) are in one place.

### 5. Better Error Handling
Import errors are tracked and reported systematically.

## Migration Guide

### Step 1: Load Providers at Startup
In your main application file, add:
```python
from src.services.provider_loader import load_all_providers

# Call once at startup
load_all_providers()
```

### Step 2: Use Registry Instead of If/Elif Chains
**Old code:**
```python
if attempt_provider == "openrouter":
    resp = await _to_thread(make_openrouter_request_openai, messages, model, **optional)
    processed = await _to_thread(process_openrouter_response, resp)
elif attempt_provider == "featherless":
    resp = await _to_thread(make_featherless_request_openai, messages, model, **optional)
    processed = await _to_thread(process_featherless_response, resp)
# ... 20+ more
```

**New code:**
```python
from src.services.provider_registry import get_provider_registry

registry = get_provider_registry()
provider_config = registry.get(attempt_provider)

if provider_config:
    timeout = provider_config.timeout
    resp = await asyncio.wait_for(
        _to_thread(provider_config.make_request, messages, model, **optional),
        timeout=timeout
    )
    processed = await _to_thread(provider_config.process_response, resp)
```

### Step 3: Use Config for Provider Lists
**Old code:**
```python
# Hard-coded list
for test_provider in ["huggingface", "featherless", "fireworks", "together", "google-vertex"]:
    # ...
```

**New code:**
```python
from src.config.providers import AUTO_DETECT_PROVIDERS

for test_provider in AUTO_DETECT_PROVIDERS:
    # ...
```

## Next Steps

The following items still need to be updated in `chat.py`:

1. **Lines 123-405**: Remove old provider imports, use `load_all_providers()` instead
2. **Lines 520-524**: Remove `PROVIDER_TIMEOUTS` dict, use `registry.get_timeout()` instead
3. **Lines 1490-1496**: Replace hard-coded provider list with `AUTO_DETECT_PROVIDERS`
4. **Lines 1535-1663**: Replace streaming if/elif chain with registry lookup
5. **Lines 1811-2011**: Replace non-streaming if/elif chain with registry lookup

## Testing

After migration, verify:
1. All providers still work correctly
2. Error messages are informative
3. Failover logic still functions
4. Performance is not degraded

## Rollback Plan

If issues arise:
1. The old code is preserved in git history
2. The new modules can be removed
3. chat.py can be reverted to the previous version

## Questions?

Contact the development team or see the code comments in:
- `src/services/provider_registry.py`
- `src/services/provider_loader.py`
- `src/config/providers.py`
