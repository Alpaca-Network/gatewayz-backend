#!/usr/bin/env python3
"""
Example usage of the model search with chat statistics feature.

This file demonstrates common use cases for searching models and analyzing
their chat completion statistics.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.chat_completion_requests import search_models_with_chat_stats


def example_1_basic_search():
    """Example 1: Basic search across all providers."""
    print("\n" + "=" * 80)
    print("EXAMPLE 1: Basic Search - All GPT-4 models")
    print("=" * 80)

    results = search_models_with_chat_stats(query="gpt 4", limit=10)

    print(f"\nFound {len(results)} models matching 'gpt 4'")

    for model in results[:3]:  # Show top 3
        print(f"\n  - {model['model_name']}")
        print(f"    Provider: {model['provider']['name']}")
        print(f"    Requests: {model['chat_stats']['total_requests']:,}")
        print(f"    Success Rate: {model['chat_stats']['success_rate']}%")


def example_2_provider_specific():
    """Example 2: Search within a specific provider."""
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Provider-Specific Search - GPT-4 on OpenRouter")
    print("=" * 80)

    results = search_models_with_chat_stats(
        query="gpt 4",
        provider_name="openrouter",
        limit=10
    )

    print(f"\nFound {len(results)} GPT-4 models on OpenRouter")

    for model in results:
        stats = model['chat_stats']
        print(f"\n  - {model['model_name']}")
        print(f"    Model ID: {model['provider_model_id']}")
        print(f"    Requests: {stats['total_requests']:,}")
        print(f"    Avg Response Time: {stats['avg_processing_time_ms']:.2f}ms")


def example_3_cost_analysis():
    """Example 3: Analyze costs across different providers."""
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Cost Analysis - GPT-4 across providers")
    print("=" * 80)

    results = search_models_with_chat_stats(query="gpt 4")

    # Filter only models with usage data
    used_models = [m for m in results if m['chat_stats']['total_requests'] > 0]

    print(f"\nAnalyzing {len(used_models)} GPT-4 models with usage data:\n")

    cost_data = []
    for model in used_models:
        if not model.get('pricing_prompt') or not model.get('pricing_completion'):
            continue

        stats = model['chat_stats']
        avg_input = stats['avg_input_tokens']
        avg_output = stats['avg_output_tokens']

        # Calculate average cost per request
        prompt_cost = avg_input * model['pricing_prompt']
        completion_cost = avg_output * model['pricing_completion']
        total_cost_per_request = prompt_cost + completion_cost

        # Calculate total cost for all requests
        total_requests = stats['total_requests']
        total_cost = total_cost_per_request * total_requests

        cost_data.append({
            'model': model['model_name'],
            'provider': model['provider']['name'],
            'avg_cost': total_cost_per_request,
            'total_cost': total_cost,
            'requests': total_requests
        })

    # Sort by average cost
    cost_data.sort(key=lambda x: x['avg_cost'])

    for item in cost_data[:5]:  # Show top 5 cheapest
        print(f"  {item['model']} ({item['provider']})")
        print(f"    Avg cost/request: ${item['avg_cost']:.4f}")
        print(f"    Total spent: ${item['total_cost']:.2f} ({item['requests']:,} requests)\n")


def example_4_performance_comparison():
    """Example 4: Compare performance metrics across providers."""
    print("\n" + "=" * 80)
    print("EXAMPLE 4: Performance Comparison - Claude models")
    print("=" * 80)

    results = search_models_with_chat_stats(query="claude")

    # Filter models with sufficient data
    significant_models = [
        m for m in results
        if m['chat_stats']['total_requests'] >= 50  # At least 50 requests
    ]

    print(f"\nComparing {len(significant_models)} Claude models with 50+ requests:\n")

    # Sort by success rate (descending), then by avg processing time (ascending)
    significant_models.sort(
        key=lambda x: (-x['chat_stats']['success_rate'], x['chat_stats']['avg_processing_time_ms'])
    )

    for model in significant_models[:5]:  # Top 5
        stats = model['chat_stats']
        print(f"  {model['model_name']} ({model['provider']['name']})")
        print(f"    Success Rate: {stats['success_rate']:.2f}%")
        print(f"    Avg Response Time: {stats['avg_processing_time_ms']:.0f}ms")
        print(f"    Total Requests: {stats['total_requests']:,}")
        print(f"    Completed/Failed: {stats['completed_requests']}/{stats['failed_requests']}\n")


def example_5_token_usage_analysis():
    """Example 5: Analyze token usage patterns."""
    print("\n" + "=" * 80)
    print("EXAMPLE 5: Token Usage Analysis - Llama models")
    print("=" * 80)

    results = search_models_with_chat_stats(query="llama")

    # Filter models with usage
    used_models = [m for m in results if m['chat_stats']['total_requests'] > 0]

    print(f"\nAnalyzing token usage for {len(used_models)} Llama models:\n")

    for model in used_models[:5]:  # Top 5 by usage
        stats = model['chat_stats']
        print(f"  {model['model_name']} ({model['provider']['name']})")
        print(f"    Total Tokens: {stats['total_tokens']:,}")
        print(f"    Avg Input: {stats['avg_input_tokens']:.0f} tokens")
        print(f"    Avg Output: {stats['avg_output_tokens']:.0f} tokens")
        print(f"    Input/Output Ratio: {stats['avg_input_tokens'] / max(stats['avg_output_tokens'], 1):.2f}")
        print(f"    Requests: {stats['total_requests']:,}\n")


def example_6_find_best_value():
    """Example 6: Find best value models (performance per dollar)."""
    print("\n" + "=" * 80)
    print("EXAMPLE 6: Best Value Models - High performance, low cost")
    print("=" * 80)

    # Search across multiple model families
    all_results = []
    for query in ["gpt", "claude", "llama", "mistral"]:
        all_results.extend(search_models_with_chat_stats(query=query))

    # Filter: must have usage data, pricing, and good success rate
    quality_models = [
        m for m in all_results
        if m['chat_stats']['total_requests'] >= 20
        and m['chat_stats']['success_rate'] >= 95
        and m.get('pricing_prompt')
        and m.get('pricing_completion')
    ]

    # Calculate value score (lower is better)
    # Score = (avg_cost_per_request / success_rate) * avg_processing_time_ms
    for model in quality_models:
        stats = model['chat_stats']
        avg_cost = (
            stats['avg_input_tokens'] * model['pricing_prompt'] +
            stats['avg_output_tokens'] * model['pricing_completion']
        )
        # Normalize: cost (dollars) * time (seconds) / success_rate
        value_score = (avg_cost * stats['avg_processing_time_ms'] / 1000) / (stats['success_rate'] / 100)
        model['value_score'] = value_score

    # Sort by value score (lower is better)
    quality_models.sort(key=lambda x: x['value_score'])

    print(f"\nTop 5 best value models (high performance, low cost):\n")

    for i, model in enumerate(quality_models[:5], 1):
        stats = model['chat_stats']
        avg_cost = (
            stats['avg_input_tokens'] * model['pricing_prompt'] +
            stats['avg_output_tokens'] * model['pricing_completion']
        )

        print(f"{i}. {model['model_name']} ({model['provider']['name']})")
        print(f"   Success Rate: {stats['success_rate']:.1f}%")
        print(f"   Avg Response Time: {stats['avg_processing_time_ms']:.0f}ms")
        print(f"   Avg Cost/Request: ${avg_cost:.4f}")
        print(f"   Requests: {stats['total_requests']:,}")
        print(f"   Value Score: {model['value_score']:.6f}\n")


def main():
    """Run all examples."""
    print("\n" + "=" * 80)
    print("MODEL SEARCH WITH CHAT STATISTICS - EXAMPLES")
    print("=" * 80)

    try:
        # Run each example
        example_1_basic_search()
        example_2_provider_specific()
        example_3_cost_analysis()
        example_4_performance_comparison()
        example_5_token_usage_analysis()
        example_6_find_best_value()

        print("\n" + "=" * 80)
        print("All examples completed successfully!")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"\nError running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
