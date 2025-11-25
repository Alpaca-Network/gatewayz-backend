# APIdog General Runner - Quick Start Guide

Get the APIdog General Runner deployed on Railway in 5 minutes.

## Prerequisites

- Railway account and project already set up
- Railway CLI installed: `npm install -g @railway/cli`
- Already authenticated: `railway login`
- APIdog access token (from your APIdog account)

## Quick Start (2 Steps)

### Step 1: Run the Deployment Script

```bash
cd /root/repo
bash scripts/add-apidog-runner-to-railway.sh
```

The script will:
- ✅ Verify your Railway setup
- ✅ Deploy the apidog-runner service
- ✅ Configure environment variables
- ✅ Verify both services are running

### Step 2: Verify Deployment

```bash
# Check service status
railway service list

# View logs
railway logs --follow --service apidog-runner

# Test health endpoint (once domain is assigned)
curl https://<runner-domain>/health
```

Done! 🎉

## Manual Deployment (If Script Doesn't Work)

### Step 1: Switch to Your Project

```bash
railway project switch --id <YOUR_PROJECT_ID>
```

### Step 2: Create APIdog Runner Service

```bash
railway service create --name apidog-runner
railway service select --name apidog-runner
railway up --detach
```

### Step 3: Set Environment Variables

```bash
railway variables set TZ "America/Toronto"
railway variables set SERVER_APP_BASE_URL "https://api.apidog.com"
railway variables set TEAM_ID "529917"
railway variables set RUNNER_ID "12764"
railway variables set ACCESS_TOKEN "YOUR_TOKEN_HERE"
```

### Step 4: Check Status

```bash
railway logs --tail 50 --service apidog-runner
railway domain --service apidog-runner
```

## Configuration

### Environment Variables

| Variable | Value |
|----------|-------|
| TZ | America/Toronto |
| SERVER_APP_BASE_URL | https://api.apidog.com |
| TEAM_ID | 529917 |
| RUNNER_ID | 12764 |
| ACCESS_TOKEN | Your APIdog token |

### Update Variables

```bash
railway variables set KEY VALUE --service apidog-runner
```

## Troubleshooting

### Service won't start

```bash
# Check logs for errors
railway logs --tail 100 --service apidog-runner

# Verify variables are set
railway variables --service apidog-runner

# Restart the service
railway up --service apidog-runner --detach
```

### Can't access health endpoint

```bash
# Get the service domain
railway domain --service apidog-runner

# Test from your machine
curl https://<domain>/health

# Or shell into the container
railway shell --service apidog-runner
curl http://localhost:4524/health
```

### Both services running but can't communicate

Use private networking:
```python
# From gateway-api code
apidog_url = "http://apidog-runner.internal:4524"
```

## Common Commands

```bash
# View all services
railway service list

# Switch to a service
railway service select --name apidog-runner

# View service logs
railway logs --follow --service apidog-runner

# Get service domain
railway domain --service apidog-runner

# View environment variables
railway variables --service apidog-runner

# Update a variable
railway variables set KEY VALUE --service apidog-runner

# Restart service
railway up --service apidog-runner --detach

# Check resource usage
railway metrics --service apidog-runner

# Shell into container
railway shell --service apidog-runner
```

## Next Steps

1. ✅ Deployment complete
2. 📖 Read the full guide: [APIDOG_RUNNER_SETUP.md](APIDOG_RUNNER_SETUP.md)
3. 🔌 Integrate with your API code
4. 📊 Monitor logs and metrics
5. 🚀 Create your first APIdog test

## Support

- **APIdog Documentation**: https://docs.apidog.com/general-runner
- **Railway Documentation**: https://docs.railway.app/guides/services
- **Full Setup Guide**: [APIDOG_RUNNER_SETUP.md](APIDOG_RUNNER_SETUP.md)

---

**Need help?** Check the troubleshooting section or review the full documentation.
