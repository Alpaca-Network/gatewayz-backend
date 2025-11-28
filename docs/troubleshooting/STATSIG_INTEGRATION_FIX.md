# Statsig Integration - Troubleshooting & Fix Guide

## Problem Summary

The diagnostic script revealed that Statsig integration is **not working** because:

1. ❌ **Missing Environment Variable**: `STATSIG_SERVER_SECRET_KEY` is not set
2. ❌ **Missing Python Package**: `statsig-python-core` is not installed
3. ⚠️ **Service in Fallback Mode**: Events are only logged to console, not sent to Statsig

---

## Quick Fix (3 Steps)

### Step 1: Install the Python Package

The backend uses the `statsig-python-core` SDK, which is currently **not installed**.

```bash
pip install statsig-python-core
```

Or add to your `requirements.txt` if not already there and reinstall:

```bash
pip install -r requirements.txt
```

**Verify it's in requirements.txt:**
```bash
grep statsig requirements.txt
# Should show: statsig-python-core==0.10.2
```

---

### Step 2: Set Environment Variable

You need to set your Statsig server secret key in your environment.

#### Option A: Using `.env` file (Recommended for local development)

1. Copy the example if you don't have a `.env` file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your Statsig key:
   ```bash
   # In .env file
   STATSIG_SERVER_SECRET_KEY=secret-your-actual-server-secret-key-here
   ```

3. Get your key from Statsig:
   - Go to: https://console.statsig.com
   - Navigate to: **Project Settings** → **API Keys** → **Server Secret Key**
   - Copy the key that starts with `secret-`

#### Option B: Environment variable (Production/Railway/Vercel)

Set the environment variable in your deployment platform:

**Railway:**
```bash
railway variables set STATSIG_SERVER_SECRET_KEY=secret-your-key-here
```

**Vercel:**
```bash
vercel env add STATSIG_SERVER_SECRET_KEY
# Then paste your key when prompted
```

**Docker:**
```bash
docker run -e STATSIG_SERVER_SECRET_KEY=secret-your-key-here ...
```

---

### Step 3: Restart Your Application

After installing the package and setting the environment variable, restart your app:

```bash
# Kill any running instances
pkill -f "python.*main.py" || pkill -f uvicorn

# Restart
python src/main.py
# OR
uvicorn src.main:app --reload
```

---

## Verification

### Test 1: Run the Diagnostic Script

```bash
python scripts/diagnostics/check_statsig_integration.py
```

**Expected output (all checks should pass):**
```
============================================================
SUMMARY
============================================================
  ✅ PASS - Environment Variables
  ✅ PASS - Package Installation
  ✅ PASS - Service Initialization
  ✅ PASS - Event Logging
  ✅ PASS - Analytics Route
  ✅ PASS - Main App Integration

🎉 All checks passed! Statsig integration is working correctly.
```

### Test 2: Check Application Startup Logs

When you start the app, you should see:

```
✅ Statsig SDK initialized successfully
   Environment: development
   Server Key: secret-abc...
```

**NOT this (fallback mode):**
```
⚠️  STATSIG_SERVER_SECRET_KEY not set - Statsig analytics disabled (using fallback)
```

### Test 3: Send a Test Event via API

```bash
# Test the analytics endpoint
curl -X POST http://localhost:8000/v1/analytics/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "test_event",
    "user_id": "test_user_123",
    "value": "test_value",
    "metadata": {
      "source": "manual_test",
      "timestamp": "2025-11-28"
    }
  }'
```

**Expected response:**
```json
{
  "success": true,
  "message": "Event 'test_event' logged successfully"
}
```

### Test 4: Verify in Statsig Dashboard

1. Go to: https://console.statsig.com
2. Navigate to: **Metrics** → **Events**
3. Look for events like:
   - `test_event` (from your test)
   - Other events your app logs

**Note:** Events may take 1-2 minutes to appear in the dashboard.

---

## Common Issues & Solutions

### Issue 1: "statsig_python_core not found"

**Cause:** Package not installed

**Fix:**
```bash
pip install statsig-python-core
pip freeze | grep statsig  # Verify installation
```

---

### Issue 2: "Statsig service in fallback mode"

**Cause:** `STATSIG_SERVER_SECRET_KEY` not set or incorrect

**Fix:**
1. Check if variable is set:
   ```bash
   python3 -c "import os; print(os.getenv('STATSIG_SERVER_SECRET_KEY', 'NOT SET'))"
   ```

2. If NOT SET, add to `.env`:
   ```bash
   echo "STATSIG_SERVER_SECRET_KEY=secret-your-key-here" >> .env
   ```

3. Restart the app

---

### Issue 3: Events sent but not showing in Statsig

**Possible causes:**
1. **Wrong API key** - Using client key instead of server secret key
   - Server keys start with `secret-`
   - Client keys start with `client-`
   - Make sure you're using the **Server Secret Key**

