#!/usr/bin/env python3
"""
Comprehensive Performance Test for Gatewayz API
Tests ALL available models with a basic math problem
Saves results incrementally to avoid data loss
"""

import asyncio
import time
import httpx
import json
import csv
from typing import Dict, List
from datetime import datetime
from pathlib import Path
import statistics

# Configuration
API_BASE_URL = "https://api.gatewayz.ai"
API_KEY = "gw_live_keYT21TicJZzxObd8-6LJukxOg5p0CLo_3Yki83w3pU"

# Test configuration
BATCH_SIZE = 1  # Process 1 model at a time (sequential to avoid rate limits)
BATCH_DELAY = 30  # Delay between requests in seconds (SAFE MODE: 2 req/min = 120/hour)
REQUEST_TIMEOUT = 60  # Timeout per request in seconds

# Simple math problem
MATH_PROBLEM = "What is 127 + 389? Please provide only the numerical answer."
EXPECTED_ANSWER = "516"

# Output files
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
RESULTS_JSON = f"performance_results_{TIMESTAMP}.json"
RESULTS_CSV = f"performance_results_{TIMESTAMP}.csv"
SUMMARY_FILE = f"performance_summary_{TIMESTAMP}.txt"


async def fetch_all_models(client: httpx.AsyncClient) -> List[Dict]:
    """Fetch all available models from the API"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = await client.get(
            f"{API_BASE_URL}/v1/models",
            headers=headers,
            timeout=30.0,
        )

        if response.status_code == 200:
            data = response.json()
            models = data.get("data", [])
            return [model.get("id") for model in models if model.get("id")]
        else:
            print(f"❌ Failed to fetch models: HTTP {response.status_code}")
            return []

    except Exception as e:
        print(f"❌ Error fetching models: {e}")
        return []


async def test_model(client: httpx.AsyncClient, model: str, index: int, total: int) -> Dict:
    """Test a single model and measure response time"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": MATH_PROBLEM}
        ],
        "max_tokens": 50,
        "temperature": 0.1,
    }

    start_time = time.time()

    try:
        response = await client.post(
            f"{API_BASE_URL}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        end_time = time.time()
        response_time = (end_time - start_time) * 1000  # Convert to milliseconds

        if response.status_code == 200:
            data = response.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "N/A")
            tokens = data.get("usage", {})

            # Check if answer is correct
            answer_stripped = answer.strip()
            is_correct = EXPECTED_ANSWER in answer_stripped

            result = {
                "index": index,
                "model": model,
                "status": "success",
                "response_time_ms": round(response_time, 2),
                "answer": answer_stripped[:50],  # Limit length
                "is_correct": is_correct,
                "tokens_total": tokens.get("total_tokens", 0),
                "tokens_prompt": tokens.get("prompt_tokens", 0),
                "tokens_completion": tokens.get("completion_tokens", 0),
                "provider": model.split("/")[0] if "/" in model else "unknown",
                "timestamp": datetime.now().isoformat(),
            }

            status_icon = "✓" if is_correct else "⚠"
            print(f"  [{index}/{total}] {status_icon} {model}: {response_time:.0f}ms - {answer_stripped[:20]}")

            return result
        else:
            end_time = time.time()
            response_time = (end_time - start_time) * 1000
            error_detail = response.text[:100] if response.text else "Unknown error"

            result = {
                "index": index,
                "model": model,
                "status": "error",
                "response_time_ms": round(response_time, 2),
                "error": f"HTTP {response.status_code}: {error_detail}",
                "provider": model.split("/")[0] if "/" in model else "unknown",
                "timestamp": datetime.now().isoformat(),
            }

            print(f"  [{index}/{total}] ✗ {model}: Error {response.status_code}")

            return result

    except asyncio.TimeoutError:
        end_time = time.time()
        response_time = (end_time - start_time) * 1000

        result = {
            "index": index,
            "model": model,
            "status": "timeout",
            "response_time_ms": round(response_time, 2),
            "error": f"Request timed out after {REQUEST_TIMEOUT}s",
            "provider": model.split("/")[0] if "/" in model else "unknown",
            "timestamp": datetime.now().isoformat(),
        }

        print(f"  [{index}/{total}] ⏱ {model}: Timeout")

        return result

    except Exception as e:
        end_time = time.time()
        response_time = (end_time - start_time) * 1000

        result = {
            "index": index,
            "model": model,
            "status": "error",
            "response_time_ms": round(response_time, 2),
            "error": str(e)[:100],
            "provider": model.split("/")[0] if "/" in model else "unknown",
            "timestamp": datetime.now().isoformat(),
        }

        print(f"  [{index}/{total}] ✗ {model}: {str(e)[:30]}")

        return result


