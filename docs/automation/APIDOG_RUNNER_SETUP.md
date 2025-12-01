# APIdog General Runner Setup for Gatewayz on Railway

This guide explains how to deploy and manage the APIdog General Runner container alongside your Gatewayz API gateway on Railway.

## Overview

The **APIdog General Runner** is a self-hosted testing and automation service that can:
- Execute automated API tests
- Perform performance testing
- Generate test reports
- Integrate with your CI/CD pipeline
- Support multiple programming languages (Node.js 18, Java 21, Python 3, PHP 8)

Your deployment now includes:
- **gateway-api**: Your Gatewayz inference API gateway
- **apidog-runner**: APIdog General Runner for API testing and automation

## Architecture

```
Railway Project
├── gateway-api (FastAPI)
│   ├── Port: $PORT (auto-assigned)
│   ├── Health: /health
│   └── Uvicorn with 1 worker
│
└── apidog-runner (APIdog)
    ├── Port: 4524
    ├── Languages: Node.js 18, Java 21, Python 3, PHP 8
    └── Health: curl http://localhost:4524/health
```

Both services run within the same Railway project:
- Private networking for service-to-service communication
- Shared environment context
- Unified project management

## Deployment Options

### Option 1: Automatic Deployment (Recommended)

Use the provided deployment script to automatically set up the APIdog runner service:

```bash
bash scripts/add-apidog-runner-to-railway.sh
```

The script will:
1. Verify Railway CLI is installed
2. Authenticate with Railway
3. Verify your project is configured
4. Create the apidog-runner service
5. Set required environment variables
6. Verify both services are running

### Option 2: Manual Deployment via Railway CLI

#### Step 1: Prepare Your Railway Environment

```bash
# Install Railway CLI (if not already installed)
npm install -g @railway/cli

# Authenticate
railway login

# Select your project
railway project switch --id <YOUR_PROJECT_ID>
```

#### Step 2: Verify Current Deployment

```bash
# List existing services
railway service list

# You should see: gateway-api (your main API)
```

#### Step 3: Add APIdog Runner Service

```bash
# Create a new empty service
railway service create --name apidog-runner

# Switch to the new service
railway service select --name apidog-runner

# Deploy using the Dockerfile
railway up --service apidog-runner --detach
```

#### Step 4: Configure Environment Variables

Set the required environment variables for the APIdog runner:

```bash
# Switch to apidog-runner service
railway service select --name apidog-runner

# Set APIdog configuration
railway variables set TZ "America/Toronto"
railway variables set SERVER_APP_BASE_URL "https://api.apidog.com"
railway variables set TEAM_ID "529917"
railway variables set RUNNER_ID "12764"
railway variables set ACCESS_TOKEN "TSHGR-D0U71l2GNTXmHWLEEKQy0jQ_21nlElnB"
```

**IMPORTANT:** Keep your `ACCESS_TOKEN` secure! Use Railway's secret variable system (not committed to git).

#### Step 5: Verify Deployment

```bash
# Check service status
railway status

# View logs
railway logs --follow

# Once deployed, get the service URL
railway domain

# Test health endpoint
curl https://<runner-domain>/health
```

### Option 3: Using Docker Compose Locally

Test the setup locally before deploying to Railway:

```bash
# Create a test docker-compose file
cat > docker-compose.apidog-test.yml << 'EOF'
version: '3.8'
services:
  apidog-runner:
    build:
      context: .
      dockerfile: Dockerfile.apidog
    environment:
      TZ: America/Toronto
      SERVER_APP_BASE_URL: https://api.apidog.com
      TEAM_ID: "529917"
      RUNNER_ID: "12764"
      ACCESS_TOKEN: TSHGR-D0U71l2GNTXmHWLEEKQy0jQ_21nlElnB
    ports:
      - "4524:4524"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4524/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
EOF

# Run locally
docker-compose -f docker-compose.apidog-test.yml up

# In another terminal, test the health endpoint
curl http://localhost:4524/health
```

## Environment Variables Reference

### Required Variables

