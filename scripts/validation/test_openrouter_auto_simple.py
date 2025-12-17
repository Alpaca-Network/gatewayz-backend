#!/usr/bin/env python3
"""
Simple validation of openrouter/auto model without heavy dependencies
This validates the model exists in OpenRouter's API
"""

import json
import urllib.request
import sys

def test_openrouter_auto_in_api():
    """Check if openrouter/auto exists in OpenRouter's public API"""

    print("=" * 80)
    print("VALIDATING OPENROUTER/AUTO MODEL")
    print("=" * 80)

    print("\n[TEST] Checking OpenRouter API for 'openrouter/auto' model...")

    try:
        # Fetch from OpenRouter's public API (no auth needed for model list)
        url = "https://openrouter.ai/api/v1/models"
        req = urllib.request.Request(url)
        req.add_header('Content-Type', 'application/json')

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())

        models = data.get("data", [])
        print(f"   Fetched {len(models)} models from OpenRouter API")

        # Search for openrouter/auto
        auto_model = None
        for model in models:
            if model.get("id") == "openrouter/auto":
                auto_model = model
                break

        if auto_model:
            print("\n✅ SUCCESS: Found 'openrouter/auto' in OpenRouter API!")
            print("\nModel Details:")
            print(f"  ID: {auto_model.get('id')}")
            print(f"  Name: {auto_model.get('name')}")
            print(f"  Context Length: {auto_model.get('context_length')}")

            description = auto_model.get('description', '')
            if description:
                # Print first 200 chars of description
                print(f"  Description: {description[:200]}...")

            # Check pricing
            pricing = auto_model.get('pricing', {})
            print(f"  Pricing - Prompt: {pricing.get('prompt')}, Completion: {pricing.get('completion')}")
            print("  Note: -1 pricing means it routes to various models with their own pricing")

            # Check architecture
            architecture = auto_model.get('architecture', {})
            print(f"  Modality: {architecture.get('modality')}")

            print("\n" + "=" * 80)
            print("✅ VALIDATION PASSED: openrouter/auto is a valid OpenRouter model")
            print("=" * 80)
            return True
        else:
            print("\n❌ FAILED: 'openrouter/auto' NOT found in OpenRouter API")
            print(f"   Total models checked: {len(models)}")

            # Show some sample model IDs for debugging
            print("\n   Sample model IDs from API:")
            for model in models[:5]:
                print(f"     - {model.get('id')}")

            return False

    except Exception as e:
        print(f"\n❌ ERROR: Failed to fetch from OpenRouter API: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_openrouter_auto_in_api()
    sys.exit(0 if success else 1)
