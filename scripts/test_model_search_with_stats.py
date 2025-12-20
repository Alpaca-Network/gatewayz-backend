#!/usr/bin/env python3
"""
Test script for model search with chat completion statistics.

This script demonstrates how to use the search_models_with_chat_stats function
to find models and their associated chat completion request data.

Examples:
    # Search all GPT-4 models across all providers
    python scripts/test_model_search_with_stats.py --query "gpt 4"

    # Search GPT-4 models on OpenRouter only
    python scripts/test_model_search_with_stats.py --query "gpt 4" --provider openrouter

    # Search all Claude models
    python scripts/test_model_search_with_stats.py --query "claude"

    # Search Llama models on Together AI
    python scripts/test_model_search_with_stats.py --query "llama" --provider together
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add parent directory to path to import from src
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.chat_completion_requests import search_models_with_chat_stats

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def format_model_result(model: dict, index: int) -> str:
    """Format a single model result for display."""
    provider = model.get('provider', {})
    chat_stats = model.get('chat_stats', {})

    output = []
    output.append(f"\n{'=' * 80}")
    output.append(f"Result #{index + 1}")
    output.append(f"{'=' * 80}")

    # Model information
    output.append(f"\nModel: {model.get('model_name')}")
    output.append(f"Identifier: {model.get('model_identifier')}")
    output.append(f"Provider Model ID: {model.get('provider_model_id')}")
    output.append(f"Provider: {provider.get('name')} ({provider.get('slug')})")

    # Capabilities
    output.append(f"\nCapabilities:")
    output.append(f"  - Context Length: {model.get('context_length', 'N/A'):,} tokens")
    output.append(f"  - Modality: {model.get('modality')}")
    output.append(f"  - Streaming: {'Yes' if model.get('supports_streaming') else 'No'}")
    output.append(f"  - Function Calling: {'Yes' if model.get('supports_function_calling') else 'No'}")
    output.append(f"  - Vision: {'Yes' if model.get('supports_vision') else 'No'}")

    # Pricing
    pricing_prompt = model.get('pricing_prompt')
    pricing_completion = model.get('pricing_completion')
    if pricing_prompt is not None or pricing_completion is not None:
        output.append(f"\nPricing:")
        if pricing_prompt is not None:
            output.append(f"  - Prompt: ${pricing_prompt:.6f} per token")
        if pricing_completion is not None:
            output.append(f"  - Completion: ${pricing_completion:.6f} per token")

    # Health status
    output.append(f"\nHealth Status: {model.get('health_status', 'unknown').upper()}")

    # Chat completion statistics
    output.append(f"\nChat Completion Statistics:")
    output.append(f"  - Total Requests: {chat_stats.get('total_requests', 0):,}")
    output.append(f"  - Completed: {chat_stats.get('completed_requests', 0):,}")
    output.append(f"  - Failed: {chat_stats.get('failed_requests', 0):,}")
    output.append(f"  - Success Rate: {chat_stats.get('success_rate', 0):.2f}%")

    if chat_stats.get('total_requests', 0) > 0:
        output.append(f"\n  Token Usage:")
        output.append(f"    - Total Tokens: {chat_stats.get('total_tokens', 0):,}")
        output.append(f"    - Avg Input Tokens: {chat_stats.get('avg_input_tokens', 0):.2f}")
        output.append(f"    - Avg Output Tokens: {chat_stats.get('avg_output_tokens', 0):.2f}")

        output.append(f"\n  Performance:")
        output.append(f"    - Avg Processing Time: {chat_stats.get('avg_processing_time_ms', 0):.2f}ms")
        output.append(f"    - Total Processing Time: {chat_stats.get('total_processing_time_ms', 0):,}ms")

        if chat_stats.get('last_request_at'):
            output.append(f"\n  Last Request: {chat_stats.get('last_request_at')}")

    return '\n'.join(output)


def main():
    parser = argparse.ArgumentParser(
        description='Search models with chat completion statistics',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--query', '-q',
        required=True,
        help='Search query for model name (e.g., "gpt 4", "claude", "llama")'
    )
    parser.add_argument(
        '--provider', '-p',
        help='Optional provider slug or name to filter results (e.g., "openrouter", "portkey")'
    )
    parser.add_argument(
        '--limit', '-l',
        type=int,
        default=20,
        help='Maximum number of results to return (default: 20)'
    )
    parser.add_argument(
        '--json', '-j',
        action='store_true',
        help='Output results as JSON instead of formatted text'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        logger.info(f"Searching for models matching: '{args.query}'")
        if args.provider:
            logger.info(f"Filtering by provider: '{args.provider}'")

        # Execute search
        results = search_models_with_chat_stats(
            query=args.query,
            provider_name=args.provider,
            limit=args.limit
        )

        # Output results
        if args.json:
            # JSON output
            print(json.dumps({
                'query': args.query,
                'provider_filter': args.provider,
                'total_results': len(results),
                'models': results
            }, indent=2, default=str))
        else:
            # Formatted text output
            print(f"\n{'=' * 80}")
            print(f"SEARCH RESULTS")
            print(f"{'=' * 80}")
            print(f"\nQuery: '{args.query}'")
            if args.provider:
                print(f"Provider Filter: '{args.provider}'")
            print(f"Total Results: {len(results)}")

            if not results:
                print("\nNo models found matching your search criteria.")
                return

            # Display each result
            for i, model in enumerate(results):
                print(format_model_result(model, i))

            # Summary statistics
            total_requests = sum(m.get('chat_stats', {}).get('total_requests', 0) for m in results)
            total_tokens = sum(m.get('chat_stats', {}).get('total_tokens', 0) for m in results)

            print(f"\n{'=' * 80}")
            print(f"SUMMARY")
            print(f"{'=' * 80}")
            print(f"Total Models Found: {len(results)}")
            print(f"Total Requests Across All Models: {total_requests:,}")
            print(f"Total Tokens Across All Models: {total_tokens:,}")

            # Most used model
            if total_requests > 0:
                most_used = max(results, key=lambda x: x.get('chat_stats', {}).get('total_requests', 0))
                print(f"\nMost Used Model:")
                print(f"  - {most_used.get('model_name')} ({most_used.get('provider', {}).get('name')})")
                print(f"  - {most_used.get('chat_stats', {}).get('total_requests', 0):,} requests")

    except Exception as e:
        logger.error(f"Error searching models: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
