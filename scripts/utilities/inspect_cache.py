#!/usr/bin/env python3
"""
Interactive Cache Inspector

Browse and inspect Redis cache contents with filtering and pretty printing.
Similar to Supabase GUI but for Redis cache.

Usage:
    python scripts/utilities/inspect_cache.py
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from src.config.redis_config import get_redis_client
except ImportError:
    print("Error: Could not import Redis config. Make sure you're in the project root.")
    sys.exit(1)


def format_size(bytes_val):
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} TB"


def format_ttl(seconds):
    """Format TTL to human readable format"""
    if seconds < 0:
        return "No expiration"
    elif seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def list_keys_by_pattern(redis_client, pattern="*", limit=100):
    """List keys matching pattern"""
    print(f"\n{'='*80}")
    print(f"Keys matching: {pattern}")
    print(f"{'='*80}\n")

    keys = []
    cursor = 0

    while True:
        cursor, batch = redis_client.scan(cursor, match=pattern, count=100)
        keys.extend(batch)

        if cursor == 0 or len(keys) >= limit:
            break

    if not keys:
        print("No keys found.")
        return []

    print(f"Found {len(keys)} keys (showing first {min(len(keys), limit)}):\n")

    # Get details for each key
    for i, key in enumerate(keys[:limit], 1):
        try:
            key_type = redis_client.type(key)
            ttl = redis_client.ttl(key)

            # Try to get size
            size = "N/A"
            if key_type == "string":
                value = redis_client.get(key)
                if value:
                    size = format_size(len(value))

            print(f"{i:3d}. {key}")
            print(f"     Type: {key_type:10s} | TTL: {format_ttl(ttl):15s} | Size: {size}")
        except Exception as e:
            print(f"{i:3d}. {key} (error: {e})")

    return keys


def inspect_key(redis_client, key):
    """Inspect a specific key in detail"""
    print(f"\n{'='*80}")
    print(f"Key Details: {key}")
    print(f"{'='*80}\n")

    # Check if key exists
    if not redis_client.exists(key):
        print("❌ Key does not exist")
        return

    # Get metadata
    key_type = redis_client.type(key)
    ttl = redis_client.ttl(key)

    print(f"Type: {key_type}")
    print(f"TTL:  {format_ttl(ttl)}")

    if ttl > 0:
        expires_at = datetime.now() + timedelta(seconds=ttl)
        print(f"Expires: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}")

    print()

    # Get value based on type
    try:
        if key_type == "string":
            value = redis_client.get(key)
            print(f"Size: {format_size(len(value))}")
            print("\nValue:")
            print("-" * 80)

            # Try to parse as JSON
            try:
                data = json.loads(value)
                print(json.dumps(data, indent=2)[:5000])  # Limit to 5000 chars
                if len(json.dumps(data)) > 5000:
                    print("\n... (truncated, full size: {} chars)".format(len(json.dumps(data))))
            except json.JSONDecodeError:
                # Not JSON, print as string
                print(value[:5000])
                if len(value) > 5000:
                    print("\n... (truncated)")

        elif key_type == "hash":
            fields = redis_client.hgetall(key)
            print(f"Fields: {len(fields)}")
            print("\nHash contents:")
            print("-" * 80)
            for field, value in list(fields.items())[:20]:
                print(f"{field}: {value[:100]}")
            if len(fields) > 20:
                print(f"... ({len(fields) - 20} more fields)")

        elif key_type == "list":
            length = redis_client.llen(key)
            print(f"Length: {length}")
            print("\nList items (first 20):")
            print("-" * 80)
            items = redis_client.lrange(key, 0, 19)
            for i, item in enumerate(items):
                print(f"{i}: {item[:100]}")

        elif key_type == "set":
            members = redis_client.smembers(key)
            print(f"Members: {len(members)}")
            print("\nSet members (first 20):")
            print("-" * 80)
            for i, member in enumerate(list(members)[:20]):
                print(f"- {member}")

        elif key_type == "zset":
            size = redis_client.zcard(key)
            print(f"Members: {size}")
            print("\nSorted set members (first 20):")
            print("-" * 80)
            members = redis_client.zrange(key, 0, 19, withscores=True)
            for member, score in members:
                print(f"{score}: {member}")

    except Exception as e:
        print(f"Error reading value: {e}")


def show_cache_stats(redis_client):
    """Show overall cache statistics"""
    print(f"\n{'='*80}")
    print("Cache Statistics")
    print(f"{'='*80}\n")

    # Get Redis info
    info = redis_client.info()
    memory_info = redis_client.info("memory")
    stats = redis_client.info("stats")
    keyspace = redis_client.info("keyspace")

    # Overall stats
    print("📊 Overall:")
    print(f"   Total Keys: {info.get('db0', {}).get('keys', 0)}")
    print(f"   Memory Used: {format_size(memory_info.get('used_memory', 0))}")
    print(f"   Peak Memory: {format_size(memory_info.get('used_memory_peak', 0))}")
    print(f"   Connected Clients: {info.get('connected_clients', 0)}")

    # Key breakdown by prefix
    print("\n🔑 Keys by Prefix:")
    prefixes = {}

    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor, count=1000)
        for key in keys:
            prefix = key.split(':')[0] if ':' in key else 'other'
            prefixes[prefix] = prefixes.get(prefix, 0) + 1

        if cursor == 0:
            break

    for prefix, count in sorted(prefixes.items(), key=lambda x: x[1], reverse=True):
        print(f"   {prefix:30s}: {count:5d} keys")

    # Commands stats
    print(f"\n📈 Operations:")
    print(f"   Total Commands: {stats.get('total_commands_processed', 0):,}")
    print(f"   Commands/sec: {stats.get('instantaneous_ops_per_sec', 0)}")
    print(f"   Hit Rate: N/A (not tracked by default)")


def interactive_menu():
    """Interactive menu for cache inspection"""
    redis_client = get_redis_client()

    if not redis_client:
        print("❌ Could not connect to Redis. Check your REDIS_URL configuration.")
        return

    print("\n" + "="*80)
    print("🔍 Redis Cache Inspector")
    print("="*80)

    try:
        redis_client.ping()
        print("✅ Connected to Redis successfully")
    except Exception as e:
        print(f"❌ Failed to connect to Redis: {e}")
        return

    while True:
        print("\n" + "="*80)
        print("Options:")
        print("  1. Show cache statistics")
        print("  2. List all keys (first 100)")
        print("  3. Search keys by pattern")
        print("  4. Inspect specific key")
        print("  5. Browse model catalog cache")
        print("  6. Browse auth cache")
        print("  7. Browse catalog response cache")
        print("  8. Browse health cache")
        print("  9. Browse DB cache")
        print("  0. Exit")
        print("="*80)

        choice = input("\nEnter choice: ").strip()

        if choice == "0":
            print("\n👋 Goodbye!")
            break

        elif choice == "1":
            show_cache_stats(redis_client)

        elif choice == "2":
            list_keys_by_pattern(redis_client, "*", limit=100)

        elif choice == "3":
            pattern = input("Enter search pattern (e.g., 'catalog:*', 'auth:*'): ").strip()
            list_keys_by_pattern(redis_client, pattern or "*", limit=100)

        elif choice == "4":
            key = input("Enter key name: ").strip()
            if key:
                inspect_key(redis_client, key)

        elif choice == "5":
            print("\n📚 Model Catalog Cache Keys:")
            print("  - models:catalog:full (Full catalog)")
            print("  - models:provider:* (Provider catalogs)")
            print("  - models:gateway:* (Gateway catalogs)")
            print("  - models:unique (Unique models)")
            print("  - models:stats (Catalog statistics)")
            list_keys_by_pattern(redis_client, "models:*", limit=50)

        elif choice == "6":
            print("\n🔐 Authentication Cache Keys:")
            print("  - auth:key_user:* (API key to user)")
            print("  - auth:user_id:* (User by ID)")
            print("  - auth:username:* (User by username)")
            print("  - auth:privy_id:* (User by Privy ID)")
            list_keys_by_pattern(redis_client, "auth:*", limit=50)

        elif choice == "7":
            print("\n📋 Catalog Response Cache Keys:")
            print("  - catalog:v2:* (Cached API responses)")
            list_keys_by_pattern(redis_client, "catalog:*", limit=50)

        elif choice == "8":
            print("\n❤️ Health Cache Keys:")
            print("  - health:system (System health)")
            print("  - health:providers (Provider health)")
            print("  - health:models (Model health)")
            print("  - health:gateways (Gateway health)")
            list_keys_by_pattern(redis_client, "health:*", limit=50)

        elif choice == "9":
            print("\n💾 Database Cache Keys:")
            print("  - db:user:* (User cache)")
            print("  - db:api_key:* (API key cache)")
            print("  - db:plan:* (Plan cache)")
            print("  - db:pricing:* (Pricing cache)")
            list_keys_by_pattern(redis_client, "db:*", limit=50)

        else:
            print("❌ Invalid choice")

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        interactive_menu()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
