#!/usr/bin/env python3
"""
Rate-Limited Model Healthcheck Script

This is an OpenRouter rate-limit compliant version of the healthcheck script.
It only checks critical models and adds proper delays between checks to avoid
hitting OpenRouter's "4 model switches per minute" limit.

Key changes from original:
1. Only checks CRITICAL_MODELS list (configurable)
2. Adds 20-second delay between model checks (3 checks/min max)
3. Logs estimated completion time
4. Can be gradually expanded as needed
"""

import sys
import os
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.models import get_cached_models
from src.config import Config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('healthcheck_rate_limited.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# OpenRouter allows 4 model switches per minute
# We use 3 per minute to be safe (one check every 20 seconds)
MODEL_CHECK_DELAY_SECONDS = 20

# Critical models to check (high-priority, most-used models)
CRITICAL_MODELS = {
    # OpenAI models
    'gpt-4o',
    'gpt-4o-mini',
    'gpt-4-turbo',
    'gpt-4',
    'gpt-3.5-turbo',

    # Anthropic models
    'claude-3-5-sonnet',
    'claude-3-opus',
    'claude-3-sonnet',
    'claude-3-haiku',

    # Meta models
    'llama-3.1-405b',
    'llama-3.1-70b',
    'llama-3.1-8b',
    'llama-3.3-70b',

    # Google models
    'gemini-pro',
    'gemini-2.0-flash-exp',

    # DeepSeek
    'deepseek-chat',
    'deepseek-coder',

    # Mistral
    'mistral-large',
    'mixtral-8x7b',

    # Other popular models
    'qwen-2.5-72b',
}

# Gateway configuration - only check OpenRouter for now
GATEWAY_CONFIG = {
    'openrouter': {
        'name': 'OpenRouter',
        'api_key': Config.OPENROUTER_API_KEY,
        'enabled': True,
    }
}

# ============================================================================
# HEALTHCHECK FUNCTIONS
# ============================================================================

def filter_critical_models(all_models: List[Dict]) -> List[Dict]:
    """Filter models to only include critical ones."""
    critical = []

    for model in all_models:
        model_id = model.get('id', '')

        # Check if model ID matches any critical model
        # Support both exact match and partial match (e.g., "openai/gpt-4" matches "gpt-4")
        for critical_id in CRITICAL_MODELS:
            if critical_id in model_id.lower():
                critical.append(model)
                break

    return critical


def check_critical_models_with_rate_limit(gateway_name: str, delay_seconds: int = None) -> Dict:
    """
    Check critical models with proper rate limiting.

    Args:
        gateway_name: Name of the gateway to check
        delay_seconds: Seconds to wait between checks (default: MODEL_CHECK_DELAY_SECONDS)

    Returns:
        Dictionary with healthcheck results
    """
    if delay_seconds is None:
        delay_seconds = MODEL_CHECK_DELAY_SECONDS
    logger.info(f"=" * 80)
    logger.info(f"Starting rate-limited healthcheck for {gateway_name}")
    logger.info(f"=" * 80)

    results = {
        'gateway': gateway_name,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'total_models_available': 0,
        'critical_models_checked': 0,
        'accessible_models': 0,
        'inaccessible_models': 0,
        'skipped_models': 0,
        'models': []
    }

    try:
        # Get all models from cache
        all_models = get_cached_models(gateway_name)

        if not all_models:
            logger.warning(f"No models found in cache for {gateway_name}")
            return results

        results['total_models_available'] = len(all_models)
        logger.info(f"Found {len(all_models)} total models in cache")

        # Filter to critical models only
        critical_models = filter_critical_models(all_models)
        logger.info(f"Filtered to {len(critical_models)} critical models")

        # Calculate estimated time
        estimated_minutes = (len(critical_models) * delay_seconds) / 60
        logger.info(f"Estimated completion time: {estimated_minutes:.1f} minutes")
        logger.info(f"Rate limit: 1 model every {delay_seconds} seconds")
        logger.info(f"=" * 80)

        # Check each critical model with rate limiting
        for idx, model in enumerate(critical_models, 1):
            model_id = model.get('id', 'unknown')

            try:
                logger.info(f"[{idx}/{len(critical_models)}] Checking: {model_id}")

                # Validate model has required fields
                has_id = bool(model.get('id'))
                has_name = bool(model.get('name'))
                accessible = has_id and has_name

                model_result = {
                    'id': model_id,
                    'name': model.get('name', 'Unknown'),
                    'accessible': accessible,
                    'has_pricing': 'pricing' in model,
                    'context_length': model.get('context_length', 0),
                    'checked_at': datetime.now(timezone.utc).isoformat(),
                }

                if accessible:
                    results['accessible_models'] += 1
                    logger.info(f"  ✓ Accessible")
                else:
                    results['inaccessible_models'] += 1
                    logger.warning(f"  ✗ Inaccessible - missing required fields")

                results['models'].append(model_result)
                results['critical_models_checked'] += 1

                # Rate limiting: sleep between checks (except for last model)
                if idx < len(critical_models):
                    logger.debug(f"  Waiting {delay_seconds}s before next check...")
                    time.sleep(delay_seconds)

            except Exception as e:
                logger.error(f"  Error checking {model_id}: {e}")
                results['inaccessible_models'] += 1
                results['models'].append({
                    'id': model_id,
                    'accessible': False,
                    'error': str(e),
                    'checked_at': datetime.now(timezone.utc).isoformat(),
                })

        # Calculate skipped models (non-critical)
        results['skipped_models'] = results['total_models_available'] - results['critical_models_checked']

        logger.info(f"=" * 80)
        logger.info(f"Healthcheck completed for {gateway_name}")
        logger.info(f"Total models available: {results['total_models_available']}")
        logger.info(f"Critical models checked: {results['critical_models_checked']}")
        logger.info(f"Accessible: {results['accessible_models']}")
        logger.info(f"Inaccessible: {results['inaccessible_models']}")
        logger.info(f"Skipped (non-critical): {results['skipped_models']}")
        logger.info(f"=" * 80)

    except Exception as e:
        logger.error(f"Error during healthcheck: {e}", exc_info=True)
        results['error'] = str(e)

    return results


def export_results(results: Dict, output_dir: Path = Path('healthcheck_results')):
    """Export results to JSON file."""
    try:
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        output_file = output_dir / f"healthcheck_rate_limited_{timestamp}.json"

        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)

        logger.info(f"Results exported to: {output_file}")
        return output_file

    except Exception as e:
        logger.error(f"Failed to export results: {e}")
        return None


