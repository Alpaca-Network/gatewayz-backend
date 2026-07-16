# Gatewayz Production Pricing Audit Report
**Date:** 2026-01-22  
**Database:** https://ynleroehyrmaafkgjgmr.supabase.co  
**Auditor:** Claude Code via Protocol

---

## Executive Summary

### Current State
- **Total Models:** 10,856
- **Models in `model_pricing` table:** 10,834 (99.8%)
- **Models with VALID pricing:** 1,573 (14.5%) ✅
- **Models with ZERO pricing:** 9,261 (85.3%) 🔴
- **Models missing `model_pricing` entry:** 22 (0.2%)

### Critical Issues

#### 🔴 Issue #1: 85% of models appear as "FREE"
- **9,261 models have $0.00 pricing** in the `model_pricing` table
- This makes them appear as free models when they're not
- Users could theoretically use these models without credits being deducted

#### 🔴 Issue #2: Pricing never fetched from providers
- These 9,261 models never had pricing data populated
- The `models` table lacks `pricing_original_*` columns for these models
- Root cause: Provider API pricing not fetched during model sync

#### ⚠️ Issue #3: 22 models completely missing from `model_pricing`
- Small gap but should be filled for completeness

---

## Detailed Findings

### Providers with ZERO-pricing models (Top 15)
| Provider | Models with $0 Pricing |
|----------|------------------------|
| deepinfra | 208 |
| chutes | 103 |
| vercel-ai-gateway | 86 |
| novita | 83 |
| openai | 79 |
| fal | 69 |
| cloudflare-workers-ai | 59 |
| modelz | 50 |
| nebius | 45 |
| aihubmix | 44 |
| together | 40 |
| openrouter | 32 |
| fireworks | 20 |
| aimo | 19 |
| helicone | 18 |

### Providers with VALID pricing (Top 15)
| Provider | Models with Pricing |
|----------|---------------------|
| aihubmix | 394 |
| openrouter | 260 |
| aimo | 117 |
| together | 63 |
| anannas | 46 |
| vercel-ai-gateway | 31 |
| featherless | 17 |
| fireworks | 15 |
| simplismart | 14 |
| deepinfra | 9 |

### Pricing Format Validation
Sample of models WITH pricing:
```
✓ $0.000000100 per token (gemini-2.0-flash)
✓ $0.000000075 per token
✓ $0.000002000 per token
```
**All non-zero pricing is in correct per-token format (<$0.001)** ✅

---

## Root Cause Analysis

### Why 9,261 models have zero pricing:

1. **Provider APIs don't expose pricing**
   - Some providers (Chutes, Nosana, etc.) don't have pricing in their API responses
   - The model sync only stores pricing if provider returns it

2. **Manual pricing not applied**
   - `src/data/manual_pricing.json` exists but is only used for specific models
   - Not all models are enriched with manual pricing

3. **Cross-reference pricing not used**
   - `pricing_lookup.py` has cross-reference logic (use OpenRouter as source)
   - But this isn't applied automatically to all models

4. **Normalization only applied to existing data**
   - The migration `20260119000000_normalize_pricing_to_per_token.sql` normalized existing pricing
   - But if no pricing existed, it set it to 0

---

## Recommended Solution

### Phase 1: Immediate (Fix 9,261 zero-pricing models)
1. Run pricing enrichment for all models using `pricing_lookup.py`
2. Apply manual pricing from `manual_pricing.json`
3. Use OpenRouter cross-reference for gateway providers
4. Update `model_pricing` table with normalized values

### Phase 2: Systematic (Prevent future gaps)
1. Add database trigger to auto-populate `model_pricing` when models are inserted
2. Add pricing validation in model sync (warn if pricing missing)
3. Expand `manual_pricing.json` for all providers
4. Add automated pricing sync job (daily)

### Phase 3: Validation
1. Verify all 10,856 models have non-zero pricing
2. Validate pricing is in correct per-token format
3. Add monitoring alerts for zero-pricing models
4. Create pricing accuracy tests

---

## Success Metrics

- ✅ **100% coverage:** All 10,856 models have `model_pricing` entries
- ✅ **0 zero-pricing models:** (except legitimately free models with `:free` suffix)
- ✅ **All prices < $0.001:** Proper per-token format
- ✅ **Automated sync:** New models get pricing automatically

---

## Next Steps

1. **Update GitHub issues** with this audit data
2. **Apply pricing enrichment** to 9,261 models
3. **Implement automated sync** trigger
4. **Deploy validation** scripts

