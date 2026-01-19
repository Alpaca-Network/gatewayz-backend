# Pricing Standardization Fix Plan

**Created:** 2026-01-19
**Priority:** 🔴 CRITICAL
**Impact:** Cost calculation accuracy, billing integrity
**Estimated Time:** 2-3 days

---

## Executive Summary

### Problem
The system has **critical inconsistencies** in how pricing is stored and calculated:
- Database schema claims pricing is "per token"
- Actual data is stored "per 1 million tokens" (from provider APIs)
- Cost calculations multiply tokens × price directly
- **Result:** Costs may be calculated **1,000,000× too high**

### Solution
Standardize all pricing to **per-token format** across the entire system:
1. Normalize provider API responses before storage
2. Migrate existing database values
3. Update cost calculation logic
4. Add comprehensive tests

---

## Phase 1: Investigation & Assessment ⚠️

### 1.1 Verify Database Pricing Values

**Query to check current pricing:**
```sql
-- Check sample of models with pricing
SELECT
    m.model_name,
    p.name as provider,
    m.pricing_prompt,
    m.pricing_completion,
    -- If pricing is per-token, values should be very small (e.g., 0.000000055)
    -- If pricing is per-1M, values will be larger (e.g., 0.055)
    CASE
        WHEN m.pricing_prompt > 0.001 THEN 'Likely per-1M or per-1K'
        WHEN m.pricing_prompt > 0.000001 THEN 'Likely per-1K'
        ELSE 'Likely per-token'
    END as suspected_format
FROM models m
JOIN providers p ON m.provider_id = p.id
WHERE m.pricing_prompt IS NOT NULL
ORDER BY m.pricing_prompt DESC
LIMIT 20;
```

**Action:** Create diagnostic script
```bash
python scripts/utilities/diagnose_pricing_format.py
```

### 1.2 Analyze Recent Cost Calculations

**Query to check recent transactions:**
```sql
-- Check recent cost calculations
SELECT
    ccr.request_id,
    m.model_name,
    ccr.input_tokens,
    ccr.output_tokens,
    ccr.cost_usd,
    ccr.input_cost_usd,
    ccr.output_cost_usd,
    m.pricing_prompt,
    m.pricing_completion,
    -- Reverse engineer the format used
    CASE
        WHEN ccr.input_tokens > 0 THEN
            ccr.input_cost_usd / ccr.input_tokens
    END as calculated_price_per_token
FROM chat_completion_requests ccr
JOIN models m ON ccr.model_id = m.id
WHERE ccr.cost_usd IS NOT NULL
  AND ccr.created_at > NOW() - INTERVAL '7 days'
ORDER BY ccr.created_at DESC
LIMIT 50;
```

**Action:** Review sample transactions to determine actual format

### 1.3 Check Provider API Responses

**Action:** Create test script to fetch live pricing from each provider
```python
# scripts/utilities/test_provider_pricing_format.py
# Fetch from OpenRouter, DeepInfra, etc. and log raw responses
```

**Expected Findings:**
- OpenRouter: Per 1M tokens
- DeepInfra: Per 1M tokens
- AiHubMix: Per 1K tokens
- Featherless: Per 1M tokens
- Together: Per 1M tokens

---

## Phase 2: Design Decision 🎯

### Recommended Standard: **Store Pricing Per-Token**

**Rationale:**
1. ✅ Most granular format (no division needed during calculation)
2. ✅ Matches database schema documentation
3. ✅ Simplifies cost calculation: `cost = tokens × price`
4. ✅ Prevents rounding errors from repeated divisions
5. ✅ Clear semantic meaning in code

**Format Specifications:**
- Input: `pricing_prompt` (DECIMAL 20, 10)
- Output: `pricing_completion` (DECIMAL 20, 10)
- Values: Cost in USD per single token (e.g., `0.000000055` for $0.055/1M)
- Precision: 10 decimal places (sufficient for per-token accuracy)

**Example Conversions:**
```
Provider API → Database Storage
-----------------------------------
OpenRouter: $0.055 per 1M → 0.000000055 per token (÷ 1,000,000)
DeepInfra:  $0.055 per 1M → 0.000000055 per token (÷ 1,000,000)
AiHubMix:   $0.055 per 1K → 0.000000055 per token (÷ 1,000)
```

---

