#!/usr/bin/env python3
"""
Populate model_pricing table with normalized per-token pricing

This script:
1. Fetches all models from the database
2. Normalizes their pricing to per-token format
3. Inserts into the model_pricing table
4. Handles batching for large datasets
"""
import os
import sys
from decimal import Decimal
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from supabase import create_client
from src.services.pricing_normalization import (
    normalize_to_per_token,
    auto_detect_format,
    PricingFormat,
    get_provider_format,
)

# Load environment
load_dotenv()

# Initialize Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    print("❌ Error: SUPABASE_URL or SUPABASE_KEY not set")
    sys.exit(1)

supabase = create_client(supabase_url, supabase_key)

print("=" * 80)
print("POPULATE MODEL PRICING TABLE")
print("=" * 80)
print()


def normalize_model_pricing(model: dict) -> dict:
    """
    Normalize a model's pricing to per-token format

    Args:
        model: Model dict with pricing fields

    Returns:
        Normalized pricing dict
    """
    model_id = model.get("id")
    source_gateway = model.get("source_gateway", "").lower()

    # Get pricing fields
    pricing_prompt = model.get("pricing_prompt")
    pricing_completion = model.get("pricing_completion")
    pricing_image = model.get("pricing_image")
    pricing_request = model.get("pricing_request")

    # Determine source format
    # First check if we have provider-specific format
    provider_format = get_provider_format(source_gateway)

    # If not, auto-detect from the price value
    if pricing_prompt is not None and pricing_prompt > 0:
        detected_format = auto_detect_format(pricing_prompt)
        if detected_format != provider_format:
            # Use detected format if it's more specific
            provider_format = detected_format

    # Normalize each price
    normalized = {
        "model_id": model_id,
        "price_per_input_token": None,
        "price_per_output_token": None,
        "price_per_image_token": None,
        "price_per_request": None,
        "pricing_source": model.get("pricing_source", "provider"),
    }

    if pricing_prompt is not None:
        normalized["price_per_input_token"] = normalize_to_per_token(
            pricing_prompt, provider_format
        )

    if pricing_completion is not None:
        normalized["price_per_output_token"] = normalize_to_per_token(
            pricing_completion, provider_format
        )

    if pricing_image is not None:
        normalized["price_per_image_token"] = normalize_to_per_token(
            pricing_image, provider_format
        )

    if pricing_request is not None:
        # Request pricing is usually already per-request
        normalized["price_per_request"] = float(pricing_request) if pricing_request else None

    # Convert Decimal to float for JSON serialization
    for key in ["price_per_input_token", "price_per_output_token", "price_per_image_token"]:
        if normalized[key] is not None:
            normalized[key] = float(normalized[key])

    return normalized


def main():
    """Main execution"""

    # Step 1: Fetch all models with pricing
    print("1️⃣  Fetching models from database...")
    try:
        response = supabase.table("models").select("*").execute()
        models = response.data
        print(f"   ✅ Found {len(models)} models")
    except Exception as e:
        print(f"   ❌ Error fetching models: {e}")
        sys.exit(1)

    print()

    # Step 2: Normalize pricing
    print("2️⃣  Normalizing pricing to per-token format...")
    pricing_records = []
    skipped = 0

    for model in models:
        # Skip models without pricing
        if not model.get("pricing_prompt") and not model.get("pricing_completion"):
            skipped += 1
            continue

        try:
            normalized = normalize_model_pricing(model)
            pricing_records.append(normalized)
        except Exception as e:
            print(f"   ⚠️  Error normalizing model {model.get('id')}: {e}")
            continue

    print(f"   ✅ Normalized {len(pricing_records)} models")
    print(f"   ⏭️  Skipped {skipped} models (no pricing)")
    print()

    # Step 3: Show sample
    print("3️⃣  Sample normalized pricing (first 5):")
    for record in pricing_records[:5]:
        print(f"   Model {record['model_id']}: "
              f"input=${record['price_per_input_token']:.12f}, "
              f"output=${record['price_per_output_token']:.12f}")
    print()

    # Step 4: Insert into database (batched)
    print("4️⃣  Inserting into model_pricing table...")
    batch_size = 100
    inserted = 0
    errors = 0

    for i in range(0, len(pricing_records), batch_size):
        batch = pricing_records[i:i + batch_size]

        try:
            # Upsert (insert or update if exists)
            supabase.table("model_pricing").upsert(batch).execute()
            inserted += len(batch)
            print(f"   ✅ Inserted batch {i//batch_size + 1} ({len(batch)} records)")
        except Exception as e:
            print(f"   ❌ Error inserting batch: {e}")
            errors += len(batch)
            continue

    print()
    print(f"   ✅ Total inserted: {inserted}")
    print(f"   ❌ Total errors: {errors}")
    print()

    # Step 5: Verify
    print("5️⃣  Verifying inserted data...")
    try:
        response = supabase.table("model_pricing").select("*", count="exact").execute()
        count = response.count
        print(f"   ✅ Total records in model_pricing: {count}")

        # Check sample
        sample = supabase.table("model_pricing").select("*").limit(5).execute()
        print(f"   ✅ Sample records:")
        for record in sample.data:
            print(f"      Model {record['model_id']}: "
                  f"${record['price_per_input_token']:.12f} / "
                  f"${record['price_per_output_token']:.12f}")
    except Exception as e:
        print(f"   ❌ Error verifying: {e}")

    print()
    print("=" * 80)
    print("✅ COMPLETED")
    print("=" * 80)
    print()
    print("Next steps:")
    print("1. Verify data in Supabase Dashboard")
    print("2. Update code to use model_pricing table instead of models.pricing_*")
    print("3. Set up periodic refresh job to keep pricing updated")
    print()


if __name__ == "__main__":
    main()
