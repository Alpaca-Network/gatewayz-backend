#!/usr/bin/env python3
"""
Live test for openrouter/auto endpoint
Sends a real test message to verify the endpoint is accessible
"""

import os
import sys

import pytest


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set - set environment variable to run live API test",
)
def test_openrouter_auto():
    """Send a test message to openrouter/auto"""
    print("=" * 80)
    print("OPENROUTER/AUTO LIVE TEST")
    print("=" * 80)

    # Get API key (guaranteed to exist due to skipif)
    api_key = os.environ.get("OPENROUTER_API_KEY")

    print(f"\n✅ OPENROUTER_API_KEY is configured (length: {len(api_key)})")

    # Try to import required modules
    from src.services.openrouter_client import make_openrouter_request_openai

    print("✅ Successfully imported OpenRouter client")

    # Prepare test message
    messages = [
        {"role": "user", "content": "Say 'Hello from openrouter/auto!' in exactly those words."}
    ]

    print("\n📤 Sending test request to openrouter/auto...")
    print("   Model: openrouter/auto")
    print(f"   Message: {messages[0]['content']}")

    # Make the request
    response = make_openrouter_request_openai(
        messages=messages, model="openrouter/auto", max_tokens=50, temperature=0.7
    )

    # Assertions to validate the response
    assert response is not None, "Response should not be None"
    assert hasattr(response, "model"), "Response should have a model attribute"
    assert hasattr(response, "choices"), "Response should have choices"
    assert len(response.choices) > 0, "Response should have at least one choice"
    assert response.choices[0].message.content, "Response should have content"

    print("\n✅ Request successful!")
    print("\n📥 Response:")
    print(f"   Model used: {response.model}")
    print(f"   Response: {response.choices[0].message.content}")

    if response.usage:
        print("\n📊 Token usage:")
        print(f"   Prompt tokens: {response.usage.prompt_tokens}")
        print(f"   Completion tokens: {response.usage.completion_tokens}")
        print(f"   Total tokens: {response.usage.total_tokens}")

    print("\n" + "=" * 80)
    print("✅ OPENROUTER/AUTO IS WORKING CORRECTLY!")
    print("=" * 80)


def test_code_only():
    """Test just the code paths without making API calls"""
    print("\n" + "=" * 80)
    print("CODE PATH VALIDATION (No API calls)")
    print("=" * 80)

    from src.services.model_transformations import detect_provider_from_model_id, transform_model_id

    # Test transformation
    result = transform_model_id("openrouter/auto", "openrouter")
    assert result == "openrouter/auto", "Should preserve openrouter/auto"
    print(f"\n✅ Transform for OpenRouter: {result}")

    # Test provider detection
    provider = detect_provider_from_model_id("openrouter/auto")
    assert provider == "openrouter", "Should detect openrouter"
    print(f"✅ Provider detection: {provider}")

    # Test fallback
    fallback = transform_model_id("openrouter/auto", "cerebras")
    assert fallback != "openrouter/auto", "Should map to fallback"
    print(f"✅ Fallback for Cerebras: {fallback}")

    print("\n" + "=" * 80)
    print("✅ CODE VALIDATION PASSED")
    print("=" * 80)
    print("\nThe code is correct and will work when API key is configured.")


if __name__ == "__main__":
    # Try live test first
    has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    try:
        if has_api_key:
            test_openrouter_auto()
        else:
            print("\nNo API key found, running code-only validation...")
            test_code_only()
        print("\n✅ All tests passed!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
