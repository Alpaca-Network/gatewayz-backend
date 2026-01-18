#!/usr/bin/env python3
"""
Google Vertex AI Endpoint Performance Comparison Test

This script compares performance between regional and global endpoints for Gemini models.
Tests TTFC (Time To First Chunk), total response time, and reliability metrics.

Usage:
    python scripts/performance/test_google_vertex_endpoints.py

Requirements:
    - GOOGLE_PROJECT_ID environment variable
    - GOOGLE_VERTEX_LOCATION environment variable (regional endpoint, e.g., us-central1)
    - GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_VERTEX_CREDENTIALS_JSON
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import mean, median, stdev
from typing import Any

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from src.config import Config
from src.services.google_vertex_client import (
    _get_model_location,
    make_google_vertex_request_openai,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics for a single test"""

    model: str
    endpoint_type: str  # "global" or "regional"
    location: str
    success: bool
    ttfc: float | None  # Time to first chunk (seconds)
    total_time: float  # Total request time (seconds)
    tokens_generated: int | None
    error_message: str | None
    timestamp: str


@dataclass
class AggregatedMetrics:
    """Aggregated metrics for multiple tests"""

    model: str
    endpoint_type: str
    location: str
    total_tests: int
    successful_tests: int
    failed_tests: int
    success_rate: float
    avg_ttfc: float | None
    median_ttfc: float | None
    min_ttfc: float | None
    max_ttfc: float | None
    stddev_ttfc: float | None
    avg_total_time: float
    median_total_time: float
    min_total_time: float
    max_total_time: float
    stddev_total_time: float
    avg_tokens: float | None
    errors: list[str]


# Test configuration
TEST_CONFIG = {
    "models": [
        "gemini-3-pro-preview",  # Preview model requiring global endpoint
        "gemini-2.5-flash-lite",  # Standard model
        "gemini-1.5-pro",  # Standard model
    ],
    "iterations_per_test": 5,  # Number of times to test each model/endpoint combo
    "test_prompt": "Write a short haiku about artificial intelligence.",
    "max_tokens": 100,
    "temperature": 0.7,
}


def get_regional_location() -> str:
    """Get configured regional location"""
    return Config.GOOGLE_VERTEX_LOCATION or "us-central1"


async def test_endpoint_performance(
    model: str,
    endpoint_type: str,
    use_regional_fallback: bool,
) -> PerformanceMetrics:
    """Test performance of a single endpoint"""

    location = _get_model_location(model, try_regional_fallback=use_regional_fallback)

    logger.info(f"Testing {model} on {endpoint_type} endpoint (location: {location})...")

    start_time = time.time()
    ttfc = None
    success = False
    tokens_generated = None
    error_message = None

    try:
        # Make request
        response = make_google_vertex_request_openai(
            messages=[
                {"role": "user", "content": TEST_CONFIG["test_prompt"]}
            ],
            model=model,
            max_tokens=TEST_CONFIG["max_tokens"],
            temperature=TEST_CONFIG["temperature"],
        )

        # Calculate TTFC (approximate - actual TTFC would be measured in streaming)
        # For non-streaming, we use total request time as approximation
        total_time = time.time() - start_time
        ttfc = total_time  # Approximation for non-streaming

        # Extract tokens
        if "usage" in response:
            tokens_generated = response["usage"].get("completion_tokens")

        success = True
        logger.info(
            f"✓ {model} on {endpoint_type}: {total_time:.2f}s, "
            f"{tokens_generated or 0} tokens"
        )

    except Exception as e:
        total_time = time.time() - start_time
        error_message = str(e)
        logger.error(f"✗ {model} on {endpoint_type}: {error_message}")

    return PerformanceMetrics(
        model=model,
        endpoint_type=endpoint_type,
        location=location,
        success=success,
        ttfc=ttfc,
        total_time=total_time,
        tokens_generated=tokens_generated,
        error_message=error_message,
        timestamp=datetime.now().isoformat(),
    )


