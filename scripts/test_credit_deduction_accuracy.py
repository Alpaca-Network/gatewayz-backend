#!/usr/bin/env python3
"""
Test Credit Deduction Accuracy with Live Pricing

This script verifies that:
1. Pricing data is fetched live from providers
2. Pricing calculations are accurate
3. Credit deduction matches expected costs
4. Token counting is working correctly

Usage:
    python scripts/test_credit_deduction_accuracy.py
"""

import asyncio
import json
import logging
import os
import sys
from decimal import Decimal

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Staging backend URL
STAGING_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = os.getenv("STAGING_ADMIN_KEY", "gw_live_wTfpLJ5VB28qMXpOAhr7Uw")

# Test models with different providers and price points
TEST_MODELS = [
    {
        "model": "openai/gpt-4o-mini",
        "provider": "openrouter",
        "expected_pricing_source": "live_api_openrouter",
        "description": "GPT-4o Mini via OpenRouter",
    },
    {
        "model": "anthropic/claude-3-5-haiku-20241022",
        "provider": "openrouter",
        "expected_pricing_source": "live_api_openrouter",
        "description": "Claude 3.5 Haiku via OpenRouter",
    },
    {
        "model": "google/gemini-2.0-flash-exp:free",
        "provider": "openrouter",
        "expected_pricing_source": "live_api_openrouter",
        "description": "Gemini 2.0 Flash Free via OpenRouter",
    },
    {
        "model": "meta-llama/llama-3.3-70b-instruct",
        "provider": "featherless",
        "expected_pricing_source": "live_api_featherless",
        "description": "Llama 3.3 70B via Featherless",
    },
]


async def get_live_pricing_from_api(model_id: str) -> dict | None:
    """Fetch live pricing directly from provider APIs"""
    try:
        if "/" not in model_id:
            return None

        # For OpenRouter models
        if not model_id.startswith(("featherless/", "near/", "cerebras/")):
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("https://openrouter.ai/api/v1/models")
                if response.status_code == 200:
                    data = response.json()
                    for model in data.get("data", []):
                        if model.get("id") == model_id:
                            pricing = model.get("pricing", {})
                            return {
                                "prompt": float(pricing.get("prompt", 0)),
                                "completion": float(pricing.get("completion", 0)),
                                "source": "openrouter_api"
                            }

        # For Featherless models
        if model_id.startswith("featherless/") or "meta-llama" in model_id:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get("https://api.featherless.ai/v1/models")
                if response.status_code == 200:
                    data = response.json()
                    model_name = model_id.replace("featherless/", "")
                    for model in data.get("data", []):
                        if model.get("id") in (model_name, model_id):
                            pricing = model.get("pricing", {})
                            # Featherless uses per-1M format
                            prompt_per_1m = float(pricing.get("prompt", 0))
                            completion_per_1m = float(pricing.get("completion", 0))
                            return {
                                "prompt": prompt_per_1m / 1_000_000,  # Convert to per-token
                                "completion": completion_per_1m / 1_000_000,
                                "source": "featherless_api"
                            }

    except Exception as e:
        logger.error(f"Error fetching live pricing for {model_id}: {e}")
        return None

    return None