## Phase 3: Implementation Plan 🔧

### 3.1 Create Normalization Utility Function

**File:** `src/services/pricing_normalization.py` (NEW)

```python
"""
Pricing Normalization Utilities
Standardizes pricing from various provider formats to per-token format
"""
import logging
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


class PricingFormat:
    """Enum for pricing formats from different providers"""
    PER_TOKEN = "per_token"
    PER_1K_TOKENS = "per_1k"
    PER_1M_TOKENS = "per_1m"


def normalize_to_per_token(
    price: float | str | Decimal,
    source_format: str = PricingFormat.PER_1M_TOKENS
) -> Optional[Decimal]:
    """
    Normalize pricing from any format to per-token format.

    Args:
        price: Price value from provider API
        source_format: Format of the source price

    Returns:
        Price per single token as Decimal, or None if invalid

    Examples:
        >>> normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        Decimal('0.000000055')

        >>> normalize_to_per_token(0.055, PricingFormat.PER_1K_TOKENS)
        Decimal('0.000055')
    """
    if price is None or price == "":
        return None

    try:
        price_decimal = Decimal(str(price))

        # Handle negative values (dynamic pricing)
        if price_decimal < 0:
            logger.debug(f"Skipping negative/dynamic pricing: {price}")
            return None

        # Normalize based on source format
        if source_format == PricingFormat.PER_TOKEN:
            return price_decimal
        elif source_format == PricingFormat.PER_1K_TOKENS:
            return price_decimal / Decimal("1000")
        elif source_format == PricingFormat.PER_1M_TOKENS:
            return price_decimal / Decimal("1000000")
        else:
            logger.error(f"Unknown pricing format: {source_format}")
            return None

    except (ValueError, TypeError) as e:
        logger.error(f"Failed to normalize price {price}: {e}")
        return None


def normalize_pricing_dict(
    pricing: dict,
    source_format: str = PricingFormat.PER_1M_TOKENS
) -> dict:
    """
    Normalize all pricing fields in a dictionary.

    Args:
        pricing: Dict with 'prompt', 'completion', 'image', 'request' keys
        source_format: Format of source prices

    Returns:
        Dict with normalized per-token prices
    """
    return {
        "prompt": str(normalize_to_per_token(pricing.get("prompt", 0), source_format) or "0"),
        "completion": str(normalize_to_per_token(pricing.get("completion", 0), source_format) or "0"),
        "image": str(normalize_to_per_token(pricing.get("image", 0), source_format) or "0"),
        "request": str(normalize_to_per_token(pricing.get("request", 0), source_format) or "0"),
    }


# Provider-specific format mappings
PROVIDER_PRICING_FORMATS = {
    "openrouter": PricingFormat.PER_1M_TOKENS,
    "deepinfra": PricingFormat.PER_1M_TOKENS,
    "featherless": PricingFormat.PER_1M_TOKENS,
    "together": PricingFormat.PER_1M_TOKENS,
    "fireworks": PricingFormat.PER_1M_TOKENS,
    "nearai": PricingFormat.PER_1M_TOKENS,
    "aihubmix": PricingFormat.PER_1K_TOKENS,
    "groq": PricingFormat.PER_1M_TOKENS,
    "cerebras": PricingFormat.PER_1M_TOKENS,
}


def get_provider_format(provider_slug: str) -> str:
    """Get the pricing format used by a specific provider"""
    return PROVIDER_PRICING_FORMATS.get(
        provider_slug.lower(),
        PricingFormat.PER_1M_TOKENS  # Default assumption
    )
```

### 3.2 Update Provider Normalization Functions

**Files to Update:**

1. **`src/services/models.py`** - Update all `normalize_*_model()` functions
   - Line 3389: AiHubMix ✅ (already correct)
   - Add normalization to: DeepInfra, Featherless, Together, Fireworks, etc.

2. **`src/services/pricing_lookup.py`**
   - Line 159-164: Update cross-reference pricing normalization
   - Line 252-254: Update manual pricing enrichment

**Example Update for DeepInfra:**
```python
# OLD (src/services/models.py - deepinfra normalization)
pricing = {
    "prompt": str(model.get("pricing", {}).get("prompt", "0")),
    "completion": str(model.get("pricing", {}).get("completion", "0")),
}

# NEW
from src.services.pricing_normalization import normalize_pricing_dict, PricingFormat

pricing = normalize_pricing_dict(
    model.get("pricing", {}),
    source_format=PricingFormat.PER_1M_TOKENS
)
```

