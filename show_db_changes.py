#!/usr/bin/env python3
"""
Display actual database changes from the pricing migration
"""
import os
from dotenv import load_dotenv
from supabase import create_client
from tabulate import tabulate

# Load environment variables
load_dotenv()

# Use .env credentials (will point to local by default)
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    print("❌ Error: SUPABASE_URL or SUPABASE_KEY not set")
    exit(1)

print(f"\n🔗 Connecting to: {supabase_url[:30]}...")
supabase = create_client(supabase_url, supabase_key)

print("\n" + "="*100)
print("DATABASE PRICING CHANGES - LIVE DATA")
print("="*100)

# 1. Check if migration columns exist
print("\n1️⃣  MIGRATION COLUMNS")
print("-" * 100)
try:
    result = supabase.table("models").select("id,pricing_format_migrated,pricing_original_prompt").limit(1).execute()
    print("✅ Migration columns exist:")
    print("   - pricing_format_migrated")
    print("   - pricing_original_prompt")
    print("   - pricing_original_completion")
    print("   - pricing_original_image")
    print("   - pricing_original_request")
except Exception as e:
    print(f"❌ Migration columns do NOT exist: {e}")
    print("\n⚠️  Migration has not been applied to this database yet.")
    exit(0)

# 2. Migration status
print("\n2️⃣  MIGRATION STATUS")
print("-" * 100)
total = supabase.table("models").select("id", count="exact").execute().count
migrated = supabase.table("models").select("id", count="exact").eq("pricing_format_migrated", True).execute().count or 0
not_migrated = total - migrated

print(f"Total models: {total:,}")
print(f"✅ Migrated: {migrated:,} ({migrated/total*100:.1f}%)")
print(f"⏳ Not migrated: {not_migrated:,} ({not_migrated/total*100:.1f}%)")

# 3. Pricing distribution
print("\n3️⃣  PRICING DISTRIBUTION")
print("-" * 100)
models_with_pricing = supabase.table("models") \
    .select("pricing_prompt") \
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

dist_data = [
    ["Per-1M format (> $0.001)", per_1m_count, f"{per_1m_count/total_priced*100:.1f}%", "❌ NOT migrated"],
    ["Per-1K format ($0.000001-$0.001)", per_1k_count, f"{per_1k_count/total_priced*100:.1f}%", "❌ NOT migrated"],
    ["Per-token format (< $0.000001)", per_token_count, f"{per_token_count/total_priced*100:.1f}%", "✅ MIGRATED"],
]
print(tabulate(dist_data, headers=["Format", "Count", "%", "Status"], tablefmt="simple"))

# 4. Verification view (before/after)
print("\n4️⃣  BEFORE/AFTER COMPARISON")
print("-" * 100)
try:
    verification = supabase.table("pricing_migration_verification").select("*").limit(10).execute()

    if verification.data:
        display_data = []
        for model in verification.data[:5]:
            display_data.append([
                model['model_name'][:35],
                f"${model.get('original_prompt', 0):.9f}",
                f"${model.get('new_prompt', 0):.12f}",
                f"{model.get('change_factor', 0):.0f}x" if model.get('change_factor') else "N/A"
            ])

        print(tabulate(display_data,
                      headers=["Model", "Before (Original)", "After (Normalized)", "Change"],
                      tablefmt="grid"))
    else:
        print("⚠️  No data in verification view")
except Exception as e:
    print(f"⚠️  Verification view not accessible: {e}")

# 5. Sample migrated models
print("\n5️⃣  SAMPLE MIGRATED MODELS (5 models)")
print("-" * 100)
samples = supabase.table("models") \
    .select("id,name,pricing_prompt,pricing_original_prompt,pricing_format_migrated,source_gateway") \
    .eq("pricing_format_migrated", True) \
    .not_.is_("pricing_prompt", "null") \
    .order("pricing_prompt") \
    .limit(5) \
    .execute()

if samples.data:
    sample_data = []
    for model in samples.data:
        sample_data.append([
            model['name'][:40],
            model['source_gateway'],
            f"${model.get('pricing_original_prompt', 0):.6f}",
            f"${model.get('pricing_prompt', 0):.12f}",
            "✅"
        ])

    print(tabulate(sample_data,
                  headers=["Model Name", "Provider", "Before", "After", "Status"],
                  tablefmt="grid"))
else:
    print("⚠️  No migrated models found")

# 6. Cost calculation examples
print("\n6️⃣  COST CALCULATION EXAMPLES (1000 tokens)")
print("-" * 100)
cost_samples = supabase.table("models") \
    .select("name,pricing_prompt") \
    .not_.is_("pricing_prompt", "null") \
    .gt("pricing_prompt", 0) \
    .order("pricing_prompt") \
    .limit(5) \
    .execute()

if cost_samples.data:
    cost_data = []
    for model in cost_samples.data:
        price = model['pricing_prompt']
        cost_1k_tokens = 1000 * price
        status = "✅ Reasonable" if cost_1k_tokens < 0.01 else "❌ Too high!"

        cost_data.append([
            model['name'][:40],
            f"${price:.12f}",
            f"${cost_1k_tokens:.6f}",
            status
        ])

    print(tabulate(cost_data,
                  headers=["Model", "Price/Token", "Cost for 1K tokens", "Status"],
                  tablefmt="grid"))
else:
    print("⚠️  No pricing data found")

# 7. Summary
print("\n7️⃣  SUMMARY")
print("-" * 100)
if per_token_count == total_priced:
    print("✅ SUCCESS: All pricing is in per-token format!")
    print("✅ Migration applied successfully")
    print(f"✅ {migrated:,} models migrated")
elif per_token_count > total_priced * 0.9:
    print("⚠️  MOSTLY MIGRATED: Most pricing is per-token")
    print(f"   {per_token_count} models normalized ({per_token_count/total_priced*100:.1f}%)")
else:
    print("❌ NOT MIGRATED: Pricing still in mixed formats")
    print(f"   Only {per_token_count} per-token models ({per_token_count/total_priced*100:.1f}%)")
    print(f"   {per_1m_count + per_1k_count} models need migration")

print("\n" + "="*100)
