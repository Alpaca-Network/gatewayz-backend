#!/usr/bin/env python3
"""
Quick script to verify pricing migration was applied successfully
"""
import os
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables from .env file
load_dotenv()

# Get Supabase credentials from environment
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    print("❌ Error: SUPABASE_URL or SUPABASE_KEY not set")
    print("\nSet them with:")
    print("  export SUPABASE_URL='https://your-project.supabase.co'")
    print("  export SUPABASE_KEY='your-service-role-key'")
    exit(1)

# Create Supabase client
supabase = create_client(supabase_url, supabase_key)

print("=" * 80)
print("PRICING MIGRATION VERIFICATION")
print("=" * 80)
print()

# Check if migration column exists
print("1. Checking if migration columns exist...")
try:
    result = supabase.table("models").select("pricing_format_migrated").limit(1).execute()
    print("   ✅ Migration column 'pricing_format_migrated' exists")
except Exception as e:
    print(f"   ❌ Migration column does not exist: {e}")
    exit(1)

print()

# Check migration status
print("2. Checking migration status...")
result = supabase.table("models").select("pricing_format_migrated", count="exact").execute()
total = result.count

migrated = supabase.table("models").select("id", count="exact").eq("pricing_format_migrated", True).execute()
migrated_count = migrated.count

not_migrated = total - migrated_count if migrated_count else total

print(f"   Total models: {total}")
print(f"   ✅ Migrated: {migrated_count}")
print(f"   ⏳ Not migrated: {not_migrated}")
print()

# Check pricing distribution
print("3. Checking pricing distribution...")
models_with_pricing = supabase.table("models") \
    .select("id,name,pricing_prompt,pricing_completion,source_gateway") \
    .not_.is_("pricing_prompt", "null") \
    .limit(1000) \
    .execute()

per_1m_count = 0
per_1k_count = 0
per_token_count = 0

for model in models_with_pricing.data:
    price = model.get("pricing_prompt")
    if price and price > 0:
        if price > 0.001:
            per_1m_count += 1
        elif price >= 0.000001:
            per_1k_count += 1
        else:
            per_token_count += 1

total_priced = per_1m_count + per_1k_count + per_token_count
print(f"   Models analyzed: {total_priced}")
print(f"   Per-1M format (> 0.001): {per_1m_count} ({per_1m_count/total_priced*100:.1f}%)")
print(f"   Per-1K format (0.000001-0.001): {per_1k_count} ({per_1k_count/total_priced*100:.1f}%)")
print(f"   ✅ Per-token format (< 0.000001): {per_token_count} ({per_token_count/total_priced*100:.1f}%)")
print()

# Status
if per_token_count == total_priced:
    print("✅ SUCCESS: All pricing is in per-token format!")
elif per_token_count > total_priced * 0.9:
    print("⚠️  MOSTLY MIGRATED: Most pricing is per-token, but some may need migration")
else:
    print("❌ NOT MIGRATED: Pricing is still in mixed formats - migration may not have run")
print()

# Sample models
print("4. Sample models (5 lowest prices):")
sample = supabase.table("models") \
    .select("id,name,pricing_prompt,source_gateway") \
    .not_.is_("pricing_prompt", "null") \
    .order("pricing_prompt") \
    .limit(5) \
    .execute()

for model in sample.data:
    price = model.get("pricing_prompt")
    print(f"   {model['name'][:40]:40} | ${price:.12f}")

print()
print("=" * 80)