### 3.3 Update Manual Pricing File

**File:** `src/data/manual_pricing.json`

**Action:** Add metadata to clarify format
```json
{
  "_metadata": {
    "last_updated": "2026-01-19",
    "format": "per_1m_tokens",
    "note": "All prices are per 1 million tokens. System normalizes to per-token on load."
  },
  "deepinfra": {
    "meta-llama/Meta-Llama-3.1-8B-Instruct": {
      "prompt": "0.055",
      "completion": "0.055",
      "request": "0",
      "image": "0"
    }
  }
}
```

**Update loader in `src/services/pricing_lookup.py`:**
```python
def get_model_pricing(gateway: str, model_id: str) -> dict[str, str] | None:
    """Get pricing for a specific model from manual pricing data"""
    try:
        pricing_data = load_manual_pricing()
        # ... existing lookup code ...

        if model_id in gateway_pricing:
            raw_pricing = gateway_pricing[model_id]

            # Normalize to per-token format
            metadata = pricing_data.get("_metadata", {})
            source_format = metadata.get("format", "per_1m_tokens")

            from src.services.pricing_normalization import normalize_pricing_dict, PricingFormat

            format_map = {
                "per_1m_tokens": PricingFormat.PER_1M_TOKENS,
                "per_1k_tokens": PricingFormat.PER_1K_TOKENS,
                "per_token": PricingFormat.PER_TOKEN,
            }

            return normalize_pricing_dict(raw_pricing, format_map.get(source_format))
```

### 3.4 Database Migration

**File:** `supabase/migrations/20260119000000_normalize_pricing_to_per_token.sql` (NEW)

```sql
-- Migration: Normalize all pricing to per-token format
-- Created: 2026-01-19
-- Description: Convert all pricing values from per-1M format to per-token format

-- Step 1: Add temporary columns to track migration
ALTER TABLE "public"."models"
ADD COLUMN IF NOT EXISTS "pricing_format_migrated" BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS "pricing_original_prompt" NUMERIC(20, 10),
ADD COLUMN IF NOT EXISTS "pricing_original_completion" NUMERIC(20, 10);

-- Step 2: Backup original values
UPDATE "public"."models"
SET
    pricing_original_prompt = pricing_prompt,
    pricing_original_completion = pricing_completion
WHERE pricing_prompt IS NOT NULL
   OR pricing_completion IS NOT NULL;

-- Step 3: Detect current format and normalize
-- Assumption: If pricing_prompt > 0.001, it's likely per-1M or per-1K format
-- If pricing_prompt is between 0.000001 and 0.001, it's likely per-1K format
-- If pricing_prompt < 0.000001, it's already per-token format

-- Normalize values that appear to be per-1M (> 0.001)
UPDATE "public"."models"
SET
    pricing_prompt = CASE
        WHEN pricing_prompt > 0.001 THEN pricing_prompt / 1000000.0
        WHEN pricing_prompt BETWEEN 0.000001 AND 0.001 THEN pricing_prompt / 1000.0
        ELSE pricing_prompt
    END,
    pricing_completion = CASE
        WHEN pricing_completion > 0.001 THEN pricing_completion / 1000000.0
        WHEN pricing_completion BETWEEN 0.000001 AND 0.001 THEN pricing_completion / 1000.0
        ELSE pricing_completion
    END,
    pricing_image = CASE
        WHEN pricing_image > 0.001 THEN pricing_image / 1000000.0
        WHEN pricing_image BETWEEN 0.000001 AND 0.001 THEN pricing_image / 1000.0
        ELSE pricing_image
    END,
    pricing_request = CASE
        WHEN pricing_request > 0.001 THEN pricing_request / 1000000.0
        WHEN pricing_request BETWEEN 0.000001 AND 0.001 THEN pricing_request / 1000.0
        ELSE pricing_request
    END,
    pricing_format_migrated = TRUE
WHERE pricing_format_migrated = FALSE;

-- Step 4: Update comments
COMMENT ON COLUMN "public"."models"."pricing_prompt" IS
    'Cost in USD per single token for input/prompt tokens (e.g., 0.000000055 for $0.055 per 1M tokens)';
COMMENT ON COLUMN "public"."models"."pricing_completion" IS
    'Cost in USD per single token for output/completion tokens';
COMMENT ON COLUMN "public"."models"."pricing_image" IS
    'Cost in USD per single image token';
COMMENT ON COLUMN "public"."models"."pricing_request" IS
    'Cost in USD per single request (typically 0)';

-- Step 5: Log migration results
DO $$
DECLARE
    v_migrated_count INTEGER;
    v_avg_prompt_price NUMERIC;
BEGIN
    SELECT COUNT(*), AVG(pricing_prompt)
    INTO v_migrated_count, v_avg_prompt_price
    FROM "public"."models"
    WHERE pricing_format_migrated = TRUE;

    RAISE NOTICE 'Pricing normalization complete: % models migrated, avg prompt price: %',
        v_migrated_count, v_avg_prompt_price;
END $$;

-- Step 6: Verification query
-- This should show very small values (e.g., 0.000000055)
SELECT
    model_name,
    pricing_prompt as new_prompt_price,
    pricing_original_prompt as old_prompt_price,
    CASE
        WHEN pricing_original_prompt > 0 THEN
            ROUND((pricing_original_prompt / pricing_prompt)::NUMERIC, 0)
    END as division_factor
FROM "public"."models"
WHERE pricing_format_migrated = TRUE
ORDER BY pricing_prompt DESC
LIMIT 10;
```