def aggregate_metrics(metrics: list[PerformanceMetrics]) -> AggregatedMetrics:
    """Aggregate multiple test metrics"""

    if not metrics:
        raise ValueError("No metrics to aggregate")

    model = metrics[0].model
    endpoint_type = metrics[0].endpoint_type
    location = metrics[0].location

    successful = [m for m in metrics if m.success]
    failed = [m for m in metrics if not m.success]

    ttfc_values = [m.ttfc for m in successful if m.ttfc is not None]
    total_time_values = [m.total_time for m in successful]
    token_values = [m.tokens_generated for m in successful if m.tokens_generated is not None]

    return AggregatedMetrics(
        model=model,
        endpoint_type=endpoint_type,
        location=location,
        total_tests=len(metrics),
        successful_tests=len(successful),
        failed_tests=len(failed),
        success_rate=len(successful) / len(metrics) * 100,
        avg_ttfc=mean(ttfc_values) if ttfc_values else None,
        median_ttfc=median(ttfc_values) if ttfc_values else None,
        min_ttfc=min(ttfc_values) if ttfc_values else None,
        max_ttfc=max(ttfc_values) if ttfc_values else None,
        stddev_ttfc=stdev(ttfc_values) if len(ttfc_values) > 1 else None,
        avg_total_time=mean(total_time_values) if total_time_values else 0,
        median_total_time=median(total_time_values) if total_time_values else 0,
        min_total_time=min(total_time_values) if total_time_values else 0,
        max_total_time=max(total_time_values) if total_time_values else 0,
        stddev_total_time=stdev(total_time_values) if len(total_time_values) > 1 else 0,
        avg_tokens=mean(token_values) if token_values else None,
        errors=[m.error_message for m in failed if m.error_message],
    )


def generate_comparison_report(
    global_metrics: AggregatedMetrics,
    regional_metrics: AggregatedMetrics,
) -> dict[str, Any]:
    """Generate comparison report between global and regional endpoints"""

    # Calculate performance difference
    if global_metrics.avg_ttfc and regional_metrics.avg_ttfc:
        ttfc_improvement = (
            (global_metrics.avg_ttfc - regional_metrics.avg_ttfc) / global_metrics.avg_ttfc * 100
        )
    else:
        ttfc_improvement = None

    total_time_improvement = (
        (global_metrics.avg_total_time - regional_metrics.avg_total_time)
        / global_metrics.avg_total_time
        * 100
    )

    return {
        "model": global_metrics.model,
        "global_endpoint": {
            "location": global_metrics.location,
            "success_rate": f"{global_metrics.success_rate:.1f}%",
            "avg_ttfc": f"{global_metrics.avg_ttfc:.2f}s" if global_metrics.avg_ttfc else "N/A",
            "median_ttfc": f"{global_metrics.median_ttfc:.2f}s" if global_metrics.median_ttfc else "N/A",
            "avg_total_time": f"{global_metrics.avg_total_time:.2f}s",
            "median_total_time": f"{global_metrics.median_total_time:.2f}s",
            "stddev_ttfc": f"{global_metrics.stddev_ttfc:.2f}s" if global_metrics.stddev_ttfc else "N/A",
            "errors": global_metrics.errors,
        },
        "regional_endpoint": {
            "location": regional_metrics.location,
            "success_rate": f"{regional_metrics.success_rate:.1f}%",
            "avg_ttfc": f"{regional_metrics.avg_ttfc:.2f}s" if regional_metrics.avg_ttfc else "N/A",
            "median_ttfc": f"{regional_metrics.median_ttfc:.2f}s" if regional_metrics.median_ttfc else "N/A",
            "avg_total_time": f"{regional_metrics.avg_total_time:.2f}s",
            "median_total_time": f"{regional_metrics.median_total_time:.2f}s",
            "stddev_ttfc": f"{regional_metrics.stddev_ttfc:.2f}s" if regional_metrics.stddev_ttfc else "N/A",
            "errors": regional_metrics.errors,
        },
        "performance_improvement": {
            "ttfc_improvement": f"{ttfc_improvement:+.1f}%" if ttfc_improvement else "N/A",
            "total_time_improvement": f"{total_time_improvement:+.1f}%",
            "winner": (
                "regional" if regional_metrics.avg_total_time < global_metrics.avg_total_time else "global"
            ),
            "recommendation": (
                "Regional endpoint is faster - consider using for production"
                if regional_metrics.avg_total_time < global_metrics.avg_total_time
                else "Global endpoint is faster - keep current configuration"
            ),
        },
    }


