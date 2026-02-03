# Provider Pricing API Research

Research findings for expanding pricing auto-sync from 4 to 15 providers.

**Goal**: Reduce reliance on stale manual JSON pricing by implementing auto-sync for more providers.

**Current State**: Only 4 providers auto-synced (OpenRouter, Featherless, Near AI, Alibaba Cloud)

**Target**: 15 providers total (add 11 more)

---

## Currently Auto-Synced Providers (4)

### ✅ OpenRouter
- **API**: `https://openrouter.ai/api/v1/models`
- **Format**: Per-token (already normalized)
- **Fields**: `pricing.prompt`, `pricing.completion`
- **Status**: ✅ Implemented
- **File**: `src/services/pricing_provider_auditor.py::audit_openrouter()`

### ✅ Featherless
- **API**: `https://api.featherless.ai/v1/models`
- **Format**: Per-1M tokens
- **Fields**: `pricing.prompt`, `pricing.completion`
- **Status**: ✅ Implemented
- **File**: `src/services/pricing_provider_auditor.py::audit_featherless()`

### ✅ Near AI
- **API**: `https://api.near.ai/v1/models`
- **Format**: Per-1M tokens
- **Fields**: `pricing.input`, `pricing.output`
- **Status**: ✅ Implemented
- **File**: `src/services/pricing_provider_auditor.py::audit_nearai()`

### ✅ Alibaba Cloud
- **API**: Via SDK or API
- **Format**: Per-1M tokens
- **Status**: ✅ Implemented
- **File**: `src/services/pricing_provider_auditor.py::audit_alibaba_cloud()`

---

## Providers to Add (11)

### 1. Together AI ⭐ HIGH PRIORITY
- **API**: `https://api.together.xyz/v1/models`
- **Format**: Per-1M tokens (likely, need to verify)
- **Fields**: `pricing.input`, `pricing.output`
- **Authentication**: `Bearer {TOGETHER_API_KEY}`
- **Evidence**: Already fetching models from this API in `src/services/together_client.py:187`
- **Code Location**: `src/services/together_client.py::normalize_together_model()` - line 150-153
- **Status**: 🟡 API exists, pricing fields already parsed
- **Action**: Create `audit_together()` method in `pricing_provider_auditor.py`
- **Complexity**: LOW - API already integrated

### 2. Fireworks AI ⭐ HIGH PRIORITY
- **API**: `https://api.fireworks.ai/inference/v1/models` (to verify)
- **Format**: Likely per-1M tokens
- **Evidence**: Has client at `src/services/fireworks_client.py`
- **Status**: 🟡 Need to verify API returns pricing
- **Action**: Test API endpoint, check if pricing included
- **Complexity**: MEDIUM - Need API research

### 3. Groq ⭐ HIGH PRIORITY
- **API**: `https://api.groq.com/openai/v1/models` (likely)
- **Format**: Per-1M tokens
- **Evidence**: Has client at `src/services/groq_client.py`
- **Status**: 🟡 Need to verify API endpoint
- **Action**: Test API, implement fetcher
- **Complexity**: MEDIUM - Need API documentation

### 4. DeepInfra ⭐ HIGH PRIORITY
- **API**: `https://api.deepinfra.com/v1/models` or similar
- **Format**: Per-1M tokens
- **Evidence**: Has client at `src/services/deepinfra_client.py`
- **Note**: Pricing already in `manual_pricing.json`
- **Status**: 🟡 Need API endpoint
- **Action**: Research DeepInfra API docs
- **Complexity**: MEDIUM - Popular provider, should have API

### 5. Cerebras
- **API**: SDK-based (cerebras-cloud-sdk)
- **Format**: Per-1M tokens (likely)
- **Evidence**: Has client at `src/services/cerebras_client.py`
- **Status**: 🔴 May require SDK method call instead of REST API
- **Action**: Check SDK documentation for pricing methods
- **Complexity**: MEDIUM-HIGH - SDK-based, not REST

### 6. Novita AI
- **API**: Unknown
- **Evidence**: Has client at `src/services/novita_client.py`
- **Status**: 🔴 Need research
- **Action**: Check Novita documentation
- **Complexity**: MEDIUM - Less documentation available

### 7. Google Vertex AI
- **API**: Google Cloud Pricing API
- **Format**: Per-1K tokens (different!)
- **Evidence**: Uses Google Cloud SDK
- **Note**: Pricing is public on Google docs, may not need API
- **Status**: 🟡 Can scrape from docs or use Cloud Pricing API
- **Action**: Implement scraper or API client
- **Complexity**: MEDIUM - Well documented but complex API