**Rollback Migration:**
```sql
-- File: supabase/migrations/20260119000001_rollback_pricing_normalization.sql

-- Rollback pricing normalization
UPDATE "public"."models"
SET
    pricing_prompt = pricing_original_prompt,
    pricing_completion = pricing_original_completion
WHERE pricing_format_migrated = TRUE;

-- Remove migration columns
ALTER TABLE "public"."models"
DROP COLUMN IF EXISTS "pricing_format_migrated",
DROP COLUMN IF EXISTS "pricing_original_prompt",
DROP COLUMN IF EXISTS "pricing_original_completion";
```

### 3.5 Update Cost Calculation Functions

**File:** `src/services/pricing.py`

**Current Code (Lines 125-127):**
```python
# FIXED: Pricing is per single token, so just multiply (no division)
prompt_cost = prompt_tokens * pricing["prompt"]
completion_cost = completion_tokens * pricing["completion"]
```

**After Migration:** ✅ No changes needed! Code is already correct.

**File:** `src/db/chat_completion_requests_enhanced.py`

**Lines 83-86:** ✅ Already correct - just stores calculated costs

### 3.6 Update Database Views

**File:** `supabase/migrations/20260119000002_update_analytics_view_comments.sql` (NEW)

```sql
-- Update view comments to reflect per-token pricing

COMMENT ON VIEW "public"."model_usage_analytics" IS
    'Comprehensive analytics view showing all models with at least one successful request. '
    'Includes request counts, token usage breakdown (input/output), pricing per token, '
    'and calculated costs (total, input, output, per-request average). '
    'Updated in real-time as new requests are completed. '
    'Useful for cost analysis, usage tracking, and identifying most expensive/popular models. '
    'NOTE: As of 2026-01-19, pricing is stored as cost per single token (e.g., 0.000000055).';

-- Verify calculations are correct (should show reasonable costs)
SELECT
    model_name,
    input_token_price,
    output_token_price,
    successful_requests,
    total_cost_usd,
    avg_cost_per_request_usd
FROM "public"."model_usage_analytics"
ORDER BY total_cost_usd DESC
LIMIT 10;
```

---

## Phase 4: Testing Strategy 🧪

### 4.1 Unit Tests

**File:** `tests/services/test_pricing_normalization.py` (NEW)

