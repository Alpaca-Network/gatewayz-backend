#!/usr/bin/env python3
"""
Cleanup script to fix malformed model names in the database.

This script:
1. Identifies models with malformed names (containing : or ())
2. Cleans the names using the standardized cleaning function
3. Updates the database with the cleaned names
4. Provides a summary of changes made
"""
import os
import sys
from typing import List, Dict, Tuple

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.supabase_config import get_supabase_client
from src.utils.model_name_validator import clean_model_name, validate_model_name


def cleanup_malformed_model_names(dry_run: bool = True) -> Tuple[int, List[Dict]]:
    """
    Clean up malformed model names in the database.

    Args:
        dry_run: If True, only show what would be changed without actually updating

    Returns:
        Tuple of (count of updated models, list of change records)
    """
    supabase = get_supabase_client()

    print("=" * 80)
    if dry_run:
        print("DRY RUN MODE - No changes will be made to the database")
    else:
        print("LIVE MODE - Database will be updated")
    print("=" * 80)
    print()

    # Fetch all models
    print("📊 Fetching all models from database...")
    response = supabase.table("models").select(
        "model_id, model_name, provider_id, providers(name)"
    ).execute()

    models = response.data
    print(f"✅ Found {len(models)} total models\n")

    # Identify malformed names
    malformed_models = []
    for model in models:
        model_name = model.get("model_name", "")
        if not model_name:
            continue

        # Check if name is malformed
        is_valid, error_msg = validate_model_name(model_name)
        if not is_valid:
            malformed_models.append(model)

    print(f"❌ Found {len(malformed_models)} models with malformed names\n")

    if not malformed_models:
        print("✨ All model names are already clean! Nothing to do.")
        return 0, []

    # Process each malformed model
    changes_made = []
    success_count = 0
    error_count = 0

    print("=" * 80)
    print("PROCESSING MALFORMED NAMES")
    print("=" * 80)
    print()

    for i, model in enumerate(malformed_models, 1):
        model_id = model["model_id"]
        old_name = model["model_name"]
        provider_name = model.get("providers", {}).get("name", "Unknown")

        # Clean the name
        new_name = clean_model_name(old_name)

        # Check if cleaning actually changed the name
        if new_name == old_name:
            print(f"{i}. SKIP (no change needed)")
            print(f"   Model ID: {model_id}")
            print(f"   Name: {old_name}")
            print()
            continue

        # Validate the new name
        is_valid, error_msg = validate_model_name(new_name)

        print(f"{i}. {'WOULD UPDATE' if dry_run else 'UPDATING'}")
        print(f"   Model ID: {model_id}")
        print(f"   Provider: {provider_name}")
        print(f"   Old Name: {old_name}")
        print(f"   New Name: {new_name}")
        print(f"   Valid: {'✅' if is_valid else f'❌ {error_msg}'}")

        change_record = {
            "model_id": model_id,
            "provider": provider_name,
            "old_name": old_name,
            "new_name": new_name,
            "valid": is_valid,
            "error": error_msg if not is_valid else None
        }

        if not dry_run:
            try:
                # Update the database
                supabase.table("models").update(
                    {"model_name": new_name}
                ).eq("model_id", model_id).execute()

                print(f"   Status: ✅ Updated successfully")
                change_record["status"] = "success"
                success_count += 1
            except Exception as e:
                print(f"   Status: ❌ Failed to update: {e}")
                change_record["status"] = "error"
                change_record["error"] = str(e)
                error_count += 1
        else:
            change_record["status"] = "dry_run"

        changes_made.append(change_record)
        print()

    # Print summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total models checked: {len(models)}")
    print(f"Malformed models found: {len(malformed_models)}")
    print(f"Changes {'that would be made' if dry_run else 'made'}: {len(changes_made)}")

    if not dry_run:
        print(f"Successfully updated: {success_count}")
        if error_count > 0:
            print(f"Failed updates: {error_count}")

    if dry_run:
        print("\n💡 Run with --apply flag to apply these changes to the database")

    print("=" * 80)

    return len(changes_made), changes_made


def main():
    """Main execution function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Clean up malformed model names in the database"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to the database (default is dry-run mode)"
    )
    parser.add_argument(
        "--export",
        type=str,
        metavar="FILE",
        help="Export change log to a file (JSON format)"
    )

    args = parser.parse_args()

    dry_run = not args.apply

    try:
        count, changes = cleanup_malformed_model_names(dry_run=dry_run)

        # Export changes if requested
        if args.export and changes:
            import json
            with open(args.export, "w") as f:
                json.dump(changes, f, indent=2)
            print(f"\n📄 Change log exported to: {args.export}")

        return 0 if count == 0 or not dry_run else 1

    except Exception as e:
        print(f"\n❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
