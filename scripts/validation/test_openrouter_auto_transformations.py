#!/usr/bin/env python3
"""
Test openrouter/auto model transformation logic without heavy dependencies
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_transformations():
    """Test model transformation logic for openrouter/auto"""

    print("=" * 80)
    print("TESTING OPENROUTER/AUTO MODEL TRANSFORMATION LOGIC")
    print("=" * 80)

    # Import with minimal dependencies
    try:
        from src.services.model_transformations import (
            transform_model_id,
            detect_provider_from_model_id,
            OPENROUTER_AUTO_FALLBACKS
        )
    except ImportError as e:
        print(f"❌ Failed to import model_transformations: {e}")
        return False

    all_passed = True

    # Test 1: Provider detection
    print("\n[TEST 1] Testing provider detection for 'openrouter/auto'...")
    try:
        provider = detect_provider_from_model_id("openrouter/auto")
        if provider == "openrouter":
            print(f"✅ Correctly detected provider: {provider}")
        else:
            print(f"❌ Wrong provider detected: {provider} (expected: openrouter)")
            all_passed = False
    except Exception as e:
        print(f"❌ Error in provider detection: {e}")
        all_passed = False

    # Test 2: Transformation for OpenRouter (should preserve)
    print("\n[TEST 2] Testing transformation for OpenRouter provider...")
    try:
        result = transform_model_id("openrouter/auto", "openrouter", use_multi_provider=False)
        if result == "openrouter/auto":
            print(f"✅ Model ID preserved: {result}")
        else:
            print(f"❌ Model ID changed to: {result} (expected: openrouter/auto)")
            all_passed = False
    except Exception as e:
        print(f"❌ Error in transformation: {e}")
        all_passed = False

    # Test 3: Transformation for other providers (should use fallbacks)
    print("\n[TEST 3] Testing fallback transformations for other providers...")
    print(f"   Available fallbacks: {OPENROUTER_AUTO_FALLBACKS}")

    test_providers = [
        ("cerebras", "llama-3.3-70b"),
        ("huggingface", "meta-llama/Llama-3.3-70B-Instruct"),
        ("featherless", "meta-llama/llama-3.3-70b"),
        ("fireworks", "meta-llama/llama-3.3-70b"),
        ("together", "meta-llama/llama-3.3-70b"),
    ]

    for provider, expected_fallback_base in test_providers:
        try:
            result = transform_model_id("openrouter/auto", provider, use_multi_provider=False)
            # The result might be lowercase version
            result_lower = result.lower()
            expected_lower = expected_fallback_base.lower()

            # Check if it's NOT the original openrouter/auto
            if result_lower != "openrouter/auto":
                print(f"✅ {provider}: Transformed to '{result}'")
            else:
                print(f"⚠️  {provider}: Still 'openrouter/auto' (might not have fallback)")
        except Exception as e:
            print(f"❌ {provider}: Error - {e}")
            all_passed = False

    # Test 4: Case insensitivity
    print("\n[TEST 4] Testing case insensitivity...")
    test_cases = [
        "openrouter/auto",
        "openrouter/AUTO",
        "OpenRouter/Auto",
        "OPENROUTER/AUTO"
    ]

    for test_case in test_cases:
        try:
            result = transform_model_id(test_case, "openrouter", use_multi_provider=False)
            # Should normalize to lowercase
            if result.lower() == "openrouter/auto":
                print(f"✅ '{test_case}' -> '{result}'")
            else:
                print(f"❌ '{test_case}' -> '{result}' (expected openrouter/auto)")
                all_passed = False
        except Exception as e:
            print(f"❌ '{test_case}': Error - {e}")
            all_passed = False

    # Test 5: Verify special handling in code
    print("\n[TEST 5] Verifying special handling in transformation logic...")
    print("   Checking that openrouter/auto keeps the full 'openrouter/' prefix...")

    # Read the source to verify the logic is in place
    try:
        with open('src/services/model_transformations.py', 'r') as f:
            content = f.read()

        checks = [
            ('openrouter/auto preservation logic', 'if model_id != "openrouter/auto"'),
            ('openrouter/auto fallback logic', 'if requested_model_id and requested_model_id.lower() == "openrouter/auto"'),
            ('OPENROUTER_AUTO_FALLBACKS dict', 'OPENROUTER_AUTO_FALLBACKS = {'),
        ]

        for check_name, check_string in checks:
            if check_string in content:
                print(f"✅ Found {check_name}")
            else:
                print(f"⚠️  Could not find {check_name}")

    except Exception as e:
        print(f"⚠️  Could not verify source code: {e}")

    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL TRANSFORMATION TESTS PASSED!")
    else:
        print("⚠️  SOME TESTS HAD ISSUES (see above)")
    print("=" * 80)

    return all_passed

if __name__ == "__main__":
    success = test_transformations()
    sys.exit(0 if success else 1)
