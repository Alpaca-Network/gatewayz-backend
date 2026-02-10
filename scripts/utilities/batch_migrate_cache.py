#!/usr/bin/env python3
"""Batch migrate provider clients from in-memory cache to Redis cache.

This script automates the migration of provider client files by:
1. Replacing old cache imports with new Redis cache import
2. Updating cache write patterns to use cache_gateway_catalog()
3. Removing manual timestamp updates
"""

import re
from pathlib import Path

# Provider clients to migrate (provider_name, file_name)
PROVIDERS_TO_MIGRATE = [
    ("aihubmix", "aihubmix_client.py"),
    ("aimo", "aimo_client.py"),
    ("anannas", "anannas_client.py"),
    ("canopywave", "canopywave_client.py"),
    ("fal", "fal_image_client.py"),
    ("helicone", "helicone_client.py"),
    ("modelz", "modelz_client.py"),
    ("morpheus", "morpheus_client.py"),
    ("near", "near_client.py"),
    ("onerouter", "onerouter_client.py"),
    ("zai", "zai_client.py"),
    ("alibaba", "alibaba_cloud_client.py"),
    ("vercel", "vercel_ai_gateway_client.py"),
]


def migrate_provider_client(provider_name: str, file_path: Path) -> bool:
    """Migrate a single provider client file.

    Args:
        provider_name: The provider name (e.g., 'aihubmix')
        file_path: Path to the provider client file

    Returns:
        True if migration was successful, False otherwise
    """
    if not file_path.exists():
        print(f"⚠️  File not found: {file_path}")
        return False

    print(f"\n📝 Migrating {file_path.name}...")

    content = file_path.read_text()
    original_content = content

    # Pattern 1: Replace cache import
    old_import_pattern = rf"from src\.cache import _({provider_name}[_\w]*_models_cache)"
    new_import = "from src.services.model_catalog_cache import cache_gateway_catalog"

    # Check if file uses old cache
    if not re.search(old_import_pattern, content):
        print(f"   ℹ️  No old cache import found for {provider_name}")
        return False

    # Replace import
    content = re.sub(
        old_import_pattern,
        new_import,
        content,
        count=1
    )

    # Pattern 2: Replace _cache_and_return helper function
    cache_helper_pattern = r'def _cache_and_return\(models[^)]*\)[^:]*:\s+["\']?[^"\']*["\']?\s+_\w+_models_cache\["data"\] = models\s+_\w+_models_cache\["timestamp"\] = datetime\.now\(timezone\.utc\)\s+return models'
    new_helper = '''def _cache_and_return(models: list[dict]) -> list[dict]:
        # Cache models in Redis with automatic TTL and error tracking
        cache_gateway_catalog("''' + provider_name + '''", models)
        return models'''

    content = re.sub(cache_helper_pattern, new_helper, content, flags=re.MULTILINE | re.DOTALL)

    # Pattern 3: Replace direct cache writes (if any)
    direct_cache_pattern = rf'_\w*_models_cache\["data"\] = (\w+)\s+_\w*_models_cache\["timestamp"\] = datetime\.now\(timezone\.utc\)'
    direct_cache_replacement = rf'# Cache models in Redis with automatic TTL and error tracking\n        cache_gateway_catalog("{provider_name}", \1)'

    content = re.sub(direct_cache_pattern, direct_cache_replacement, content, flags=re.MULTILINE)

    # Pattern 4: Fix return statements that return cache["data"]
    return_cache_pattern = r'return _\w+_models_cache\["data"\]'
    content = re.sub(return_cache_pattern, 'return normalized_models', content)

    # Check if any changes were made
    if content == original_content:
        print(f"   ⚠️  No changes made")
        return False

    # Write back the modified content
    file_path.write_text(content)
    print(f"   ✅ Successfully migrated {file_path.name}")
    return True


def main():
    """Main migration function."""
    print("🚀 Starting batch cache migration for provider clients...")
    print("=" * 60)

    services_dir = Path(__file__).parent.parent.parent / "src" / "services"

    migrated_count = 0
    skipped_count = 0
    failed_count = 0

    for provider_name, file_name in PROVIDERS_TO_MIGRATE:
        file_path = services_dir / file_name

        try:
            if migrate_provider_client(provider_name, file_path):
                migrated_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            print(f"   ❌ Error migrating {file_name}: {e}")
            failed_count += 1

    print("\n" + "=" * 60)
    print(f"✅ Migration complete!")
    print(f"   - Migrated: {migrated_count}")
    print(f"   - Skipped:  {skipped_count}")
    print(f"   - Failed:   {failed_count}")
    print("\n📋 Next steps:")
    print("   1. Review the changes with: git diff src/services/")
    print("   2. Test imports: python -m py_compile src/services/*_client.py")
    print("   3. Run tests: pytest tests/services/")


if __name__ == "__main__":
    main()
