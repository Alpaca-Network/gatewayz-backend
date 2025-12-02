#!/usr/bin/env python3
"""
Simple validation script to check if openrouter/auto is accessible
"""
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def check_model_transformations():
    """Check if model transformation logic handles openrouter/auto correctly"""
    print("\n=== Checking Model Transformation Logic ===")
    try:
        from src.services.model_transformations import transform_model_id, detect_provider_from_model_id

        # Test 1: Should preserve openrouter/auto for OpenRouter provider
        result = transform_model_id("openrouter/auto", "openrouter")
        if result == "openrouter/auto":
            print("✅ OpenRouter provider: openrouter/auto preserved correctly")
        else:
            print(f"❌ OpenRouter provider: Expected 'openrouter/auto', got '{result}'")
            return False

        # Test 2: Should map to fallback for other providers
        result_cerebras = transform_model_id("openrouter/auto", "cerebras")
        if result_cerebras != "openrouter/auto":
            print(f"✅ Cerebras provider: Correctly maps to fallback '{result_cerebras}'")
        else:
            print(f"❌ Cerebras provider: Should map to fallback, but got '{result_cerebras}'")
            return False

        # Test 3: Should detect openrouter as the provider
        provider = detect_provider_from_model_id("openrouter/auto")
        if provider == "openrouter":
            print(f"✅ Provider detection: Correctly detects 'openrouter'")
        else:
            print(f"❌ Provider detection: Expected 'openrouter', got '{provider}'")
            return False

        return True
    except Exception as e:
        print(f"❌ Error in model transformation check: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_ai_sdk_routing():
    """Check if AI SDK endpoint has openrouter/auto routing logic"""
    print("\n=== Checking AI SDK Routing Logic ===")
    try:
        from src.routes.ai_sdk import _is_openrouter_model

        # Test if openrouter/auto is detected as OpenRouter model
        result = _is_openrouter_model("openrouter/auto")
        if result is True:
            print("✅ AI SDK endpoint: openrouter/auto correctly identified as OpenRouter model")
        else:
            print(f"❌ AI SDK endpoint: openrouter/auto should be identified as OpenRouter model")
            return False

        # Test that other models are not misidentified
        result2 = _is_openrouter_model("openai/gpt-4o")
        if result2 is False:
            print("✅ AI SDK endpoint: openai/gpt-4o correctly NOT identified as OpenRouter model")
        else:
            print(f"❌ AI SDK endpoint: openai/gpt-4o should NOT be identified as OpenRouter model")
            return False

        return True
    except Exception as e:
        print(f"❌ Error in AI SDK routing check: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_openrouter_client():
    """Check if OpenRouter client is properly configured"""
    print("\n=== Checking OpenRouter Client ===")
    try:
        from src.services.openrouter_client import get_openrouter_client
        from src.config import Config

        # Check if API key is configured
        if Config.OPENROUTER_API_KEY:
            print(f"✅ OPENROUTER_API_KEY is configured (length: {len(Config.OPENROUTER_API_KEY)})")
        else:
            print("⚠️  OPENROUTER_API_KEY is not configured - API calls will fail")
            print("   To fix: Set OPENROUTER_API_KEY in your environment")
            return True  # This is not a validation failure, just a warning

        # Try to get the client
        try:
            client = get_openrouter_client()
            print("✅ OpenRouter client can be instantiated")
            return True
        except ValueError as e:
            print(f"⚠️  OpenRouter client creation failed: {e}")
            return True  # Configuration issue, not a code issue

    except Exception as e:
        print(f"❌ Error checking OpenRouter client: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_fallback_configuration():
    """Check if openrouter/auto fallbacks are configured"""
    print("\n=== Checking Fallback Configuration ===")
    try:
        from src.services.model_transformations import OPENROUTER_AUTO_FALLBACKS

        if OPENROUTER_AUTO_FALLBACKS:
            print(f"✅ Fallback mappings configured for {len(OPENROUTER_AUTO_FALLBACKS)} providers:")
            for provider, fallback in sorted(OPENROUTER_AUTO_FALLBACKS.items()):
                print(f"   - {provider}: {fallback}")
        else:
            print("⚠️  No fallback mappings configured")

        # Check some expected providers
        expected_providers = ["cerebras", "huggingface", "fireworks", "google-vertex"]
        missing = [p for p in expected_providers if p not in OPENROUTER_AUTO_FALLBACKS]
        if missing:
            print(f"⚠️  Missing fallback mappings for: {', '.join(missing)}")
        else:
            print(f"✅ All expected providers have fallback mappings")

        return True
    except Exception as e:
        print(f"❌ Error checking fallback configuration: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all validation checks"""
    print("=" * 80)
    print("OPENROUTER/AUTO VALIDATION SCRIPT")
    print("=" * 80)

    all_passed = True

    # Run checks
    all_passed &= check_model_transformations()
    all_passed &= check_ai_sdk_routing()
    all_passed &= check_openrouter_client()
    all_passed &= check_fallback_configuration()

    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL VALIDATION CHECKS PASSED")
        print("=" * 80)
        print("\nNext steps to verify full functionality:")
        print("1. Ensure OPENROUTER_API_KEY is set in your environment")
        print("2. Make a test request to /api/chat/ai-sdk with model='openrouter/auto'")
        print("3. Check that the request is routed through OpenRouter (not Vercel AI Gateway)")
        return 0
    else:
        print("❌ SOME VALIDATION CHECKS FAILED")
        print("=" * 80)
        print("\nPlease review the errors above and fix them.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
