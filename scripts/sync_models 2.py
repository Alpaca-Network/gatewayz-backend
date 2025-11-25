#!/usr/bin/env python3
"""
Script to sync models from provider APIs to database

This script can be run manually or as a scheduled job (cron/systemd timer)
to keep the model catalog up-to-date.

Usage:
    # Sync all providers (dry run first to test)
    python scripts/sync_models.py --dry-run

    # Actually sync all providers
    python scripts/sync_models.py

    # Sync specific providers
    python scripts/sync_models.py --providers openrouter deepinfra

    # Sync with verbose logging
    python scripts/sync_models.py --verbose

Examples:
    # Test sync without writing to DB
    python scripts/sync_models.py --dry-run

    # Sync just OpenRouter
    python scripts/sync_models.py --providers openrouter

    # Sync OpenRouter and DeepInfra with detailed logs
    python scripts/sync_models.py --providers openrouter deepinfra --verbose
"""

import argparse
import logging
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.model_catalog_sync import sync_all_providers, sync_provider_models


def setup_logging(verbose: bool = False):
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main():
    parser = argparse.ArgumentParser(
        description='Sync AI models from provider APIs to database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--providers',
        nargs='+',
        metavar='PROVIDER',
        help='Specific providers to sync (e.g., openrouter deepinfra). If not specified, syncs all.'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Fetch and transform models but do not write to database'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose (DEBUG) logging'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Print configuration
    logger.info("="*60)
    logger.info("Model Catalog Sync Script")
    logger.info("="*60)
    if args.providers:
        logger.info(f"Providers: {', '.join(args.providers)}")
    else:
        logger.info("Providers: ALL")
    logger.info(f"Dry Run: {args.dry_run}")
    logger.info(f"Verbose: {args.verbose}")
    logger.info("="*60)

    try:
        # Run sync
        result = sync_all_providers(
            provider_slugs=args.providers,
            dry_run=args.dry_run
        )

        # Print results
        logger.info("\n" + "="*60)
        logger.info("Sync Complete")
        logger.info("="*60)
        logger.info(f"Success: {result['success']}")
        logger.info(f"Providers Processed: {result['providers_processed']}")
        logger.info(f"Models Fetched: {result['total_models_fetched']}")
        logger.info(f"Models Transformed: {result.get('total_models_transformed', 0)}")
        logger.info(f"Models Skipped: {result.get('total_models_skipped', 0)}")
        logger.info(f"Models Synced: {result['total_models_synced']}")

        if result.get('errors'):
            logger.error(f"\nErrors: {len(result['errors'])}")
            for error in result['errors']:
                logger.error(f"  - {error['provider']}: {error['error']}")

        # Print per-provider summary
        logger.info("\n" + "="*60)
        logger.info("Per-Provider Summary")
        logger.info("="*60)

        for provider_result in result.get('results', []):
            provider = provider_result.get('provider', 'unknown')
            success = "✓" if provider_result.get('success') else "✗"
            fetched = provider_result.get('models_fetched', 0)
            transformed = provider_result.get('models_transformed', 0)
            skipped = provider_result.get('models_skipped', 0)
            synced = provider_result.get('models_synced', 0)

            logger.info(
                f"{success} {provider:20} | "
                f"Fetched: {fetched:4} | "
                f"Transformed: {transformed:4} | "
                f"Skipped: {skipped:4} | "
                f"Synced: {synced:4}"
            )

        # Exit code based on success
        sys.exit(0 if result['success'] else 1)

    except KeyboardInterrupt:
        logger.warning("\nSync interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
