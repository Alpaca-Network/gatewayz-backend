# OpenRouter Authentication Error - Root Cause and Fix

**Date**: 2025-11-27
**Issue**: `openrouter authentication error` when trying to use OpenRouter models
**Status**: ✅ **ROOT CAUSE IDENTIFIED**

---

## Problem Summary

When attempting to use the `openrouter/auto` model (or any OpenRouter model) through the Gatewayz API, requests fail with:

```json
{
  "detail": "openrouter authentication error"
}
```

## Root Cause

**The `OPENROUTER_API_KEY` environment variable is not set.**

### Diagnostic Results

```
[CHECK 1] Environment Variable
❌ OPENROUTER_API_KEY is NOT set in environment
```

The application expects the OpenRouter API key to be available via the `OPENROUTER_API_KEY` environment variable, but it's currently not configured.

## Why This Happens

1. **Configuration Location**: The OpenRouter API key is loaded from environment variables in `src/config/config.py:114`

   ```python
   OPENROUTER_API_KEY = _get_env_var("OPENROUTER_API_KEY")
   ```

2. **Missing .env File**: There is no `.env` file in the project root (only `.env.example`, `.env.template`)

3. **Runtime Environment**: When the application starts, it can't find the `OPENROUTER_API_KEY` variable

4. **Error Propagation**: When a request is made to OpenRouter:
   - The OpenRouter client attempts to initialize
   - It checks for the API key (`src/services/openrouter_client.py:20`)
   - If missing, raises `ValueError("OpenRouter API key not configured")`
   - This gets caught and converted to HTTP 401/403 in `src/services/provider_failover.py:263,283,311`
   - Returns as: `"openrouter authentication error"`

## Solution

### Option 1: Create a .env File (Recommended for Local Development)

1. Copy the example file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your OpenRouter API key:
   ```bash
   OPENROUTER_API_KEY=sk-or-v1-YOUR_ACTUAL_KEY_HERE
   OPENROUTER_SITE_URL=https://your-site.com  # Optional
   OPENROUTER_SITE_NAME=Gatewayz API          # Optional
   ```

3. Get an API key from: https://openrouter.ai/keys

4. Restart the application

### Option 2: Set Environment Variable Directly (Production)

For production deployments (Railway, Vercel, Docker):

**Railway:**
```bash
# In Railway dashboard or CLI
railway variables set OPENROUTER_API_KEY=sk-or-v1-YOUR_KEY_HERE
```

**Vercel:**
```bash
# In Vercel dashboard or CLI
vercel env add OPENROUTER_API_KEY
# Enter: sk-or-v1-YOUR_KEY_HERE
```

**Docker:**
```bash
# In docker-compose.yml or .env file
environment:
  - OPENROUTER_API_KEY=sk-or-v1-YOUR_KEY_HERE

# Or via command line
docker run -e OPENROUTER_API_KEY=sk-or-v1-YOUR_KEY_HERE ...
```

**Linux/macOS Shell:**
```bash
export OPENROUTER_API_KEY=sk-or-v1-YOUR_KEY_HERE
python src/main.py
```

## Verification

After setting the API key, verify it's working:

### Method 1: Run the Diagnostic Script

```bash
python3 diagnose_openrouter_auth.py
```

Expected output:
```
[CHECK 1] Environment Variable
✅ OPENROUTER_API_KEY is set in environment

[CHECK 2] Config Module
✅ Config.OPENROUTER_API_KEY is set

[CHECK 3] OpenRouter Client Initialization
✅ OpenRouter client initialized successfully

[CHECK 4] Test API Request
✅ Request successful!

✅ ALL CHECKS PASSED - OPENROUTER AUTHENTICATION IS WORKING
```

### Method 2: Test via API

```bash
curl https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer YOUR_GATEWAYZ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openrouter/auto",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Should return a successful response with content from the routed model.

## Implementation Details

### How the API Key is Used

1. **Loading** (`src/config/config.py:114`):
   ```python
   OPENROUTER_API_KEY = _get_env_var("OPENROUTER_API_KEY")
   ```
   - Uses `_get_env_var` helper which strips whitespace
   - Returns `None` if not set

2. **Client Initialization** (`src/services/connection_pool.py:256-269`):
   ```python
   def get_openrouter_pooled_client() -> OpenAI:
       if not Config.OPENROUTER_API_KEY:
           raise ValueError("OpenRouter API key not configured")

       return get_pooled_client(
           provider="openrouter",
           base_url="https://openrouter.ai/api/v1",
           api_key=Config.OPENROUTER_API_KEY,
           default_headers={
               "HTTP-Referer": Config.OPENROUTER_SITE_URL,
               "X-Title": Config.OPENROUTER_SITE_NAME,
           },
       )
   ```

3. **Making Requests** (`src/services/openrouter_client.py:31-45`):
   ```python
   def make_openrouter_request_openai(messages, model, **kwargs):
       try:
           client = get_openrouter_client()
           response = client.chat.completions.create(
               model=model, messages=messages, **kwargs
           )
           return response
       except Exception as e:
           logger.error(f"OpenRouter request failed: {e}")
           raise
   ```

4. **Error Handling** (`src/services/provider_failover.py:263,283,311`):
   - Authentication errors (401/403) are caught
   - Converted to: `"{provider} authentication error"`
   - Returned as HTTP 500 to the client

## API Key Format

OpenRouter API keys have the format:
```
sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- Starts with: `sk-or-v1-`
- Length: ~64 characters total
- Get one from: https://openrouter.ai/keys

## Required Permissions

The API key needs:
- ✅ Read access to models
- ✅ Make chat completion requests
- ✅ Access to Auto Router feature (for `openrouter/auto`)

## Related Configuration

Optional environment variables for OpenRouter:

```bash
# Required
OPENROUTER_API_KEY=sk-or-v1-...

# Optional (defaults provided)
OPENROUTER_SITE_URL=https://your-site.com
OPENROUTER_SITE_NAME=Your Application Name
```

These optional variables are used for:
- `OPENROUTER_SITE_URL`: Sent in `HTTP-Referer` header (for OpenRouter analytics)
- `OPENROUTER_SITE_NAME`: Sent in `X-Title` header (for OpenRouter dashboard display)

## Troubleshooting

### Still Getting Authentication Error After Setting Key?

1. **Restart the application** - Config is loaded at startup
2. **Check for whitespace** - The key should have no spaces
3. **Verify key validity** - Test directly with OpenRouter:
   ```bash
   curl https://openrouter.ai/api/v1/models \
     -H "Authorization: Bearer $OPENROUTER_API_KEY"
   ```
4. **Check key permissions** - Ensure it's not restricted
5. **Run diagnostic script**: `python3 diagnose_openrouter_auth.py`

### Key Shows as Set But Still Fails?

- The key might be invalid or expired
- Get a new key from https://openrouter.ai/keys
- Check your OpenRouter account status

### Works Locally But Not in Production?

- Ensure environment variable is set in production environment
- Check deployment platform's environment variable settings
- Verify no typos in variable name (it's case-sensitive)

## Next Steps

1. ✅ Set `OPENROUTER_API_KEY` environment variable
2. ✅ Restart the application
3. ✅ Run diagnostic script to verify
4. ✅ Test with a real API request

---

**Related Documentation:**
- OpenRouter API Keys: https://openrouter.ai/keys
- OpenRouter Documentation: https://openrouter.ai/docs
- Model Validation Report: `docs/OPENROUTER_AUTO_VALIDATION.md`
- Testing Guide: `docs/OPENROUTER_AUTO_TESTING_GUIDE.md`