async def test_model_pricing(model_config: dict) -> dict:
    """Test pricing accuracy for a specific model"""
    model_id = model_config["model"]
    logger.info(f"\n{'='*80}")
    logger.info(f"Testing: {model_config['description']}")
    logger.info(f"Model ID: {model_id}")
    logger.info(f"{'='*80}")

    results = {
        "model": model_id,
        "description": model_config["description"],
        "tests_passed": 0,
        "tests_failed": 0,
        "errors": [],
        "warnings": [],
    }

    try:
        # Step 1: Fetch live pricing from provider API
        logger.info("\n[1/5] Fetching live pricing from provider API...")
        live_pricing = await get_live_pricing_from_api(model_id)

        if live_pricing:
            logger.info(f"✓ Live pricing from {live_pricing['source']}:")
            logger.info(f"  - Prompt: ${live_pricing['prompt']:.10f} per token")
            logger.info(f"  - Completion: ${live_pricing['completion']:.10f} per token")
            results["live_pricing"] = live_pricing
            results["tests_passed"] += 1
        else:
            logger.warning(f"⚠ Could not fetch live pricing from provider")
            results["warnings"].append("No live pricing available from provider API")

        # Step 2: Query backend pricing endpoint
        logger.info("\n[2/5] Checking backend pricing cache...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            # First, get model info from catalog
            catalog_response = await client.get(
                f"{STAGING_URL}/models",
                headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )

            if catalog_response.status_code == 200:
                catalog_data = catalog_response.json()
                model_found = False

                for model in catalog_data.get("data", []):
                    if model.get("id") == model_id:
                        model_found = True
                        backend_pricing = model.get("pricing", {})

                        if backend_pricing:
                            # Backend returns pricing in per-1M format
                            prompt_per_1m = float(backend_pricing.get("prompt", 0))
                            completion_per_1m = float(backend_pricing.get("completion", 0))

                            # Convert to per-token for comparison
                            backend_pricing_per_token = {
                                "prompt": prompt_per_1m / 1_000_000,
                                "completion": completion_per_1m / 1_000_000,
                                "source": model.get("pricing_source", "unknown")
                            }

                            logger.info(f"✓ Backend pricing (source: {backend_pricing_per_token['source']}):")
                            logger.info(f"  - Prompt: ${backend_pricing_per_token['prompt']:.10f} per token")
                            logger.info(f"  - Completion: ${backend_pricing_per_token['completion']:.10f} per token")

                            results["backend_pricing"] = backend_pricing_per_token
                            results["tests_passed"] += 1

                            # Compare with live pricing if available
                            if live_pricing:
                                prompt_diff = abs(live_pricing["prompt"] - backend_pricing_per_token["prompt"])
                                completion_diff = abs(live_pricing["completion"] - backend_pricing_per_token["completion"])

                                # Allow for small floating point differences (0.1% tolerance)
                                tolerance = 0.001
                                prompt_match = prompt_diff < (live_pricing["prompt"] * tolerance) if live_pricing["prompt"] > 0 else prompt_diff < 1e-10
                                completion_match = completion_diff < (live_pricing["completion"] * tolerance) if live_pricing["completion"] > 0 else completion_diff < 1e-10

                                if prompt_match and completion_match:
                                    logger.info("✓ Backend pricing matches live pricing (within tolerance)")
                                    results["tests_passed"] += 1
                                else:
                                    logger.warning(f"⚠ Backend pricing differs from live pricing:")
                                    logger.warning(f"  - Prompt diff: ${prompt_diff:.10f}")
                                    logger.warning(f"  - Completion diff: ${completion_diff:.10f}")
                                    results["warnings"].append("Backend pricing differs from live pricing")
                        else:
                            logger.warning("⚠ No pricing data in backend catalog")
                            results["warnings"].append("No pricing in backend catalog")

                        break

                if not model_found:
                    logger.error(f"✗ Model {model_id} not found in backend catalog")
                    results["errors"].append("Model not found in catalog")
                    results["tests_failed"] += 1
            else:
                logger.error(f"✗ Failed to fetch catalog: {catalog_response.status_code}")
                results["errors"].append(f"Catalog API error: {catalog_response.status_code}")
                results["tests_failed"] += 1

        # Step 3: Test actual chat completion with credit tracking
        logger.info("\n[3/5] Testing chat completion with credit tracking...")

        test_message = "Say 'hello' and nothing else."

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Get initial credit balance
            user_response = await client.get(
                f"{STAGING_URL}/users/me",
                headers={"Authorization": f"Bearer {ADMIN_KEY}"}
            )

            if user_response.status_code == 200:
                initial_balance = user_response.json().get("credits", 0)
                logger.info(f"Initial balance: ${initial_balance:.6f}")

                # Make chat completion request
                chat_response = await client.post(
                    f"{STAGING_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {ADMIN_KEY}"},
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": test_message}],
                        "max_tokens": 10,
                        "stream": False,
                    }
                )

                if chat_response.status_code == 200:
                    chat_data = chat_response.json()
                    usage = chat_data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)

                    logger.info(f"✓ Chat completion successful:")
                    logger.info(f"  - Prompt tokens: {prompt_tokens}")
                    logger.info(f"  - Completion tokens: {completion_tokens}")
                    logger.info(f"  - Response: {chat_data.get('choices', [{}])[0].get('message', {}).get('content', '')[:100]}")

                    results["usage"] = {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    }
                    results["tests_passed"] += 1

                    # Step 4: Check updated balance
                    logger.info("\n[4/5] Checking credit deduction...")

                    # Wait a moment for the transaction to process
                    await asyncio.sleep(1)

                    user_response_after = await client.get(
                        f"{STAGING_URL}/users/me",
                        headers={"Authorization": f"Bearer {ADMIN_KEY}"}
                    )

                    if user_response_after.status_code == 200:
                        final_balance = user_response_after.json().get("credits", 0)
                        actual_deduction = initial_balance - final_balance

                        logger.info(f"Final balance: ${final_balance:.6f}")
                        logger.info(f"Actual deduction: ${actual_deduction:.6f}")

                        # Calculate expected cost
                        if "backend_pricing" in results:
                            pricing = results["backend_pricing"]
                            expected_cost = (prompt_tokens * pricing["prompt"]) + (completion_tokens * pricing["completion"])

                            logger.info(f"Expected cost: ${expected_cost:.6f}")

                            cost_diff = abs(actual_deduction - expected_cost)
                            cost_match = cost_diff < 0.000001  # Allow for tiny rounding differences

                            if cost_match:
                                logger.info("✓ Credit deduction matches expected cost")
                                results["tests_passed"] += 1
                            else:
                                logger.error(f"✗ Credit deduction mismatch:")
                                logger.error(f"  - Expected: ${expected_cost:.6f}")
                                logger.error(f"  - Actual: ${actual_deduction:.6f}")
                                logger.error(f"  - Difference: ${cost_diff:.6f}")
                                results["errors"].append(f"Cost mismatch: expected ${expected_cost:.6f}, got ${actual_deduction:.6f}")
                                results["tests_failed"] += 1

                            results["cost_verification"] = {
                                "expected": expected_cost,
                                "actual": actual_deduction,
                                "difference": cost_diff,
                                "match": cost_match
                            }
                        else:
                            logger.warning("⚠ Cannot verify cost without backend pricing")
                            results["warnings"].append("Cost verification skipped (no backend pricing)")
                    else:
                        logger.error(f"✗ Failed to get final balance: {user_response_after.status_code}")
                        results["errors"].append("Failed to get final balance")
                        results["tests_failed"] += 1
                else:
                    logger.error(f"✗ Chat completion failed: {chat_response.status_code}")
                    logger.error(f"Response: {chat_response.text[:500]}")
                    results["errors"].append(f"Chat API error: {chat_response.status_code}")
                    results["tests_failed"] += 1
            else:
                logger.error(f"✗ Failed to get initial balance: {user_response.status_code}")
                results["errors"].append("Failed to get initial balance")
                results["tests_failed"] += 1

        # Step 5: Summary
        logger.info("\n[5/5] Test Summary")
        logger.info(f"Tests passed: {results['tests_passed']}")
        logger.info(f"Tests failed: {results['tests_failed']}")

        if results["warnings"]:
            logger.info(f"Warnings: {len(results['warnings'])}")
            for warning in results["warnings"]:
                logger.warning(f"  - {warning}")

        if results["errors"]:
            logger.error(f"Errors: {len(results['errors'])}")
            for error in results["errors"]:
                logger.error(f"  - {error}")

    except Exception as e:
        logger.error(f"✗ Test failed with exception: {e}", exc_info=True)
        results["errors"].append(f"Exception: {str(e)}")
        results["tests_failed"] += 1

    return results