```python
"""Tests for pricing normalization utilities"""
import pytest
from decimal import Decimal
from src.services.pricing_normalization import (
    normalize_to_per_token,
    normalize_pricing_dict,
    PricingFormat,
)


class TestPricingNormalization:
    """Test pricing normalization functions"""

    def test_normalize_per_1m_to_per_token(self):
        """Test normalization from per-1M format"""
        result = normalize_to_per_token(0.055, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.000000055")
        assert result == expected

    def test_normalize_per_1k_to_per_token(self):
        """Test normalization from per-1K format"""
        result = normalize_to_per_token(0.055, PricingFormat.PER_1K_TOKENS)
        expected = Decimal("0.000055")
        assert result == expected

    def test_normalize_already_per_token(self):
        """Test normalization when already per-token"""
        result = normalize_to_per_token(0.000000055, PricingFormat.PER_TOKEN)
        expected = Decimal("0.000000055")
        assert result == expected

    def test_normalize_negative_price(self):
        """Test that negative prices (dynamic) return None"""
        result = normalize_to_per_token(-1, PricingFormat.PER_1M_TOKENS)
        assert result is None

    def test_normalize_zero_price(self):
        """Test that zero prices are handled"""
        result = normalize_to_per_token(0, PricingFormat.PER_1M_TOKENS)
        assert result == Decimal("0")

    def test_normalize_pricing_dict(self):
        """Test normalizing full pricing dictionary"""
        pricing = {
            "prompt": "0.055",
            "completion": "0.040",
            "image": "0",
            "request": "0",
        }
        result = normalize_pricing_dict(pricing, PricingFormat.PER_1M_TOKENS)

        assert float(result["prompt"]) == pytest.approx(0.000000055, rel=1e-9)
        assert float(result["completion"]) == pytest.approx(0.000000040, rel=1e-9)
        assert result["image"] == "0"
        assert result["request"] == "0"


class TestCostCalculations:
    """Test that cost calculations are accurate"""

    def test_llama_3_1_8b_cost(self):
        """Test cost calculation for Llama-3.1-8B (known pricing)"""
        # Known: $0.055 per 1M tokens = $0.000000055 per token
        price_per_token = Decimal("0.000000055")
        tokens = 1000

        cost = float(tokens * price_per_token)
        expected_cost = 0.000055  # $0.055 per 1M × 1000 tokens

        assert cost == pytest.approx(expected_cost, rel=1e-6)

    def test_gpt4_cost(self):
        """Test cost calculation for GPT-4 (known pricing)"""
        # GPT-4: ~$30 per 1M input tokens = $0.000030 per token
        price_per_token = Decimal("0.000030")
        tokens = 1000

        cost = float(tokens * price_per_token)
        expected_cost = 0.030  # $30 per 1M × 1000 tokens

        assert cost == pytest.approx(expected_cost, rel=1e-6)
```

### 4.2 Integration Tests

**File:** `tests/integration/test_pricing_end_to_end.py` (NEW)

```python
"""End-to-end tests for pricing system"""
import pytest
from src.services.models import get_cached_models
from src.services.pricing import calculate_cost


@pytest.mark.integration
class TestPricingEndToEnd:
    """Test complete pricing flow from API to cost calculation"""

    def test_pricing_format_in_catalog(self):
        """Verify all models in catalog have per-token pricing"""
        models = get_cached_models("all")

        for model in models[:100]:  # Sample
            pricing = model.get("pricing", {})
            prompt_price = float(pricing.get("prompt", 0) or 0)

            if prompt_price > 0:
                # Per-token prices should be very small (< 0.001)
                assert prompt_price < 0.001, (
                    f"Model {model.get('id')} has suspiciously high "
                    f"pricing: {prompt_price} (expected per-token format)"
                )

    def test_cost_calculation_accuracy(self):
        """Test cost calculation produces reasonable values"""
        # Llama-3.1-8B: $0.055 per 1M tokens
        model_id = "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct"
        prompt_tokens = 1000
        completion_tokens = 500

        cost = calculate_cost(model_id, prompt_tokens, completion_tokens)

        # Expected: (1000 + 500) × 0.000000055 = 0.0000825
        expected = 0.0000825
        tolerance = expected * 0.1  # 10% tolerance

        assert abs(cost - expected) < tolerance, (
            f"Cost {cost} outside expected range {expected} ± {tolerance}"
        )
```

### 4.3 Database Tests

**File:** `tests/db/test_pricing_migration.py` (NEW)

