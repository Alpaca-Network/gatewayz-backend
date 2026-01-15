#!/usr/bin/env python3
"""
Provider Seeding Script
Ensures all providers from GATEWAY_REGISTRY are synced to the database

Run this:
- On every deployment (via CI/CD)
- Manually when adding new providers
- As part of database migrations

Usage:
    python scripts/database/seed_providers.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config.supabase_config import get_supabase_client
from src.routes.catalog import GATEWAY_REGISTRY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Provider configuration mapping from GATEWAY_REGISTRY to database schema
PROVIDER_ENV_VAR_MAP = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "vercel-ai-gateway": "VERCEL_AI_GATEWAY_KEY",
    "featherless": "FEATHERLESS_API_KEY",
    "chutes": "CHUTES_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "google-vertex": "GOOGLE_APPLICATION_CREDENTIALS",
    "cerebras": "CEREBRAS_API_KEY",
    "nebius": "NEBIUS_API_KEY",
    "xai": "XAI_API_KEY",
    "novita": "NOVITA_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "aimo": "AIMO_API_KEY",
    "near": "NEAR_API_KEY",
    "fal": "FAL_API_KEY",
    "helicone": "HELICONE_API_KEY",
    "alpaca": "ALPACA_NETWORK_API_KEY",
    "alibaba": "ALIBABA_CLOUD_API_KEY",
    "clarifai": "CLARIFAI_API_KEY",
    "onerouter": "ONEROUTER_API_KEY",
    "simplismart": "SIMPLISMART_API_KEY",
    "aihubmix": "AIHUBMIX_API_KEY",
    "anannas": "ANANNAS_API_KEY",
    "cloudflare-workers-ai": "CLOUDFLARE_WORKERS_AI_API_KEY",
}


def transform_gateway_to_provider(gateway_id: str, gateway_config: dict) -> dict:
    """Transform GATEWAY_REGISTRY entry to provider database schema"""
    return {
        "name": gateway_config["name"],
        "slug": gateway_id,
        "description": f"{gateway_config['name']} - {gateway_config.get('priority', 'standard')} priority gateway",
        "api_key_env_var": PROVIDER_ENV_VAR_MAP.get(gateway_id, f"{gateway_id.upper().replace('-', '_')}_API_KEY"),
        "supports_streaming": True,  # Most providers support streaming
        "is_active": True,
        "site_url": gateway_config.get("site_url"),
        "metadata": {
            "color": gateway_config.get("color", "bg-gray-500"),
            "priority": gateway_config.get("priority", "slow"),
            "icon": gateway_config.get("icon"),
            "aliases": gateway_config.get("aliases", []),
        }
    }


def seed_providers(dry_run: bool = False) -> dict:
    """
    Seed/update all providers from GATEWAY_REGISTRY

    Args:
        dry_run: If True, only show what would be done

    Returns:
        Dictionary with sync results
    """
    try:
        client = get_supabase_client()

        logger.info(f"🔄 Syncing {len(GATEWAY_REGISTRY)} providers from GATEWAY_REGISTRY")

        providers_to_upsert = []
        for gateway_id, gateway_config in GATEWAY_REGISTRY.items():
            provider = transform_gateway_to_provider(gateway_id, gateway_config)
            providers_to_upsert.append(provider)

            if dry_run:
                logger.info(f"  [DRY RUN] Would upsert: {provider['name']} ({provider['slug']})")

        if dry_run:
            logger.info(f"✅ [DRY RUN] Would sync {len(providers_to_upsert)} providers")
            return {
                "success": True,
                "dry_run": True,
                "providers_synced": len(providers_to_upsert),
                "providers": [p["slug"] for p in providers_to_upsert]
            }

        # Upsert all providers (insert or update on conflict)
        result = client.table("providers").upsert(
            providers_to_upsert,
            on_conflict="slug"  # Update if slug already exists
        ).execute()

        synced_count = len(result.data) if result.data else 0

        logger.info(f"✅ Successfully synced {synced_count} providers to database")

        # Log each provider
        for provider in providers_to_upsert:
            logger.info(f"  ✓ {provider['name']} ({provider['slug']})")

        return {
            "success": True,
            "providers_synced": synced_count,
            "providers": [p["slug"] for p in providers_to_upsert]
        }

    except Exception as e:
        logger.error(f"❌ Failed to seed providers: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "providers_synced": 0
        }


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Seed providers from GATEWAY_REGISTRY")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("PROVIDER SEEDING SCRIPT")
    logger.info("=" * 80)

    result = seed_providers(dry_run=args.dry_run)

    logger.info("=" * 80)
    if result["success"]:
        logger.info(f"✅ SUCCESS: Synced {result['providers_synced']} providers")
        sys.exit(0)
    else:
        logger.error(f"❌ FAILED: {result.get('error', 'Unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