async def run_performance_tests() -> dict[str, Any]:
    """Run comprehensive performance tests"""

    logger.info("=" * 80)
    logger.info("Google Vertex AI Endpoint Performance Comparison")
    logger.info("=" * 80)
    logger.info(f"Regional location: {get_regional_location()}")
    logger.info(f"Models to test: {TEST_CONFIG['models']}")
    logger.info(f"Iterations per test: {TEST_CONFIG['iterations_per_test']}")
    logger.info("=" * 80)

    all_results = []
    comparison_reports = []

    for model in TEST_CONFIG["models"]:
        logger.info(f"\n{'=' * 80}")
        logger.info(f"Testing model: {model}")
        logger.info(f"{'=' * 80}")

        # Test global endpoint
        logger.info(f"\n--- Testing GLOBAL endpoint for {model} ---")
        global_metrics_list = []
        for i in range(TEST_CONFIG["iterations_per_test"]):
            logger.info(f"Iteration {i + 1}/{TEST_CONFIG['iterations_per_test']}")
            metrics = await test_endpoint_performance(
                model=model,
                endpoint_type="global",
                use_regional_fallback=False,
            )
            global_metrics_list.append(metrics)
            all_results.append(asdict(metrics))
            await asyncio.sleep(2)  # Brief pause between requests

        # Test regional endpoint
        logger.info(f"\n--- Testing REGIONAL endpoint for {model} ---")
        regional_metrics_list = []
        for i in range(TEST_CONFIG["iterations_per_test"]):
            logger.info(f"Iteration {i + 1}/{TEST_CONFIG['iterations_per_test']}")
            metrics = await test_endpoint_performance(
                model=model,
                endpoint_type="regional",
                use_regional_fallback=True,
            )
            regional_metrics_list.append(metrics)
            all_results.append(asdict(metrics))
            await asyncio.sleep(2)  # Brief pause between requests

        # Aggregate and compare
        global_agg = aggregate_metrics(global_metrics_list)
        regional_agg = aggregate_metrics(regional_metrics_list)
        comparison = generate_comparison_report(global_agg, regional_agg)
        comparison_reports.append(comparison)

        # Print summary for this model
        logger.info(f"\n{'=' * 80}")
        logger.info(f"SUMMARY for {model}")
        logger.info(f"{'=' * 80}")
        logger.info(f"Global endpoint: {comparison['global_endpoint']['avg_total_time']} avg")
        logger.info(f"Regional endpoint: {comparison['regional_endpoint']['avg_total_time']} avg")
        logger.info(f"Improvement: {comparison['performance_improvement']['total_time_improvement']}")
        logger.info(f"Winner: {comparison['performance_improvement']['winner']}")

    return {
        "test_config": TEST_CONFIG,
        "regional_location": get_regional_location(),
        "timestamp": datetime.now().isoformat(),
        "raw_results": all_results,
        "comparison_reports": comparison_reports,
    }


def save_results(results: dict[str, Any], filename: str = "google_vertex_performance_results.json"):
    """Save results to JSON file"""
    output_dir = os.path.join(os.path.dirname(__file__), "../../test_results")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"\n{'=' * 80}")
    logger.info(f"Results saved to: {output_path}")
    logger.info(f"{'=' * 80}")


def print_markdown_report(results: dict[str, Any]):
    """Print results in markdown format"""

    print("\n" + "=" * 80)
    print("# Google Vertex AI Endpoint Performance Report")
    print("=" * 80)
    print()
    print(f"**Test Date**: {results['timestamp']}")
    print(f"**Regional Location**: {results['regional_location']}")
    print(f"**Iterations per Endpoint**: {results['test_config']['iterations_per_test']}")
    print()

    for comparison in results["comparison_reports"]:
        print(f"## Model: {comparison['model']}")
        print()

        print("### Global Endpoint")
        print(f"- **Location**: {comparison['global_endpoint']['location']}")
        print(f"- **Success Rate**: {comparison['global_endpoint']['success_rate']}")
        print(f"- **Avg TTFC**: {comparison['global_endpoint']['avg_ttfc']}")
        print(f"- **Median TTFC**: {comparison['global_endpoint']['median_ttfc']}")
        print(f"- **Avg Total Time**: {comparison['global_endpoint']['avg_total_time']}")
        print(f"- **Std Dev**: {comparison['global_endpoint']['stddev_ttfc']}")
        print()

        print("### Regional Endpoint")
        print(f"- **Location**: {comparison['regional_endpoint']['location']}")
        print(f"- **Success Rate**: {comparison['regional_endpoint']['success_rate']}")
        print(f"- **Avg TTFC**: {comparison['regional_endpoint']['avg_ttfc']}")
        print(f"- **Median TTFC**: {comparison['regional_endpoint']['median_ttfc']}")
        print(f"- **Avg Total Time**: {comparison['regional_endpoint']['avg_total_time']}")
        print(f"- **Std Dev**: {comparison['regional_endpoint']['stddev_ttfc']}")
        print()

        print("### Performance Comparison")
        print(f"- **TTFC Improvement**: {comparison['performance_improvement']['ttfc_improvement']}")
        print(f"- **Total Time Improvement**: {comparison['performance_improvement']['total_time_improvement']}")
        print(f"- **Winner**: {comparison['performance_improvement']['winner']}")
        print(f"- **Recommendation**: {comparison['performance_improvement']['recommendation']}")
        print()
        print("---")
        print()


async def main():
    """Main entry point"""
    try:
        # Validate environment
        if not Config.GOOGLE_PROJECT_ID:
            logger.error("GOOGLE_PROJECT_ID environment variable not set")
            sys.exit(1)

        # Run tests
        results = await run_performance_tests()

        # Save results
        save_results(results)

        # Print markdown report
        print_markdown_report(results)

        logger.info("\n✅ Performance testing complete!")

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
