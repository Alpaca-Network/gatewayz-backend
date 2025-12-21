#!/usr/bin/env python3
"""Test Cerebras model ID transformations"""

import sys
sys.path.insert(0, '/root/repo/backend')

from src.services.model_transformations import transform_model_id, detect_provider_from_model_id

# Comprehensive test cases for all Cerebras model variations
TEST_CASES = {
    # Qwen 3 32B - Production model
    "qwen-3-32b": {
        "inputs": [
            "cerebras/qwen-3-32b",
            "cerebras/qwen-3-32b-instruct",
            "qwen-3-32b",
            "qwen3-32b",
            "qwen/qwen3-32b",
            "qwen/qwen-3-32b",
        ],
        "expected_output": "qwen-3-32b",
        "expected_provider": "cerebras",
    },

    # Qwen 3 235B - Preview model
    "qwen-3-235b": {
        "inputs": [
            "cerebras/qwen-3-235b",
            "cerebras/qwen-3-235b-instruct",
            "qwen/qwen-3-235b",
            "qwen-3-235b",
        ],
        "expected_output": "qwen-3-235b-a22b-instruct-2507",
        "expected_provider": "cerebras",
    },

    # Z.ai GLM 4.6 - Preview model
    "zai-glm-4.6": {
        "inputs": [
            "cerebras/zai-glm-4.6",
            "zai-glm-4.6",
            "zai/glm-4.6",
        ],
        "expected_output": "zai-glm-4.6",
        "expected_provider": "cerebras",
    },

    # Llama 3.3 70B
    "llama-3.3-70b": {
        "inputs": [
            "cerebras/llama-3.3-70b",
            "cerebras/llama-3.3-70b-instruct",
            "llama-3.3-70b",
        ],
        "expected_output": "llama-3.3-70b",
        "expected_provider": "cerebras",
    },

    # Llama 3.1 70B
    "llama3.1-70b": {
        "inputs": [
            "cerebras/llama-3.1-70b",
            "cerebras/llama-3.1-70b-instruct",
            "llama3.1-70b",
        ],
        "expected_output": "llama3.1-70b",
        "expected_provider": "cerebras",
    },

    # Llama 3.1 8B
    "llama3.1-8b": {
        "inputs": [
            "cerebras/llama-3.1-8b",
            "cerebras/llama-3.1-8b-instruct",
            "llama3.1-8b",
        ],
        "expected_output": "llama3.1-8b",
        "expected_provider": "cerebras",
    },

    # Llama 3.1 405B
    "llama3.1-405b": {
        "inputs": [
            "cerebras/llama-3.1-405b",
            "llama3.1-405b",
        ],
        "expected_output": "llama3.1-405b",
        "expected_provider": "cerebras",
    },

    # Llama 3.3 405B
    "llama-3.3-405b": {
        "inputs": [
            "cerebras/llama-3.3-405b",
            "llama-3.3-405b",
        ],
        "expected_output": "llama-3.3-405b",
        "expected_provider": "cerebras",
    },
}

def test_transformations():
    """Test all model ID transformations"""
    print("Testing Cerebras Model ID Transformations")
    print("=" * 80)

    total_tests = 0
    passed_tests = 0
    failed_tests = []

    for model_name, test_data in TEST_CASES.items():
        print(f"\n📝 Testing {model_name}:")
        print(f"   Expected output: {test_data['expected_output']}")
        print(f"   Expected provider: {test_data['expected_provider']}")
        print()

        for input_id in test_data["inputs"]:
            total_tests += 1

            # Test transformation
            result = transform_model_id(input_id, "cerebras")
            transform_ok = result == test_data["expected_output"]

            # Test provider detection
            detected = detect_provider_from_model_id(input_id)
            provider_ok = detected == test_data["expected_provider"]

            if transform_ok and provider_ok:
                status = "✅"
                passed_tests += 1
            else:
                status = "❌"
                failed_tests.append({
                    "input": input_id,
                    "expected": test_data["expected_output"],
                    "got": result,
                    "expected_provider": test_data["expected_provider"],
                    "got_provider": detected,
                })

            print(f"   {status} {input_id:45s} -> {result:40s} (provider: {detected or 'None'})")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total tests: {total_tests}")
    print(f"Passed: {passed_tests} ✅")
    print(f"Failed: {len(failed_tests)} ❌")

    if failed_tests:
        print("\nFailed tests:")
        for fail in failed_tests:
            print(f"  ❌ {fail['input']}")
            print(f"     Expected: {fail['expected']}")
            print(f"     Got: {fail['got']}")
            print(f"     Expected provider: {fail['expected_provider']}")
            print(f"     Got provider: {fail['got_provider']}")

    return 0 if len(failed_tests) == 0 else 1

if __name__ == "__main__":
    sys.exit(test_transformations())