def save_results(results: List[Dict], is_final: bool = False):
    """Save results to JSON and CSV files"""
    # Save JSON
    with open(RESULTS_JSON, 'w') as f:
        json.dump(results, f, indent=2)

    # Save CSV
    if results:
        fieldnames = list(results[0].keys())
        with open(RESULTS_CSV, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    if is_final:
        print(f"\n💾 Results saved to:")
        print(f"   - {RESULTS_JSON}")
        print(f"   - {RESULTS_CSV}")


def generate_summary(results: List[Dict], total_time: float):
    """Generate and save comprehensive summary"""

    successful = [r for r in results if r["status"] == "success"]
    correct_answers = [r for r in results if r.get("is_correct", False)]
    errors = [r for r in results if r["status"] == "error"]
    timeouts = [r for r in results if r["status"] == "timeout"]

    # Provider breakdown
    providers = {}
    for r in results:
        provider = r.get("provider", "unknown")
        if provider not in providers:
            providers[provider] = {"total": 0, "success": 0, "error": 0, "timeout": 0}
        providers[provider]["total"] += 1
        providers[provider][r["status"]] += 1

    # Response time statistics
    response_times = [r["response_time_ms"] for r in successful]

    summary = []
    summary.append("=" * 80)
    summary.append("GATEWAYZ COMPREHENSIVE PERFORMANCE TEST - FINAL REPORT")
    summary.append("=" * 80)
    summary.append(f"Test Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    summary.append(f"Total Test Duration: {total_time:.2f} seconds ({total_time/60:.2f} minutes)")
    summary.append(f"API Endpoint: {API_BASE_URL}")
    summary.append(f"Math Problem: {MATH_PROBLEM}")
    summary.append(f"Expected Answer: {EXPECTED_ANSWER}")
    summary.append("")

    summary.append("=" * 80)
    summary.append("OVERALL STATISTICS")
    summary.append("=" * 80)
    summary.append(f"Total Models Tested: {len(results)}")
    summary.append(f"Successful Requests: {len(successful)} ({len(successful)/len(results)*100:.1f}%)")
    summary.append(f"Correct Answers: {len(correct_answers)} ({len(correct_answers)/len(results)*100:.1f}%)")
    summary.append(f"Failed Requests: {len(errors)} ({len(errors)/len(results)*100:.1f}%)")
    summary.append(f"Timeouts: {len(timeouts)} ({len(timeouts)/len(results)*100:.1f}%)")
    summary.append("")

    if response_times:
        summary.append("=" * 80)
        summary.append("RESPONSE TIME STATISTICS (Successful Requests Only)")
        summary.append("=" * 80)
        summary.append(f"Fastest Response: {min(response_times):.2f}ms")
        summary.append(f"Slowest Response: {max(response_times):.2f}ms")
        summary.append(f"Average Response: {statistics.mean(response_times):.2f}ms")
        summary.append(f"Median Response: {statistics.median(response_times):.2f}ms")
        if len(response_times) > 1:
            summary.append(f"Std Deviation: {statistics.stdev(response_times):.2f}ms")
        summary.append("")

        # Top 10 fastest models
        fastest = sorted(successful, key=lambda x: x["response_time_ms"])[:10]
        summary.append("TOP 10 FASTEST MODELS:")
        summary.append("-" * 80)
        for i, r in enumerate(fastest, 1):
            summary.append(f"{i:2d}. {r['model']:<50} {r['response_time_ms']:>8.2f}ms")
        summary.append("")

        # Top 10 slowest models
        slowest = sorted(successful, key=lambda x: x["response_time_ms"], reverse=True)[:10]
        summary.append("TOP 10 SLOWEST MODELS:")
        summary.append("-" * 80)
        for i, r in enumerate(slowest, 1):
            summary.append(f"{i:2d}. {r['model']:<50} {r['response_time_ms']:>8.2f}ms")
        summary.append("")

    summary.append("=" * 80)
    summary.append("BREAKDOWN BY PROVIDER")
    summary.append("=" * 80)
    summary.append(f"{'Provider':<20} {'Total':<8} {'Success':<10} {'Error':<8} {'Timeout':<8} {'Success %':<10}")
    summary.append("-" * 80)

    for provider, stats in sorted(providers.items(), key=lambda x: x[1]["total"], reverse=True):
        success_pct = stats["success"] / stats["total"] * 100 if stats["total"] > 0 else 0
        summary.append(
            f"{provider:<20} {stats['total']:<8} {stats['success']:<10} "
            f"{stats['error']:<8} {stats['timeout']:<8} {success_pct:>9.1f}%"
        )

    summary.append("")
    summary.append("=" * 80)
    summary.append("FILES GENERATED")
    summary.append("=" * 80)
    summary.append(f"- {RESULTS_JSON} (Full results in JSON format)")
    summary.append(f"- {RESULTS_CSV} (Full results in CSV format)")
    summary.append(f"- {SUMMARY_FILE} (This summary)")
    summary.append("=" * 80)

    summary_text = "\n".join(summary)

    # Save to file
    with open(SUMMARY_FILE, 'w') as f:
        f.write(summary_text)

    # Print to console
    print("\n" + summary_text)


async def run_comprehensive_test():
    """Run comprehensive performance test on all models"""
    print("=" * 80)
    print("GATEWAYZ COMPREHENSIVE PERFORMANCE TEST - SEQUENTIAL MODE")
    print("=" * 80)
    print(f"API Endpoint: {API_BASE_URL}")
    print(f"Test Mode: Sequential (1 model at a time)")
    print(f"Delay Between Requests: {BATCH_DELAY} seconds")
    print(f"Request Timeout: {REQUEST_TIMEOUT} seconds")
    print(f"Rate Limit Protection: Enabled")
    print("=" * 80)
    print()

    test_start_time = time.time()

    async with httpx.AsyncClient() as client:
        # Fetch all available models
        print("📡 Fetching available models...")
        models = await fetch_all_models(client)

        if not models:
            print("❌ No models found or failed to fetch models!")
            return

        print(f"✓ Found {len(models)} models to test")
        print()

        # Process in batches
        all_results = []
        total_models = len(models)
        successful_count = 0
        failed_count = 0

        # Estimate total time
        estimated_time = total_models * (12 + BATCH_DELAY)  # ~12s avg response + delay
        print(f"⏱️  Estimated completion time: {estimated_time/60:.1f} minutes (~{estimated_time/3600:.1f} hours)")
        print(f"📊 Target rate: 2 requests/minute (120/hour) - SAFE MODE to avoid rate limits")
        print()

        for batch_num in range(0, total_models, BATCH_SIZE):
            batch_models = models[batch_num:batch_num + BATCH_SIZE]
            batch_end = min(batch_num + BATCH_SIZE, total_models)
            batch_start_time = time.time()

            # Calculate progress
            progress_pct = (batch_num / total_models) * 100
            elapsed = time.time() - test_start_time
            if batch_num > 0:
                avg_time_per_model = elapsed / batch_num
                remaining_models = total_models - batch_num
                eta_seconds = avg_time_per_model * remaining_models
                eta_minutes = eta_seconds / 60
                print(f"📊 Progress: {batch_num}/{total_models} ({progress_pct:.1f}%) | "
                      f"✓ {successful_count} | ✗ {failed_count} | "
                      f"ETA: {eta_minutes:.1f}min")

            print(f"🔄 Testing model {batch_num + 1}/{total_models}...")

            # Test all models in current batch concurrently
            tasks = [
                test_model(client, model, batch_num + i + 1, total_models)
                for i, model in enumerate(batch_models)
            ]
            batch_results = await asyncio.gather(*tasks)

            # Add to all results
            all_results.extend(batch_results)

            # Update counters
            for r in batch_results:
                if r["status"] == "success":
                    successful_count += 1
                else:
                    failed_count += 1

            # Save intermediate results every 10 models
            if (batch_num + 1) % 10 == 0 or batch_end == total_models:
                save_results(all_results)
                print(f"  💾 Results saved (checkpoint at model {batch_num + 1})")

            print()

            # Delay before next batch (except for last batch)
            if batch_end < total_models:
                await asyncio.sleep(BATCH_DELAY)

    test_end_time = time.time()
    total_time = test_end_time - test_start_time

    # Save final results
    save_results(all_results, is_final=True)

    # Generate comprehensive summary
    generate_summary(all_results, total_time)


if __name__ == "__main__":
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  GATEWAYZ SEQUENTIAL PERFORMANCE TEST                        ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    print("⚙️  Mode: Sequential (1 model at a time)")
    print("⏱️  Estimated Duration: 3-4 hours (SAFE MODE)")
    print("🛡️  Rate Limit Protection: Enabled (30s delay between requests)")
    print("💾 Progress: Auto-saved every 10 models")
    print("🎯 Target Rate: 2 requests/minute (120/hour - well under 1000/hour limit)")
    print()
    print("Press Ctrl+C to stop at any time. Progress will be saved!")
    print()

    try:
        asyncio.run(run_comprehensive_test())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user!")
        print("💾 Partial results have been saved to disk.")
        print("You can analyze the results from the generated files.")
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        print("💾 Partial results may have been saved.")