```python
"""Tests for pricing migration"""
import pytest
from src.config.supabase_config import get_supabase_client


@pytest.mark.database
class TestPricingMigration:
    """Test database pricing migration"""

    def test_pricing_values_are_small(self):
        """Verify pricing values are in per-token format (very small)"""
        client = get_supabase_client()

        result = client.table("models").select(
            "model_name, pricing_prompt, pricing_completion"
        ).limit(100).execute()

        for model in result.data:
            prompt_price = float(model.get("pricing_prompt") or 0)
            completion_price = float(model.get("pricing_completion") or 0)

            if prompt_price > 0:
                assert prompt_price < 0.001, (
                    f"{model['model_name']} pricing_prompt too high: {prompt_price}"
                )
            if completion_price > 0:
                assert completion_price < 0.001, (
                    f"{model['model_name']} pricing_completion too high: {completion_price}"
                )

    def test_cost_calculation_in_database(self):
        """Test that database view calculations are correct"""
        client = get_supabase_client()

        result = client.table("model_usage_analytics").select(
            "model_name, "
            "total_input_tokens, "
            "total_output_tokens, "
            "input_token_price, "
            "output_token_price, "
            "total_cost_usd"
        ).limit(10).execute()

        for row in result.data:
            input_tokens = row.get("total_input_tokens", 0)
            output_tokens = row.get("total_output_tokens", 0)
            input_price = float(row.get("input_token_price") or 0)
            output_price = float(row.get("output_token_price") or 0)
            total_cost = float(row.get("total_cost_usd") or 0)

            # Verify calculation
            expected_cost = (input_tokens * input_price) + (output_tokens * output_price)

            if expected_cost > 0:
                # Allow 1% difference for rounding
                assert abs(total_cost - expected_cost) / expected_cost < 0.01
```

### 4.4 Smoke Tests

**Script:** `scripts/utilities/verify_pricing_fix.py` (NEW)

```python
#!/usr/bin/env python3
"""
Smoke test to verify pricing fix is working correctly
Run after deployment to validate pricing calculations
"""
import sys
from decimal import Decimal
from src.services.pricing import get_model_pricing, calculate_cost
from src.config.supabase_config import get_supabase_client


def check_pricing_format():
    """Check that pricing is in correct format"""
    print("✓ Checking pricing format in catalog...")

    # Sample known models
    test_models = [
        "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct",
        "openrouter/anthropic/claude-3-5-sonnet",
    ]

    for model_id in test_models:
        pricing = get_model_pricing(model_id)
        prompt_price = pricing.get("prompt", 0)

        if prompt_price > 0.001:
            print(f"  ✗ FAIL: {model_id} has high pricing: {prompt_price}")
            print(f"    Expected: < 0.001 (per-token)")
            return False
        else:
            print(f"  ✓ {model_id}: {prompt_price} (per-token format)")

    return True


def check_cost_calculation():
    """Check that cost calculations are reasonable"""
    print("\n✓ Checking cost calculations...")

    # Llama-3.1-8B: Should cost ~$0.000055 per 1000 tokens
    cost = calculate_cost(
        "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct",
        1000,
        500
    )

    expected = 0.0000825  # (1000 + 500) × 0.000000055

    if abs(cost - expected) > expected * 0.2:  # 20% tolerance
        print(f"  ✗ FAIL: Cost {cost} too far from expected {expected}")
        return False
    else:
        print(f"  ✓ Cost calculation correct: ${cost:.8f} (expected: ${expected:.8f})")

    return True


def check_database_values():
    """Check database has correct pricing values"""
    print("\n✓ Checking database pricing values...")

    client = get_supabase_client()
    result = client.table("models").select(
        "model_name, pricing_prompt"
    ).gt("pricing_prompt", 0).limit(5).execute()

    all_valid = True
    for row in result.data:
        price = float(row["pricing_prompt"])
        if price > 0.001:
            print(f"  ✗ {row['model_name']}: {price} (too high)")
            all_valid = False
        else:
            print(f"  ✓ {row['model_name']}: {price}")

    return all_valid


def main():
    """Run all checks"""
    print("=" * 60)
    print("PRICING FIX VERIFICATION")
    print("=" * 60)

    checks = [
        ("Pricing Format", check_pricing_format),
        ("Cost Calculation", check_cost_calculation),
        ("Database Values", check_database_values),
    ]

    all_passed = True
    for name, check_fn in checks:
        try:
            if not check_fn():
                all_passed = False
        except Exception as e:
            print(f"\n✗ {name} FAILED with exception: {e}")
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL CHECKS PASSED")
        print("Pricing fix is working correctly!")
        return 0
    else:
        print("✗ SOME CHECKS FAILED")
        print("Please review the output above and fix issues.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

---

## Phase 5: Deployment Plan 🚀

### 5.1 Pre-Deployment Checklist

- [ ] All tests passing locally
- [ ] Code reviewed by at least 2 team members
- [ ] Database backup created
- [ ] Rollback plan tested
- [ ] Staging environment ready
- [ ] Monitoring alerts configured

### 5.2 Deployment Steps

**Step 1: Deploy to Staging**
```bash
# 1. Create feature branch
git checkout -b fix/pricing-standardization