| Variable | Value | Description |
|----------|-------|-------------|
| `TZ` | `America/Toronto` | Container timezone (matches APIdog server) |
| `SERVER_APP_BASE_URL` | `https://api.apidog.com` | APIdog server endpoint |
| `TEAM_ID` | `529917` | Your APIdog team ID |
| `RUNNER_ID` | `12764` | Your APIdog runner ID (unique per runner) |
| `ACCESS_TOKEN` | `TSHGR-D0U71l2GNTXmHWLEEKQy0jQ_21nlElnB` | Authentication token for the runner |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `RUNNER_NAME` | `general-runner` | Display name for this runner |
| `HTTP_PROXY` | (none) | HTTP proxy URL if needed |
| `HTTPS_PROXY` | (none) | HTTPS proxy URL if needed |

### Accessing from Your API

To access the APIdog runner from your gateway-api service:

```python
# In your FastAPI code
import httpx
import os

# Private network URL (within Railway project)
apidog_runner_url = os.getenv(
    "APIDOG_RUNNER_URL",
    "http://apidog-runner.internal:4524"
)

async with httpx.AsyncClient() as client:
    response = await client.get(f"{apidog_runner_url}/health")
```

## File Changes

Your deployment now includes:

```
Repository
├── Dockerfile.apidog              [NEW] Wrapper Dockerfile for APIdog runner
├── railway.json                   [MODIFIED] Now supports multiple services
├── docs/
│   └── APIDOG_RUNNER_SETUP.md    [NEW] This documentation
└── scripts/
    └── add-apidog-runner-to-railway.sh [NEW] Automated deployment script
```

### What Changed in railway.json

- **Before:** Single service configuration for gateway-api
- **After:** Multi-service configuration with:
  - `gateway-api`: Your main Gatewayz API
  - `apidog-runner`: APIdog General Runner service

Both services are defined with consistent restart policies and health checks.

## Monitoring & Health Checks

### Health Check Endpoints

**Gateway API:**
```bash
# Main health check
curl https://<api-domain>/health

# OpenAI-compatible models endpoint
curl https://<api-domain>/v1/models
```

**APIdog Runner:**
```bash
# Runner health
curl https://<runner-domain>/health

# Check runner status
curl https://<runner-domain>/status
```

### Viewing Logs

```bash
# View gateway-api logs
railway logs --follow --service gateway-api

# View apidog-runner logs
railway logs --follow --service apidog-runner

# View logs from both services (latest 50 lines)
railway logs --tail 50
```

### Service Status

```bash
# Check status of all services
railway service list

# Check specific service
railway status --service apidog-runner
```

## Troubleshooting

### Problem: APIdog Runner fails to start

**Symptoms:**
- Container shows as restarting
- Logs show authentication errors

**Solutions:**
```bash
# 1. Verify environment variables are set correctly
railway variables --service apidog-runner

# 2. Check that ACCESS_TOKEN is valid (not expired)
# Contact APIdog support if token needs renewal

# 3. View detailed logs
railway logs --tail 100 --service apidog-runner

# 4. Restart the service
railway up --service apidog-runner --detach
```

### Problem: Health check failing

**Symptoms:**
- Service shows "unhealthy" status
- Container keeps restarting

**Solutions:**
```bash
# 1. Check if port 4524 is correctly configured
# Railway automatically maps container ports

# 2. Verify network connectivity
railway shell --service apidog-runner
curl http://localhost:4524/health

# 3. Increase health check timeout if needed
# Edit railway.json and redeploy
```

### Problem: Can't connect from gateway-api to runner

**Symptoms:**
- Connection timeout when trying to reach runner
- 404 errors on runner endpoints

**Solutions:**
```bash
# 1. Use private network domain
# From gateway-api, use: http://apidog-runner.internal:4524

# 2. Verify both services are in same environment
railway environment list

# 3. Check if services can communicate
railway shell --service gateway-api
curl http://apidog-runner.internal:4524/health

# 4. Review Railway networking docs
# https://docs.railway.app/reference/private-networking
```

### Problem: High memory/CPU usage

**Symptoms:**
- Service using excessive resources
- Railway alerts about resource limits

**Solutions:**
```bash
# 1. Check current resource usage
railway status --service apidog-runner

# 2. Review logs for memory leaks
railway logs --tail 200 --service apidog-runner | grep -i "memory\|error"

# 3. Restart the service to reset state
railway up --service apidog-runner --detach

# 4. Consider scaling up (contact Railway support)
```

## Integration with Your API

