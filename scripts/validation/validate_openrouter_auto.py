#!/usr/bin/env python3
"""
Validation script for openrouter/auto model
This script checks:
1. If the model exists in OpenRouter's API catalog
2. If the model can be fetched through our service
3. If the model transformation logic works correctly
"""

import os
import sys

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.services.models import fetch_models_from_openrouter, fetch_specific_model
from src.services.model_transformations import transform_model_id, detect_provider_from_model_id

def validate_openrouter_auto():
    """Validate that openrouter/auto model works correctly"""

    print("=" * 80)
    print("VALIDATING OPENROUTER/AUTO MODEL")
    print("=" * 80)

    # Test 1: Check if OpenRouter API returns the auto model
    print("\n[TEST 1] Fetching models from OpenRouter API...")
    try:
        models = fetch_models_from_openrouter()
        if models is None:
            print("❌ Failed to fetch models from OpenRouter (check API key)")
            return False

        auto_model = None
        for model in models:
            if model.get("id") == "openrouter/auto":
                auto_model = model
                break

        if auto_model:
            print("✅ Found openrouter/auto in OpenRouter API")
            print(f"   Name: {auto_model.get('name')}")
            print(f"   Description: {auto_model.get('description', '')[:100]}...")
            print(f"   Context Length: {auto_model.get('context_length')}")
        else:
            print("❌ openrouter/auto NOT found in OpenRouter API")
            return False
    except Exception as e:
        print(f"❌ Error fetching models: {e}")
        return False

    # Test 2: Check if fetch_specific_model can retrieve it
    print("\n[TEST 2] Fetching specific model openrouter/auto...")
    try:
        result = fetch_specific_model("openrouter", "auto", gateway="openrouter")
        if result is not None:
            if result.get("id") == "openrouter/auto":
                print("✅ Successfully fetched openrouter/auto via fetch_specific_model")
                print(f"   Model ID: {result.get('id')}")
                print(f"   Name: {result.get('name')}")
            else:
                print(f"❌ Got wrong model: {result.get('id')}")
                return False
        else:
            print("❌ fetch_specific_model returned None")
            return False
    except Exception as e:
        print(f"❌ Error in fetch_specific_model: {e}")
        return False

    # Test 3: Check model transformation logic
    print("\n[TEST 3] Testing model transformation logic...")
    try:
        # Should preserve the full ID for openrouter provider
        transformed = transform_model_id("openrouter/auto", "openrouter")
        if transformed == "openrouter/auto":
            print("✅ Model ID preserved correctly for OpenRouter provider")
        else:
            print(f"❌ Unexpected transformation for OpenRouter: {transformed}")
            return False

        # Should transform to fallback for other providers
        transformed_hf = transform_model_id("openrouter/auto", "huggingface")
        print(f"   Fallback for HuggingFace: {transformed_hf}")
        if transformed_hf != "openrouter/auto":
            print("✅ Correctly transforms to fallback for HuggingFace")
        else:
            print("❌ Should have transformed to fallback for HuggingFace")
            return False

        transformed_cerebras = transform_model_id("openrouter/auto", "cerebras")
        print(f"   Fallback for Cerebras: {transformed_cerebras}")
        if transformed_cerebras != "openrouter/auto":
            print("✅ Correctly transforms to fallback for Cerebras")
        else:
            print("❌ Should have transformed to fallback for Cerebras")
            return False
    except Exception as e:
        print(f"❌ Error in model transformation: {e}")
        return False

    # Test 4: Check provider detection
    print("\n[TEST 4] Testing provider detection...")
    try:
        provider = detect_provider_from_model_id("openrouter/auto")
        if provider == "openrouter":
            print("✅ Correctly detects 'openrouter' as the provider")
        else:
            print(f"❌ Wrong provider detected: {provider}")
            return False
    except Exception as e:
        print(f"❌ Error in provider detection: {e}")
        return False

    print("\n" + "=" * 80)
    print("✅ ALL VALIDATION TESTS PASSED!")
    print("=" * 80)
    return True

if __name__ == "__main__":
    success = validate_openrouter_auto()
    sys.exit(0 if success else 1)