# 2. Apply code changes
# (See Phase 3 implementation)

# 3. Run tests
pytest tests/services/test_pricing_normalization.py -v
pytest tests/integration/test_pricing_end_to_end.py -v

# 4. Deploy to staging
railway environment staging
railway up

# 5. Run staging migration
railway run -e staging python -c "
from src.config.supabase_config import get_supabase_client
# Verify migration ready
"

# Apply migration manually first
psql $STAGING_DATABASE_URL < supabase/migrations/20260119000000_normalize_pricing_to_per_token.sql

# 6. Run smoke tests
python scripts/utilities/verify_pricing_fix.py
```

**Step 2: Validate Staging**
```bash
# Check sample costs
curl https://api-staging.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer $TEST_API_KEY" \
  -d '{"model": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct", "messages": [{"role": "user", "content": "test"}]}'

# Verify cost is reasonable (should be < $0.001 for short test)

# Check analytics endpoint
curl https://api-staging.gatewayz.ai/admin/analytics/cost-analysis?days=1 \
  -H "Authorization: Bearer $ADMIN_KEY"
```

**Step 3: Deploy to Production**
```bash
# 1. Merge to main
git checkout main
git merge fix/pricing-standardization

# 2. Tag release
git tag -a v2.0.4-pricing-fix -m "Fix pricing standardization"
git push origin main --tags

# 3. Deploy to production
railway environment production
railway up

# 4. Database backup
pg_dump $PROD_DATABASE_URL > backup_before_pricing_fix_$(date +%Y%m%d_%H%M%S).sql

# 5. Apply migration (with rollback ready)
psql $PROD_DATABASE_URL < supabase/migrations/20260119000000_normalize_pricing_to_per_token.sql

# 6. Monitor for 10 minutes
# Watch logs, error rates, cost calculations

# 7. Run smoke tests
python scripts/utilities/verify_pricing_fix.py --env production
```

### 5.3 Post-Deployment Validation

**Monitoring Checklist:**
- [ ] Error rate < 0.1% (no increase)
- [ ] Response time < 500ms (no degradation)
- [ ] Cost calculations appear reasonable
- [ ] No spike in Sentry errors
- [ ] Analytics dashboard shows correct costs
- [ ] Sample manual verification of 10 requests

**SQL Validation Queries:**
```sql
-- Check pricing distribution
SELECT
    CASE
        WHEN pricing_prompt < 0.000001 THEN '< $0.001/1M (per-token)'
        WHEN pricing_prompt < 0.001 THEN '$0.001-$1/1M (per-1K)'
        ELSE '> $1/1M (per-1M or error)'
    END as price_range,
    COUNT(*) as model_count
FROM models
WHERE pricing_prompt IS NOT NULL
GROUP BY 1;

-- Should show most models in "< $0.001/1M (per-token)" range

-- Check recent costs are reasonable
SELECT
    DATE_TRUNC('hour', created_at) as hour,
    COUNT(*) as requests,
    SUM(cost_usd) as total_cost,
    AVG(cost_usd) as avg_cost,
    MAX(cost_usd) as max_cost
FROM chat_completion_requests
WHERE created_at > NOW() - INTERVAL '24 hours'
  AND cost_usd IS NOT NULL
GROUP BY 1
ORDER BY 1 DESC;

-- Avg cost should be reasonable (e.g., $0.0001 - $0.01 per request)
```

---

## Phase 6: Rollback Plan 🔄

### If Issues Detected

**Immediate Rollback (< 5 minutes):**
```bash
# 1. Rollback database migration
psql $PROD_DATABASE_URL < supabase/migrations/20260119000001_rollback_pricing_normalization.sql

# 2. Rollback application code
git revert HEAD
git push origin main
railway up

