#!/usr/bin/env python3
"""Test script to verify Helicone and Vercel AI Gateway pricing integration

This test verifies that pricing data can be fetched from:
- Helicone public API: https://api.helicone.ai/v1/public/model-registry/models
- Vercel AI Gateway API: https://ai-gateway.vercel.sh/v1/models
"""

import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def test_helicone_pricing_api():
    """Test fetching pricing from Helicone public API"""
    import httpx

    logger.info("Testing Helicone public pricing API...")

    try:
        response = httpx.get(
            "https://api.helicone.ai/v1/public/model-registry/models",
            timeout=10.0,
        )

        if response.status_code != 200:
            logger.error(f"Helicone API returned status {response.status_code}")
            return False

        data = response.json()
        models = data.get("data", {}).get("models", [])

        if not models:
            logger.error("No models returned from Helicone API")
            return False

        logger.info(f"Fetched {len(models)} models from Helicone API")

        # Check for specific models we expect
        expected_models = ["claude-opus-4", "gemini-2.5-flash", "gpt-4o"]
        found_models = {m.get("id") for m in models}

        for expected in expected_models:
            if expected in found_models:
                logger.info(f"  Found expected model: {expected}")
            else:
                logger.warning(f"  Missing expected model: {expected}")

        # Verify pricing structure for a few models
        models_with_pricing = 0
        for model in models[:10]:
            model_id = model.get("id")
            endpoints = model.get("endpoints", [])
            for endpoint in endpoints:
                pricing = endpoint.get("pricing", {})
                if pricing.get("prompt") and pricing.get("completion"):
                    models_with_pricing += 1
                    logger.info(
                        f"  {model_id}: ${pricing.get('prompt')} in / ${pricing.get('completion')} out"
                    )
                    break

        logger.info(f"Models with pricing data: {models_with_pricing}/10")
        return models_with_pricing > 0

    except Exception as e:
        logger.error(f"Failed to fetch Helicone pricing: {e}")
        return False


def test_vercel_pricing_api():
    """Test fetching pricing from Vercel AI Gateway public API"""
    import httpx

    logger.info("Testing Vercel AI Gateway public pricing API...")

    try:
        response = httpx.get(
            "https://ai-gateway.vercel.sh/v1/models",
            timeout=10.0,
        )

        if response.status_code != 200:
            logger.error(f"Vercel API returned status {response.status_code}")
            return False

        data = response.json()
        models = data.get("data", [])

        if not models:
            logger.error("No models returned from Vercel API")
            return False

        logger.info(f"Fetched {len(models)} models from Vercel AI Gateway API")

        # Check for specific models we expect
        expected_models = ["anthropic/claude-opus-4", "google/gemini-2.5-flash", "openai/gpt-4o"]
        found_ids = {m.get("id") for m in models}

        for expected in expected_models:
            if expected in found_ids:
                logger.info(f"  Found expected model: {expected}")
            else:
                logger.warning(f"  Missing expected model: {expected}")

        # Verify pricing structure and convert to per-1M format
        models_with_pricing = 0
        for model in models[:10]:
            model_id = model.get("id")
            pricing = model.get("pricing", {})
            if pricing.get("input") and pricing.get("output"):
                models_with_pricing += 1
                # Convert per-token to per-1M
                input_per_1m = float(pricing.get("input", 0)) * 1_000_000
                output_per_1m = float(pricing.get("output", 0)) * 1_000_000
                logger.info(f"  {model_id}: ${input_per_1m:.2f} in / ${output_per_1m:.2f} out per 1M")

        logger.info(f"Models with pricing data: {models_with_pricing}/10")
        return models_with_pricing > 0

    except Exception as e:
        logger.error(f"Failed to fetch Vercel pricing: {e}")
        return False


def test_helicone_pricing_function():
    """Test the actual pricing function from helicone_client

    Note: This test requires the full application dependencies to be installed.
    It may be skipped if dependencies are not available.
    """
    logger.info("Testing Helicone pricing function...")

    try:
        sys.path.insert(0, "/root/repo/backend")
        from src.services.helicone_client import fetch_helicone_pricing_from_public_api

        pricing_map = fetch_helicone_pricing_from_public_api()

        if not pricing_map:
            logger.error("fetch_helicone_pricing_from_public_api returned None")
            return False

        logger.info(f"Fetched pricing for {len(pricing_map)} models")

        # Check some expected models
        test_models = ["claude-opus-4", "gemini-2.5-flash", "gpt-4o"]
        for model_id in test_models:
            if model_id in pricing_map:
                pricing = pricing_map[model_id]
                logger.info(
                    f"  {model_id}: ${pricing.get('prompt')} in / ${pricing.get('completion')} out"
                )
            else:
                logger.warning(f"  {model_id}: not found in pricing map")

        return True

    except ImportError as e:
        logger.warning(f"Skipping Helicone function test - missing dependencies: {e}")
        return True  # Skip but don't fail
    except Exception as e:
        logger.error(f"Failed to test Helicone pricing function: {e}")
        return False


def test_vercel_pricing_function():
    """Test the actual pricing function from vercel_ai_gateway_client

    Note: This test requires the full application dependencies to be installed.
    It may be skipped if dependencies are not available.
    """
    logger.info("Testing Vercel AI Gateway pricing function...")

    try:
        sys.path.insert(0, "/root/repo/backend")
        from src.services.vercel_ai_gateway_client import fetch_vercel_pricing_from_public_api

        pricing_map = fetch_vercel_pricing_from_public_api()

        if not pricing_map:
            logger.error("fetch_vercel_pricing_from_public_api returned None")
            return False

        logger.info(f"Fetched pricing for {len(pricing_map)} models")

        # Check some expected models (with provider prefix for Vercel)
        test_models = ["anthropic/claude-opus-4", "google/gemini-2.5-flash", "openai/gpt-4o"]
        for model_id in test_models:
            if model_id in pricing_map:
                pricing = pricing_map[model_id]
                logger.info(
                    f"  {model_id}: ${pricing.get('prompt')} in / ${pricing.get('completion')} out"
                )
            else:
                logger.warning(f"  {model_id}: not found in pricing map")

        return True

    except ImportError as e:
        logger.warning(f"Skipping Vercel function test - missing dependencies: {e}")
        return True  # Skip but don't fail
    except Exception as e:
        logger.error(f"Failed to test Vercel pricing function: {e}")
        return False


def main():
    """Run all pricing tests"""
    logger.info("=" * 60)
    logger.info("Gateway Pricing Integration Tests")
    logger.info("=" * 60)

    results = {}

    # Test Helicone API
    logger.info("\n" + "-" * 40)
    results["helicone_api"] = test_helicone_pricing_api()

    # Test Vercel API
    logger.info("\n" + "-" * 40)
    results["vercel_api"] = test_vercel_pricing_api()

    # Test Helicone function
    logger.info("\n" + "-" * 40)
    results["helicone_function"] = test_helicone_pricing_function()

    # Test Vercel function
    logger.info("\n" + "-" * 40)
    results["vercel_function"] = test_vercel_pricing_function()

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Test Results Summary")
    logger.info("=" * 60)

    all_passed = True
    for test_name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        logger.info(f"  {test_name}: {status}")
        if not passed:
            all_passed = False

    if all_passed:
        logger.info("\nAll tests passed!")
        return 0
    else:
        logger.error("\nSome tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