### Example: Call APIdog Runner from FastAPI

```python
from fastapi import APIRouter, HTTPException
import httpx
import os

router = APIRouter(prefix="/api/apidog", tags=["APIdog"])

@router.post("/execute-test")
async def execute_apidog_test(test_id: str, payload: dict):
    """Execute an APIdog test via the runner service"""

    runner_url = os.getenv(
        "APIDOG_RUNNER_URL",
        "http://apidog-runner.internal:4524"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{runner_url}/api/tests/{test_id}/execute",
                json=payload
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=500,
            detail=f"APIdog runner error: {str(e)}"
        )

@router.get("/runner-status")
async def get_runner_status():
    """Check APIdog runner health"""

    runner_url = os.getenv(
        "APIDOG_RUNNER_URL",
        "http://apidog-runner.internal:4524"
    )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{runner_url}/health")
            return response.json()
    except httpx.HTTPError:
        return {"status": "unavailable", "runner_url": runner_url}
```

### Example: Environment Setup for Testing

```bash
# In your .env file
APIDOG_RUNNER_URL=http://apidog-runner.internal:4524
APIDOG_TEAM_ID=529917
APIDOG_RUNNER_ID=12764
APIDOG_ACCESS_TOKEN=TSHGR-D0U71l2GNTXmHWLEEKQy0jQ_21nlElnB
```

## Rollback to Single Service (if needed)

If you need to revert to the single-service configuration:

```bash
# 1. Keep current gateway-api running
railway service select --name gateway-api
railway up --detach

# 2. Stop apidog-runner service
railway service select --name apidog-runner
railway down

# 3. Revert railway.json to single-service format
git checkout railway.json

# 4. Redeploy gateway-api
railway up --service gateway-api --detach
```

## Performance & Scaling

### Initial Configuration

- **gateway-api**: 1 replica, auto-scaling enabled
- **apidog-runner**: 1 replica, stateless design
- **Health checks**: 30s interval, 10s timeout

### Scaling Up

For high-traffic scenarios:

```bash
# Increase gateway-api replicas
railway scale --service gateway-api --replicas 2

# Note: APIdog runner should remain as 1 replica
# (runners are designed for single-instance deployment)
```

### Monitoring Resource Usage

```bash
# View resource metrics
railway metrics --service apidog-runner --range 1d

# Export metrics for analysis
railway metrics --service gateway-api --range 7d > api_metrics.json
```

## Cost Considerations

**Typical Railway costs for this setup:**
- **gateway-api**: Depends on traffic and computation
- **apidog-runner**: ~$5-10/month for 1 replica (idle)
- **Private networking**: Free within same project
- **Egress**: Charged at Railway rates for external API calls

**Cost optimization:**
- Monitor unused test executions
- Consider auto-stop for development environments
- Use Railway's analytics to identify waste

## Next Steps

1. **Test the deployment:**
   ```bash
   curl https://<runner-domain>/health
   ```

2. **Configure APIdog in your workflow:**
   - Create test scenarios in APIdog
   - Set up test schedules
   - Configure notifications

3. **Integrate with your CI/CD:**
   - Add APIdog tests to GitHub Actions
   - Trigger tests on deployment
   - Monitor test results

4. **Monitor and maintain:**
   - Check logs regularly
   - Review health metrics
   - Update runner credentials as needed

## Support & Resources

### Documentation
- [APIdog General Runner Docs](https://docs.apidog.com/general-runner)
- [Railway Services Guide](https://docs.railway.app/guides/services)
- [Railway Private Networking](https://docs.railway.app/reference/private-networking)

### Troubleshooting
- Check Railway logs: `railway logs --follow`
- Review APIdog runner status: `curl https://<runner-domain>/health`
- Contact APIdog support: support@apidog.com

### Common Commands

```bash
# Check service health
railway status --service apidog-runner

# View real-time logs
railway logs --follow --service apidog-runner

# Restart service
railway up --service apidog-runner --detach

# Update environment variables
railway variables set KEY VALUE --service apidog-runner

# Check resource usage
railway metrics --service apidog-runner

# Shell into container for debugging
railway shell --service apidog-runner
```

---

**Last Updated:** 2025-11-25
**Version:** 2.0.3
**System:** Gatewayz Universal Inference API
**Status:** Production Ready