2. **Wrong project** - Key is for a different Statsig project
   - Verify you're looking at the correct project in Statsig dashboard

3. **Rate limiting** - Too many events too quickly
   - Check Statsig status: https://status.statsig.com

4. **Network issues** - Firewall blocking Statsig API
   - Test connectivity: `curl -I https://statsigapi.net`

---

### Issue 4: No events being logged from application

**Cause:** Analytics endpoint not being called

**Check:**
1. Verify the route is loaded:
   ```bash
   grep "Analytics Events" /tmp/route_loading_debug.txt
   # Should show: [OK] Analytics Events (analytics)
   ```

2. Check if you're calling the endpoint from your frontend:
   ```javascript
   // Frontend should call:
   await fetch('/v1/analytics/events', {
     method: 'POST',
     headers: { 'Content-Type': 'application/json' },
     body: JSON.stringify({
       event_name: 'button_clicked',
       metadata: { button: 'submit' }
     })
   });
   ```

3. Add direct logging in your Python code:
   ```python
   from src.services.statsig_service import statsig_service

   # Log event directly
   statsig_service.log_event(
       user_id="user_123",
       event_name="important_action",
       metadata={"action": "purchase"}
   )
   ```

---

## Integration Architecture

### How It Works

```
Frontend/Client
      ↓
POST /v1/analytics/events
      ↓
src/routes/analytics.py
      ↓
src/services/statsig_service.py
      ↓
Statsig Python Core SDK
      ↓
Statsig API (https://statsigapi.net)
      ↓
Statsig Dashboard (console.statsig.com)
```

### Key Files

1. **Service:** `src/services/statsig_service.py`
   - Statsig SDK wrapper
   - Handles initialization, event logging, feature flags

2. **Route:** `src/routes/analytics.py`
   - REST API endpoints
   - `/v1/analytics/events` - Single event
   - `/v1/analytics/batch` - Multiple events

3. **Main App:** `src/main.py`
   - Initializes Statsig on startup (line ~530)
   - Shuts down Statsig on teardown (line ~594)
   - Loads analytics route (line 338)

4. **Config:** `.env`
   - `STATSIG_SERVER_SECRET_KEY` - Your server key
   - `APP_ENV` - Environment (development/staging/production)

---

## Environment Variables Reference

```bash
# Required for Statsig to work
STATSIG_SERVER_SECRET_KEY=secret-your-server-secret-key

# Optional - controls which environment in Statsig
APP_ENV=development  # or staging, production
```

**Important:**
- Use **Server Secret Key** (starts with `secret-`)
- **NOT** Client SDK Key (starts with `client-`)

---

## Testing Checklist

Use this checklist to verify your integration:

- [ ] Package installed: `pip list | grep statsig`
- [ ] Environment variable set: `echo $STATSIG_SERVER_SECRET_KEY`
- [ ] `.env` file contains `STATSIG_SERVER_SECRET_KEY=secret-...`
- [ ] Diagnostic script passes: `python scripts/diagnostics/check_statsig_integration.py`
- [ ] App starts without warnings about Statsig fallback mode
- [ ] Can send test event via API: `POST /v1/analytics/events`
- [ ] Events appear in Statsig dashboard within 1-2 minutes
- [ ] No errors in application logs related to Statsig

---

## Additional Resources

- **Statsig Console:** https://console.statsig.com
- **Statsig Documentation:** https://docs.statsig.com/server/pythonSDK
- **Python SDK GitHub:** https://github.com/statsig-io/python-sdk
- **API Reference:** `docs/api.md`
- **Feature Flags Guide:** `docs/features/STATSIG_FEATURE_FLAGS.md`

---

## Getting Help

If you're still having issues after following this guide:

1. **Check diagnostic output:**
   ```bash
   python scripts/diagnostics/check_statsig_integration.py
   ```

2. **Check application logs:**
   ```bash
   tail -f logs/app.log  # or wherever your logs are
   ```

3. **Enable debug logging:**
   ```python
   # In src/services/statsig_service.py
   logging.basicConfig(level=logging.DEBUG)
   ```

4. **Verify Statsig service status:**
   - Visit: https://status.statsig.com

5. **Contact Statsig support:**
   - Dashboard: Settings → Support
   - Or check their documentation

---

## Summary

**The integration is coded correctly**, but it's not working because:

1. The Python package `statsig-python-core` is not installed
2. The environment variable `STATSIG_SERVER_SECRET_KEY` is not set

**Fix:**
```bash
# Install package
pip install statsig-python-core

# Set environment variable in .env
echo "STATSIG_SERVER_SECRET_KEY=secret-your-actual-key" >> .env

# Restart app
python src/main.py
```

After these steps, Statsig integration will be fully functional! 🎉