### 8. X.AI (xAI/Grok)
- **API**: `https://api.x.ai/v1/models` (likely)
- **Format**: Unknown
- **Evidence**: Has client at `src/services/xai_client.py`
- **Status**: 🔴 New provider, limited docs
- **Action**: Test API endpoint
- **Complexity**: MEDIUM - Newer provider

### 9. Cloudflare Workers AI
- **API**: Cloudflare API
- **Format**: Per-1M tokens or per-request
- **Evidence**: Has client at `src/services/cloudflare_workers_ai_client.py`
- **Note**: May have per-request pricing model
- **Status**: 🔴 Need research
- **Action**: Check Cloudflare AI docs
- **Complexity**: MEDIUM-HIGH - Different pricing model

### 10. Morpheus
- **API**: Unknown
- **Evidence**: Has client at `src/services/morpheus_client.py`
- **Status**: 🔴 Need research
- **Action**: Check Morpheus documentation
- **Complexity**: MEDIUM - Less common provider

### 11. Nebius
- **API**: Unknown
- **Evidence**: Has client at `src/services/nebius_client.py`
- **Status**: 🔴 Need research
- **Action**: Check Nebius documentation
- **Complexity**: MEDIUM - Newer provider

---

## Implementation Plan

### Phase 1: Quick Wins (LOW complexity) - Week 1
1. **Together AI** - API already integrated, just extract pricing
2. Test and validate pricing format

### Phase 2: Medium Priority (MEDIUM complexity) - Week 2
3. **Fireworks AI** - Test API endpoint
4. **Groq** - Test API endpoint
5. **DeepInfra** - Research API docs

### Phase 3: Research Required (MEDIUM-HIGH complexity) - Week 3
6. **Google Vertex AI** - Implement pricing scraper
7. **X.AI** - Test new API
8. **Cerebras** - Check SDK pricing methods

### Phase 4: Additional Providers (if time permits) - Week 4
9. **Cloudflare Workers AI**
10. **Novita AI**
11. **Morpheus**
12. **Nebius**

---

## Testing Strategy

For each provider:
1. Test API endpoint manually with API key
2. Verify pricing format (per-token, per-1K, per-1M)
3. Compare with manual_pricing.json for validation
4. Implement audit method in `pricing_provider_auditor.py`
5. Add to `PRICING_SYNC_PROVIDERS` config
6. Run dry-run sync to test
7. Monitor for 24h before enabling
8. Document in `PROVIDER_PRICING_FORMATS`

---

## API Testing Commands

```bash
# Together AI
curl -H "Authorization: Bearer $TOGETHER_API_KEY" \
  https://api.together.xyz/v1/models | jq '.[0].pricing'

# Fireworks (to verify)
curl -H "Authorization: Bearer $FIREWORKS_API_KEY" \
  https://api.fireworks.ai/inference/v1/models | jq '.[0]'

# Groq (to verify)
curl -H "Authorization: Bearer $GROQ_API_KEY" \
  https://api.groq.com/openai/v1/models | jq '.data[0]'

# DeepInfra (to verify)
curl -H "Authorization: Bearer $DEEPINFRA_API_KEY" \
  https://api.deepinfra.com/v1/models | jq '.[0]'
```

---

## Expected Outcomes

- **Auto-sync coverage**: 4 → 15 providers (275% increase)
- **Models with auto-sync**: ~50% → ~85%
- **Stale pricing risk**: Reduced by 60%
- **Manual JSON maintenance**: Reduced from 1,747 lines to ~500 lines
- **Pricing accuracy**: Improved from daily updates to hourly updates

---

## Open Questions

1. **Fireworks, Groq, DeepInfra**: Do their `/v1/models` endpoints return pricing?
2. **Cerebras**: Does SDK expose pricing information?
3. **Cloudflare**: What is the pricing model (per-request vs per-token)?
4. **X.AI**: Is pricing available via API yet?
5. **Smaller providers** (Morpheus, Nebius, Novita): Do they have public APIs?

---

## References

- Issue #1038: Pricing System Audit
- `src/services/pricing_sync_service.py` - Auto-sync implementation
- `src/services/pricing_provider_auditor.py` - Provider API clients
- `src/services/pricing_normalization.py` - Format standardization
- `src/data/manual_pricing.json` - Manual fallback pricing

---

**Last Updated**: 2026-02-03
**Status**: Research in progress
