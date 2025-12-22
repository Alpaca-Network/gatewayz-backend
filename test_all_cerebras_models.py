#!/usr/bin/env python3
"""Test all Cerebras models to verify they work correctly"""

import os
import sys
from openai import OpenAI

# All Cerebras models according to official API documentation
CEREBRAS_MODELS = [
    "llama3.1-8b",
    "llama3.1-70b",
    "llama-3.3-70b",
    "qwen-3-32b",
    # Preview models (may require special access)
    # "qwen-3-235b-a22b-instruct-2507",
    # "zai-glm-4.6",
]

def test_cerebras_model(client, model_id):
    """Test a single Cerebras model"""
    try:
        print(f"\n{'='*70}")
        print(f"Testing: {model_id}")
        print(f"{'='*70}")

        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "user", "content": "Say 'Hello, I am working!' in exactly 5 words."}
            ],
            max_tokens=50,
            temperature=0.7
        )

        if response and response.choices:
            content = response.choices[0].message.content
            print(f"✅ SUCCESS")
            print(f"   Response: {content}")
            print(f"   Model: {response.model}")
            if response.usage:
                print(f"   Tokens: {response.usage.total_tokens}")
            return True
        else:
            print(f"❌ FAILED - No response")
            return False

    except Exception as e:
        print(f"❌ FAILED - {type(e).__name__}: {str(e)}")
        return False

def main():
    """Main test function"""
    api_key = os.getenv("CEREBRAS_API_KEY")

    if not api_key:
        print("❌ ERROR: CEREBRAS_API_KEY not found in environment")
        print("   Please set CEREBRAS_API_KEY to test")
        print("\n   To test transformations only (without API key):")
        print("   Run: python3 -c \"from src.services.model_transformations import transform_model_id; ...\"")
        return 1

    print(f"🔑 Using Cerebras API Key: ...{api_key[-8:]}")

    client = OpenAI(
        base_url="https://api.cerebras.ai/v1",
        api_key=api_key,
        timeout=120.0
    )

    results = {}

    for model in CEREBRAS_MODELS:
        results[model] = test_cerebras_model(client, model)

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for model, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status:10s} {model}")

    print(f"\nTotal: {passed}/{total} models working")

    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
