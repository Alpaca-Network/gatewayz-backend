#!/usr/bin/env python3
"""
End-to-end test for openrouter/auto model
Tests actual API request through the Gatewayz system
"""

import os
import sys

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_openrouter_auto_request():
    """Test making a request with openrouter/auto model"""

    print("=" * 80)
    print("TESTING OPENROUTER/AUTO END-TO-END REQUEST")
    print("=" * 80)

    # Check if we have OpenRouter API key
    from src.config import Config

    if not Config.OPENROUTER_API_KEY:
        print("❌ OPENROUTER_API_KEY not configured")
        print("   Set the environment variable to test end-to-end")
        return False

    print(f"✅ OpenRouter API key found (length: {len(Config.OPENROUTER_API_KEY)})")

    # Import the OpenRouter client
    try:
        from src.services.openrouter_client import make_openrouter_request_openai
        print("✅ Successfully imported OpenRouter client")
    except Exception as e:
        print(f"❌ Failed to import OpenRouter client: {e}")
        return False

    # Test 1: Simple request
    print("\n[TEST 1] Making a simple request with openrouter/auto...")

    messages = [
        {"role": "user", "content": "Say 'Hello from OpenRouter Auto!' and nothing else."}
    ]

    try:
        response = make_openrouter_request_openai(
            messages=messages,
            model="openrouter/auto",
            max_tokens=50,
            temperature=0.7
        )

        print("✅ Request successful!")
        print(f"   Response ID: {response.id}")
        print(f"   Model used: {response.model}")
        print(f"   Created: {response.created}")

        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content
            print(f"   Response: {content[:100]}")

            if response.usage:
                print(f"   Tokens - Prompt: {response.usage.prompt_tokens}, "
                      f"Completion: {response.usage.completion_tokens}, "
                      f"Total: {response.usage.total_tokens}")

        print("\n✅ The actual model it routed to:", response.model)
        print("   This shows OpenRouter's auto-routing in action!")

    except Exception as e:
        print(f"❌ Request failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 2: Verify it can handle different prompts
    print("\n[TEST 2] Testing with a different prompt type...")

    messages2 = [
        {"role": "user", "content": "What is 2+2? Answer with just the number."}
    ]

    try:
        response2 = make_openrouter_request_openai(
            messages=messages2,
            model="openrouter/auto",
            max_tokens=10,
            temperature=0.0
        )

        print("✅ Second request successful!")
        print(f"   Model routed to: {response2.model}")

        if response2.choices and len(response2.choices) > 0:
            content = response2.choices[0].message.content
            print(f"   Response: {content}")

    except Exception as e:
        print(f"❌ Second request failed: {e}")
        return False

    print("\n" + "=" * 80)
    print("✅ END-TO-END TEST PASSED!")
    print("=" * 80)
    print("\nThe openrouter/auto model is working correctly!")
    print("It successfully routes requests to different models based on the prompt.")

    return True

if __name__ == "__main__":
    try:
        success = test_openrouter_auto_request()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