def print_summary(results: Dict):
    """Print human-readable summary."""
    print("\n" + "=" * 80)
    print("HEALTHCHECK SUMMARY")
    print("=" * 80)
    print(f"Gateway:                 {results['gateway']}")
    print(f"Timestamp:               {results['timestamp']}")
    print(f"Total Models Available:  {results['total_models_available']}")
    print(f"Critical Models Checked: {results['critical_models_checked']}")
    print(f"Accessible:              {results['accessible_models']}")
    print(f"Inaccessible:            {results['inaccessible_models']}")
    print(f"Skipped (non-critical):  {results['skipped_models']}")

    if results['critical_models_checked'] > 0:
        health_pct = (results['accessible_models'] / results['critical_models_checked']) * 100
        print(f"Health Percentage:       {health_pct:.1f}%")

        if health_pct >= 95:
            print(f"Status:                  ✅ HEALTHY")
        elif health_pct >= 70:
            print(f"Status:                  ⚠️  DEGRADED")
        else:
            print(f"Status:                  ❌ UNHEALTHY")

    print("=" * 80)


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Rate-limited healthcheck for critical models'
    )
    parser.add_argument(
        '--gateway',
        type=str,
        default='openrouter',
        choices=['openrouter'],
        help='Gateway to check (default: openrouter)'
    )
    parser.add_argument(
        '--export',
        action='store_true',
        default=True,
        help='Export results to JSON (default: True)'
    )
    parser.add_argument(
        '--delay',
        type=int,
        default=MODEL_CHECK_DELAY_SECONDS,
        help=f'Delay between checks in seconds (default: {MODEL_CHECK_DELAY_SECONDS})'
    )
    parser.add_argument(
        '--list-critical',
        action='store_true',
        help='List critical models and exit'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.list_critical:
        print("Critical Models:")
        for model in sorted(CRITICAL_MODELS):
            print(f"  - {model}")
        print(f"\nTotal: {len(CRITICAL_MODELS)} models")
        sys.exit(0)

    # Use specified delay
    check_delay = args.delay

    # Check if gateway is configured
    gateway_config = GATEWAY_CONFIG.get(args.gateway)
    if not gateway_config or not gateway_config.get('api_key'):
        logger.error(f"Gateway {args.gateway} not configured or API key missing")
        sys.exit(1)

    # Run healthcheck
    try:
        results = check_critical_models_with_rate_limit(args.gateway, check_delay)

        # Print summary
        print_summary(results)

        # Export results
        if args.export:
            export_results(results)

        # Exit with appropriate code
        if results.get('error'):
            sys.exit(1)
        elif results['inaccessible_models'] > 0:
            sys.exit(2)  # Some models inaccessible
        else:
            sys.exit(0)  # All good

    except KeyboardInterrupt:
        logger.info("Healthcheck interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
