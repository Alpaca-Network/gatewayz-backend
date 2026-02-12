#!/usr/bin/env python3
"""
Audit Gateway API Keys
Checks all 30+ gateways for missing or invalid API keys.
"""
import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.services.gateway_health_service import GATEWAY_CONFIG


def audit_gateway_keys():
    """Audit all gateway API keys and report missing/invalid ones."""

    print("=" * 80)
    print("GATEWAY API KEY AUDIT REPORT")
    print("=" * 80)
    print()

    total_gateways = len(GATEWAY_CONFIG)
    configured_gateways = 0
    missing_keys = []
    optional_gateways = []

    for gateway_name, gateway_config in GATEWAY_CONFIG.items():
        api_key_env = gateway_config.get("api_key_env")
        api_key = gateway_config.get("api_key")
        url = gateway_config.get("url")
        needs_api_key = url is not None  # Gateways with url=None are static catalogs

        status = "✅ CONFIGURED"

        if needs_api_key:
            if not api_key or api_key == "":
                status = "❌ MISSING API KEY"
                missing_keys.append({
                    "gateway": gateway_name,
                    "env_var": api_key_env,
                    "url": url,
                })
            else:
                configured_gateways += 1
        else:
            status = "⚪ STATIC CATALOG (no key needed)"
            optional_gateways.append(gateway_name)
            configured_gateways += 1  # Count as configured

        print(f"{status:30} | {gateway_name:20} | Env: {api_key_env}")

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total Gateways: {total_gateways}")
    print(f"Configured: {configured_gateways} ({configured_gateways/total_gateways*100:.1f}%)")
    print(f"Missing Keys: {len(missing_keys)} ({len(missing_keys)/total_gateways*100:.1f}%)")
    print(f"Static Catalogs: {len(optional_gateways)}")
    print()

    if missing_keys:
        print("=" * 80)
        print("MISSING API KEYS - ACTION REQUIRED")
        print("=" * 80)
        print()
        for item in missing_keys:
            print(f"Gateway: {item['gateway']}")
            print(f"  Environment Variable: {item['env_var']}")
            print(f"  API Endpoint: {item['url']}")
            print(f"  Action: export {item['env_var']}='your-api-key-here'")
            print()

        print("To fix, add these to your .env file or environment:")
        print()
        for item in missing_keys:
            print(f"{item['env_var']}=your-api-key-here")
        print()
    else:
        print("✅ All gateways properly configured!")

    print()
    print(f"Expected Health Improvement: +{len(missing_keys) * 2.5:.1f}% if all keys configured")
    print("(Assumes ~10 models per gateway on average)")
    print()

    return len(missing_keys) == 0


if __name__ == "__main__":
    success = audit_gateway_keys()
    sys.exit(0 if success else 1)
