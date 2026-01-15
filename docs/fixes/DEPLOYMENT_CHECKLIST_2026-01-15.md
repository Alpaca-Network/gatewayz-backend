# Deployment Checklist - Model Pricing Fix

**Date**: 2026-01-15
**Branch**: `terragon/fix-backend-errors-tn4o03`
**Commit**: `4d58d1f`

## Status

✅ Code changes committed and pushed
⏳ Database migration pending
⏳ Deployment verification pending

---

## 1. Run Database Migration

The SQL migration needs to be applied to the Supabase production database.

### Option A: Supabase Dashboard (Recommended)

1. Go to [Supabase Dashboard](https://app.supabase.com)
2. Select the Gatewayz project
3. Navigate to **SQL Editor**
4. Copy and paste the contents of:
   ```
   supabase/migrations/20260115000000_add_missing_model_pricing.sql
   ```
5. Click **Run** to execute the migration
6. Verify success message

### Option B: Supabase CLI

```bash
# Connect to your project
supabase link --project-ref YOUR_PROJECT_REF

# Apply the migration
supabase db push

# Or run directly
psql $DATABASE_URL < supabase/migrations/20260115000000_add_missing_model_pricing.sql
```

### What the Migration Does

- Adds 8 models with proper pricing to `models_catalog` table
- Uses `ON CONFLICT DO UPDATE` - safe to run multiple times
- Includes model ID variants (meta/, mistralai/, bfl/ prefixes)
- Updates existing records if they already exist

---

## 2. Deploy Code Changes

The code is already pushed to the branch. Railway should auto-deploy when merged to main, or you can manually trigger:

### Files Changed
1. `src/services/cohere_client.py` - NEW
2. `src/services/model_catalog_sync.py` - Modified
3. `src/services/model_transformations.py` - Modified
4. `supabase/migrations/20260115000000_add_missing_model_pricing.sql` - NEW
5. `docs/fixes/PRICING_FIX_SUMMARY_2026-01-15.md` - NEW

### Deployment Options

**Option A: Merge to Main (Recommended)**
```bash
# Create PR and merge
gh pr create --base main --head terragon/fix-backend-errors-tn4o03 \
  --title "fix(pricing): correct pricing for 8 models using default fallback" \
  --body "See commit message for full details"

# After review and merge, Railway will auto-deploy
```

**Option B: Manual Deploy via Railway**
```bash
# Trigger deployment manually
railway up --service gatewayz-backend --environment production
```

---

## 3. Trigger Model Catalog Sync

After deployment, run a full catalog sync to pull the latest models:

### Option A: Via Railway CLI
```bash
railway run python -m src.services.model_catalog_sync --project gatewayz-backend
```

### Option B: Via API Endpoint (if available)
```bash
curl -X POST https://api.gatewayz.ai/admin/sync-models \
  -H "Authorization: Bearer $ADMIN_API_KEY"
```

---

## 4. Verification Checklist

After deployment and migration:

### Database Verification
```sql
-- Check that all 8 models are in catalog with pricing
SELECT id, name, input_cost_per_token, output_cost_per_token
FROM models_catalog
WHERE id IN (
  'deepseek/deepseek-chat',
  'google/gemini-2.0-flash',
  'mistral/mistral-large',
  'meta-llama/llama-3-8b-instruct',
  'cohere/command-r-plus',
  'alibaba/qwen-3-14b',
  'black-forest-labs/flux-1.1-pro',
  'bytedance/sdxl-lightning-4step'
);
```

### API Testing
```bash
# Test each model - should NOT show pricing warnings in logs
curl https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek/deepseek-chat",
    "messages": [{"role": "user", "content": "test"}],
    "max_tokens": 10
  }'

# Repeat for other models:
# - google/gemini-2.0-flash
# - mistral/mistral-large
# - meta/llama-3-8b-instruct
# - cohere/command-r-plus
```

### Log Monitoring
```bash
# Check Railway logs - should show NO warnings
railway logs --service gatewayz-backend | grep "not found in catalog"

# Expected: Empty output (no warnings)
```

### Pricing Check Script
```bash
# Run the verification script
python scripts/utilities/check_model_pricing.py --from-warnings

# Expected: All 8 models show "HAS PRICING" status
```

---

## 5. Rollback Plan (If Needed)

If issues occur after deployment:

### Revert Code Changes
```bash
git revert 4d58d1f
git push origin terragon/fix-backend-errors-tn4o03
```

### Rollback Database Migration
```sql
-- Delete the added models (only if needed)
DELETE FROM models_catalog WHERE id IN (
  'deepseek/deepseek-chat',
  'google/gemini-2.0-flash',
  'mistral/mistral-large',
  'meta-llama/llama-3-8b-instruct',
  'cohere/command-r-plus',
  'alibaba/qwen-3-14b',
  'black-forest-labs/flux-1.1-pro',
  'bytedance/sdxl-lightning-4step'
);
```

---

## Expected Impact

### Before Fix
- 8 models using default pricing: $20/1M tokens
- Users overcharged by 8x to 667x
- Estimated overcharge: ~$2,000/month

### After Fix
- 8 models using actual provider pricing: $0.03 to $10/1M tokens
- Accurate billing aligned with provider costs
- Customer savings: ~$1,850-1,950/month

---

## Support

If issues arise:
1. Check Railway logs: `railway logs --service gatewayz-backend`
2. Check Sentry for errors
3. Verify database records with SQL query above
4. Review full documentation: `docs/fixes/PRICING_FIX_SUMMARY_2026-01-15.md`

---

**Next Steps**:
1. ✅ Run database migration (Step 1)
2. ⏳ Merge and deploy code (Step 2)
3. ⏳ Trigger catalog sync (Step 3)
4. ⏳ Verify all checks pass (Step 4)
