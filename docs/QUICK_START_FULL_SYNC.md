# Quick Start: Keep Database Fully Synced (18k Models)

## 🎯 Goal
Sync **all 18,000+ models** from 30 providers to your database, and keep it automatically updated.

---

## ⚡ Super Quick (1 Minute)

### Option 1: Automated Setup Wizard
```bash
./scripts/setup_full_sync.sh
```
This wizard will:
- ✅ Detect your platform (Railway/Vercel/Docker/Local)
- ✅ Update configuration automatically
- ✅ Restart your app
- ✅ Run initial full sync
- ✅ Verify results
- ✅ Set up monitoring (optional)

### Option 2: Manual Quick Sync
```bash
# 1. Sync all providers NOW
./scripts/sync_all_providers_now.sh

# 2. Update config for future syncs
# Add to .env or Railway/Vercel:
PRICING_SYNC_PROVIDERS=openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface

# 3. Restart app
```

---

## 🔧 Platform-Specific Instructions

### Railway
```bash
# 1. Set environment variable
railway variables set PRICING_SYNC_PROVIDERS="openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface"

# 2. Redeploy
railway up

# 3. Wait 30 seconds, then sync
curl -X POST https://your-app.railway.app/admin/model-sync/all
```

### Vercel
```bash
# 1. Add via dashboard or CLI
vercel env add PRICING_SYNC_PROVIDERS
# Paste: openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface

# 2. Redeploy
vercel --prod

# 3. Sync
curl -X POST https://your-app.vercel.app/admin/model-sync/all
```

### Docker
```bash
# 1. Update .env
echo 'PRICING_SYNC_PROVIDERS=openrouter,featherless,deepinfra,groq,fireworks,together,cerebras,nebius,xai,novita,chutes,aimo,near,fal,helicone,anannas,aihubmix,vercel-ai-gateway,google-vertex,openai,anthropic,simplismart,onerouter,cloudflare-workers-ai,clarifai,morpheus,sybil,canopywave,modelz,cohere,huggingface' >> .env

# 2. Restart
docker-compose restart

# 3. Sync
docker exec gatewayz-api curl -X POST http://localhost:8000/admin/model-sync/all
```

### Local Development
```bash
# 1. Copy example config
cp .env.full-sync.example .env

# 2. Update with your API keys

# 3. Restart
uvicorn src.main:app --reload

# 4. Sync
curl -X POST http://localhost:8000/admin/model-sync/all
```

---

## ✅ Verification

### Check Database Count
```bash
# Should show ~18,000
curl https://your-domain.com/admin/model-sync/status | jq '.models.stats.total_active'

# Or use verification script
./scripts/verify_full_sync.sh
```

### Monitor Sync Health
```bash
# Check sync health anytime
python3 scripts/monitor_sync_health.py

# Expected output:
# ✅ STATUS: HEALTHY - Database is fully synced
```

---

## 🔄 What Happens Next

After setup, your system will:

1. **Sync automatically every 6 hours** (configurable)
2. **Keep database at ~18,000 models**
3. **Update pricing from all providers**
4. **Maintain sync status**

No further action needed! 🎉

---

## 📊 Before vs After

### Before
```
Database:  11,000 models
API:       18,000 models
Sync:      61%
Providers: 4/30 synced
Status:    ⚠️  Partial
```

### After
```
Database:  18,000 models
API:       18,000 models
Sync:      100%
Providers: 30/30 synced
Status:    ✅ Full sync
```

---

## 🚨 Troubleshooting

### Issue: Still shows 11k after sync
```bash
# Check logs for errors
tail -f logs/app.log | grep "model sync"

# Try manual sync again
curl -X POST https://your-domain.com/admin/model-sync/all

# Check specific provider
curl -X POST https://your-domain.com/admin/model-sync/provider/groq
```

### Issue: Environment variable not working
```bash
# Verify it's set
echo $PRICING_SYNC_PROVIDERS

# Railway
railway variables

# Vercel
vercel env ls

# Make sure you restarted after setting!
```

### Issue: Sync timeout
```bash
# Sync providers in batches
curl -X POST "/admin/model-sync/all?providers=openrouter&providers=featherless"
# Wait 2 minutes
curl -X POST "/admin/model-sync/all?providers=deepinfra&providers=groq"
# Continue...
```

---

## 📚 Full Documentation

- **Complete Guide**: `docs/KEEP_DB_FULLY_SYNCED.md`
- **Explanation**: `docs/DATABASE_VS_API_MODELS_EXPLAINED.md`
- **Sync Guide**: `docs/MODEL_SYNC_GUIDE.md`
- **Quick Reference**: `docs/SYNC_QUICK_REFERENCE.md`

---

## 📞 Quick Commands

```bash
# Status check
curl https://your-domain.com/admin/model-sync/status | jq

# Full sync
curl -X POST https://your-domain.com/admin/model-sync/all

# Verify count
./scripts/verify_full_sync.sh

# Monitor health
python3 scripts/monitor_sync_health.py

# Check scheduler
curl https://your-domain.com/admin/pricing/sync/{ADMIN_KEY}/scheduler/status
```

---

## 🎉 That's It!

Your database is now fully synced and will stay that way automatically.

**Questions?** Check the full documentation in `docs/` folder.

---

**Last Updated**: 2026-01-27
**Estimated Setup Time**: 5 minutes