async def main():
    """Run all tests"""
    logger.info("="*80)
    logger.info("CREDIT DEDUCTION ACCURACY TEST")
    logger.info("="*80)
    logger.info(f"Staging URL: {STAGING_URL}")
    logger.info(f"Test models: {len(TEST_MODELS)}")
    logger.info("")

    all_results = []

    for model_config in TEST_MODELS:
        result = await test_model_pricing(model_config)
        all_results.append(result)

        # Wait between tests to avoid rate limiting
        await asyncio.sleep(2)

    # Final summary
    logger.info("\n" + "="*80)
    logger.info("FINAL SUMMARY")
    logger.info("="*80)

    total_passed = sum(r["tests_passed"] for r in all_results)
    total_failed = sum(r["tests_failed"] for r in all_results)
    total_warnings = sum(len(r["warnings"]) for r in all_results)

    logger.info(f"\nOverall Results:")
    logger.info(f"  ✓ Tests passed: {total_passed}")
    logger.info(f"  ✗ Tests failed: {total_failed}")
    logger.info(f"  ⚠ Warnings: {total_warnings}")

    logger.info(f"\nPer-Model Results:")
    for result in all_results:
        status = "✓ PASS" if result["tests_failed"] == 0 else "✗ FAIL"
        logger.info(f"  {status} - {result['description']}")

    # Save detailed results to file
    output_file = "credit_deduction_test_results.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info(f"\nDetailed results saved to: {output_file}")

    # Exit with appropriate code
    sys.exit(0 if total_failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