# 3. Verify rollback worked
python scripts/utilities/verify_pricing_fix.py --env production --expect-old-format
```

**Rollback Validation:**
- [ ] Pricing values restored to original
- [ ] Cost calculations match pre-fix values
- [ ] No data loss
- [ ] All services operational

---

## Phase 7: Documentation Updates 📚

### Files to Update

1. **`README.md`**
   - Add note about pricing format standardization

2. **`docs/PRICING.md`** (NEW)
   - Document pricing format: per-token
   - Explain conversion from provider APIs
   - Add examples of calculations

3. **`CHANGELOG.md`**
   ```markdown
   ## [2.0.4] - 2026-01-19
   ### Fixed
   - **CRITICAL:** Standardized pricing to per-token format across all providers
   - Fixed cost calculations that were using inconsistent pricing formats
   - Added normalization for OpenRouter, DeepInfra, and other provider APIs
   - Migrated database pricing values to per-token format
   ```

4. **`src/services/pricing_normalization.py`**
   - Comprehensive docstrings with examples

5. **Database Schema Comments**
   - Updated via migration

---

## Success Criteria ✅

**The fix is successful when:**

1. ✅ All pricing values in database are < 0.001 (per-token format)
2. ✅ Cost calculations produce reasonable values ($0.00001 - $0.10 per request)
3. ✅ Provider API responses are normalized before storage
4. ✅ All tests passing (unit, integration, e2e)
5. ✅ No increase in error rates
6. ✅ Analytics dashboards show consistent cost data
7. ✅ Manual spot-checks confirm accurate costs

---

## Risk Assessment ⚠️

### High Risk Items
1. **Data Migration** - Potential for incorrect conversion
   - Mitigation: Backup, rollback plan, careful testing

2. **Cost Calculation Changes** - Could affect billing
   - Mitigation: Extensive testing, staged rollout, monitoring

### Medium Risk Items
1. **Provider Integration** - Changes to many provider clients
   - Mitigation: Comprehensive tests, gradual rollout

2. **Database Performance** - Decimal calculations
   - Mitigation: Index optimization, query performance testing

### Low Risk Items
1. **Code Complexity** - New normalization module
   - Mitigation: Clear documentation, code review

---

## Timeline Estimate 📅

| Phase | Duration | Owner |
|-------|----------|-------|
| Investigation | 4 hours | Backend Lead |
| Design Decision | 2 hours | Team Discussion |
| Implementation | 8 hours | Backend Dev |
| Testing | 4 hours | QA + Backend |
| Staging Deployment | 2 hours | DevOps |
| Validation | 4 hours | Team |
| Production Deploy | 2 hours | DevOps + Backend Lead |
| Monitoring | 8 hours | On-call rotation |
| **Total** | **34 hours (~2 days)** | |

---

## Communication Plan 📢

### Stakeholders
- Engineering team
- Product team
- Finance team (billing impact)
- Customer support (in case of user questions)

### Notifications
1. **Pre-deployment:** Email to stakeholders with plan
2. **During deployment:** Slack updates in #engineering
3. **Post-deployment:** Summary email with results
4. **Documentation:** Updated pricing docs on website

---

## Appendix

### A. Reference Pricing Examples

| Model | Provider | API Format | API Value | Per-Token | Per 1K | Per 1M |
|-------|----------|------------|-----------|-----------|---------|---------|
| Llama-3.1-8B | DeepInfra | Per 1M | 0.055 | 0.000000055 | 0.000055 | 0.055 |
| GPT-4 | OpenRouter | Per 1M | 30 | 0.000030 | 0.030 | 30 |
| Claude-3 | OpenRouter | Per 1M | 15 | 0.000015 | 0.015 | 15 |

### B. Useful Commands

```bash
# Check current pricing in database
psql $DATABASE_URL -c "SELECT model_name, pricing_prompt FROM models ORDER BY pricing_prompt DESC LIMIT 10;"

# Run full test suite
pytest tests/ -v -m "not slow"

# Deploy to staging
railway environment staging && railway up

# Monitor logs
railway logs -f
```

### C. Contact Information

- **Technical Lead:** [Name] - [Email]
- **DevOps:** [Name] - [Email]
- **On-call:** See PagerDuty schedule

---

**Document Version:** 1.0
**Last Updated:** 2026-01-19
**Next Review:** After production deployment