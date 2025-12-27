#!/usr/bin/env python3
"""
Test script to demonstrate flexible model search functionality.

This script shows how the improved search function handles variations in model names
with different separators (spaces, hyphens, underscores, dots).
"""

import sys
import os
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.db.models_catalog_db import search_models


def test_search_variations():
    """Test various search query formats."""

    test_queries = [
        "gpt 4",      # Should match: gpt-4, gpt4, gpt_4, gpt 4, gpt-4-turbo, etc.
        "gpt-4",      # Should match: gpt 4, gpt4, gpt_4, gpt-4, etc.
        "gpt4",       # Should match: gpt-4, gpt 4, gpt_4, etc.
        "claude 3",   # Should match: claude-3, claude3, claude_3, claude-3-opus, etc.
        "claude3",    # Should match: claude-3, claude 3, etc.
        "llama 3",    # Should match: llama-3, llama3, llama-3-70b, etc.
    ]

    print("=" * 80)
    print("Model Search Test - Flexible Matching")
    print("=" * 80)
    print()

    for query in test_queries:
        print(f"\nSearching for: '{query}'")
        print("-" * 40)

        results = search_models(query)

        if results:
            print(f"Found {len(results)} models:")
            for result in results[:10]:  # Show first 10 results
                model_name = result.get('model_name', 'N/A')
                model_id = result.get('model_id', 'N/A')
                provider_name = result.get('providers', {}).get('name', 'N/A')
                print(f"  - {model_name} (ID: {model_id}) from {provider_name}")

            if len(results) > 10:
                print(f"  ... and {len(results) - 10} more")
        else:
            print("  No models found")

    print("\n" + "=" * 80)


def search_specific_model(query: str):
    """Search for a specific model and display results."""

    print(f"\nSearching for: '{query}'")
    print("=" * 80)

    results = search_models(query)

    if results:
        print(f"\nFound {len(results)} matching models:\n")
        for idx, result in enumerate(results, 1):
            model_name = result.get('model_name', 'N/A')
            model_id = result.get('model_id', 'N/A')
            provider_name = result.get('providers', {}).get('name', 'N/A')
            provider_slug = result.get('providers', {}).get('slug', 'N/A')
            context_length = result.get('context_length', 'N/A')
            pricing_prompt = result.get('pricing_prompt', 'N/A')

            print(f"{idx}. {model_name}")
            print(f"   ID: {model_id}")
            print(f"   Provider: {provider_name} ({provider_slug})")
            print(f"   Context Length: {context_length}")
            print(f"   Pricing (prompt): ${pricing_prompt}/token" if pricing_prompt != 'N/A' else "   Pricing: N/A")
            print()
    else:
        print("\nNo models found matching your query.")

    print("=" * 80)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Search for specific query provided as command line argument
        query = ' '.join(sys.argv[1:])
        search_specific_model(query)
    else:
        # Run default test suite
        test_search_variations()
