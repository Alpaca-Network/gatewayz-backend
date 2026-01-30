#!/usr/bin/env python3
"""
Script to inspect Redis cache data
"""
import json
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, '/Users/arminrad/Desktop/Alpaca-Network/Gatewayz/gatewayz-backend')

from src.config.redis_config import get_redis_client


def inspect_cache():
    """Inspect all Redis cache keys and their data"""
    redis_client = get_redis_client()

    if not redis_client:
        print("❌ Redis client not available")
        return

    print("🔍 Inspecting Redis Cache\n")
    print("=" * 80)

    # Get all keys
    try:
        # Common cache key patterns
        patterns = [
            "models:*",      # Model catalog cache
            "providers:*",   # Provider cache
            "pricing:*",     # Pricing cache
            "rate_limit:*",  # Rate limiting
            "auth:*",        # Auth cache
            "user:*",        # User cache
            "*"              # All keys (use carefully)
        ]

        all_keys = set()
        for pattern in patterns[:-1]:  # Skip "*" for now
            keys = redis_client.keys(pattern)
            all_keys.update(keys)

        print(f"📊 Total cache keys found: {len(all_keys)}\n")

        # Group keys by prefix
        key_groups = {}
        for key in sorted(all_keys):
            prefix = key.split(':')[0] if ':' in key else 'other'
            if prefix not in key_groups:
                key_groups[prefix] = []
            key_groups[prefix].append(key)

        # Display grouped keys
        for prefix, keys in sorted(key_groups.items()):
            print(f"\n📁 {prefix.upper()} ({len(keys)} keys)")
            print("-" * 80)

            for key in keys[:5]:  # Show first 5 keys of each type
                try:
                    value = redis_client.get(key)
                    ttl = redis_client.ttl(key)

                    # Try to parse as JSON
                    try:
                        data = json.loads(value) if value else None
                        if isinstance(data, dict):
                            # Show summary for dict
                            print(f"  🔑 {key}")
                            print(f"     TTL: {ttl}s")
                            if 'data' in data and isinstance(data['data'], list):
                                print(f"     Items: {len(data['data'])}")
                            if 'timestamp' in data:
                                print(f"     Updated: {data['timestamp']}")
                            print()
                        else:
                            # Show value for simple types
                            print(f"  🔑 {key}: {str(value)[:100]} (TTL: {ttl}s)")
                    except json.JSONDecodeError:
                        print(f"  🔑 {key}: {str(value)[:100]} (TTL: {ttl}s)")
                except Exception as e:
                    print(f"  🔑 {key}: Error reading - {e}")

            if len(keys) > 5:
                print(f"  ... and {len(keys) - 5} more keys")

        print("\n" + "=" * 80)
        print("\n💡 To inspect a specific key in detail:")
        print("   python scripts/inspect_redis_cache.py <key_name>")

    except Exception as e:
        print(f"❌ Error inspecting cache: {e}")


def inspect_specific_key(key: str):
    """Inspect a specific Redis key in detail"""
    redis_client = get_redis_client()

    if not redis_client:
        print("❌ Redis client not available")
        return

    print(f"🔍 Inspecting Key: {key}\n")
    print("=" * 80)

    try:
        # Check if key exists
        exists = redis_client.exists(key)
        if not exists:
            print(f"❌ Key '{key}' does not exist")
            return

        # Get value
        value = redis_client.get(key)
        ttl = redis_client.ttl(key)

        print(f"📊 Key Information:")
        print(f"   TTL: {ttl} seconds")
        print(f"   Type: {redis_client.type(key)}")
        print(f"\n📄 Value:")

        # Try to parse and pretty-print JSON
        try:
            data = json.loads(value)
            print(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            print(value)

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Inspect specific key
        key = sys.argv[1]
        inspect_specific_key(key)
    else:
        # Inspect all cache
        inspect_cache()
