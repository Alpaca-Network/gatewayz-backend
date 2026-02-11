#!/usr/bin/env python3
"""
Simple GLM-4.5-Air Benchmark Runner
Uses only standard library modules for compatibility.
"""

import json
import os
import ssl
import statistics
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class BenchmarkResult:
    """Result of a single benchmark test."""
    test_id: str
    category: str
    difficulty: str
    prompt: str
    response_content: str
    reasoning_content: str | None
    ttfb_seconds: float
    total_duration_seconds: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float
    test_passed: bool | None  # None if no test code
    error: str | None


class SoundsgoodBenchmark:
    """Simple benchmark runner for Soundsgood GLM-4.5-Air."""

    API_URL = "https://soundsgood.one/v1/chat/completions"
    MODEL_ID = "zai-org/GLM-4.5-Air"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.results: list[BenchmarkResult] = []
        self.ssl_context = ssl.create_default_context()

    def _make_request(
        self,
        messages: list[dict],
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> dict:
        """Make a streaming request to the API (required for this provider)."""
        payload = {
            "model": self.MODEL_ID,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        request = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        start_time = time.perf_counter()
        ttfb = None
        content_chunks = []
        reasoning_chunks = []
        usage_data = {}
        finish_reason = None

        try:
            with urllib.request.urlopen(request, timeout=120, context=self.ssl_context) as response:
                ttfb = time.perf_counter() - start_time

                # Read streaming response
                buffer = ""
                for chunk in response:
                    if ttfb is None:
                        ttfb = time.perf_counter() - start_time

                    buffer += chunk.decode("utf-8")

                    # Process complete lines
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()

                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                continue

                            try:
                                chunk_data = json.loads(data_str)

                                # Extract content/reasoning from delta
                                choices = chunk_data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta and delta["content"]:
                                        content_chunks.append(delta["content"])
                                    if "reasoning" in delta and delta["reasoning"]:
                                        reasoning_chunks.append(delta["reasoning"])
                                    if choices[0].get("finish_reason"):
                                        finish_reason = choices[0]["finish_reason"]

                                # Extract usage from final chunk
                                if "usage" in chunk_data:
                                    usage_data = chunk_data["usage"]

                            except json.JSONDecodeError:
                                pass

                total_duration = time.perf_counter() - start_time

            # Build response data structure
            full_content = "".join(content_chunks)
            full_reasoning = "".join(reasoning_chunks) if reasoning_chunks else None

            data = {
                "choices": [{
                    "message": {
                        "content": full_content,
                        "reasoning": full_reasoning,
                    },
                    "finish_reason": finish_reason or "stop",
                }],
                "usage": usage_data,
            }

            return {
                "data": data,
                "ttfb_seconds": ttfb or 0.0,
                "total_duration_seconds": total_duration,
                "error": None,
            }

        except Exception as e:
            return {
                "data": None,
                "ttfb_seconds": ttfb or 0.0,
                "total_duration_seconds": time.perf_counter() - start_time,
                "error": str(e),
            }

    def _parse_response(self, result: dict) -> tuple[str, str | None, dict]:
        """Parse response content and reasoning."""
        if result["error"] or not result["data"]:
            return "", None, {}

        data = result["data"]
        choices = data.get("choices", [])
        if not choices:
            return "", None, {}

        message = choices[0].get("message", {})
        content = message.get("content", "")
        reasoning = message.get("reasoning")
        usage = data.get("usage", {})

        return content, reasoning, usage

    def _extract_code(self, response: str) -> str | None:
        """Extract code from response."""
        import re

        patterns = [
            r"```python\n(.*?)```",
            r"```\n(.*?)```",
            r"```(.*?)```",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            if matches:
                return max(matches, key=len).strip()

        # Try to find function definition
        func_match = re.search(
            r"(def \w+\(.*?\):.*?)(?=\n\n|\nclass |\ndef |\Z)",
            response,
            re.DOTALL,
        )
        if func_match:
            return func_match.group(1).strip()

        return None

    def _run_code_test(self, code: str, test_code: str) -> tuple[bool, str]:
        """Run code with test cases."""
        full_code = f"{code}\n\n{test_code}"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(full_code)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["python3", temp_path],
                capture_output=True,
                text=True,
                timeout=10,
            )

            passed = result.returncode == 0
            output = result.stdout if passed else result.stderr
            return passed, output[:500]

        except subprocess.TimeoutExpired:
            return False, "Execution timed out (>10s)"
        except Exception as e:
            return False, f"Execution error: {str(e)}"
        finally:
            os.unlink(temp_path)

    def run_test(self, test_case: dict, category: str) -> BenchmarkResult:
        """Run a single test case."""
        test_id = test_case["id"]
        difficulty = test_case.get("difficulty", "medium")
        prompt = test_case["prompt"]

        print(f"  Running {test_id} ({difficulty})...", end=" ", flush=True)

        # Make API request
        messages = [{"role": "user", "content": prompt}]
        result = self._make_request(messages)

        if result["error"]:
            print(f"ERROR: {result['error'][:50]}")
            return BenchmarkResult(
                test_id=test_id,
                category=category,
                difficulty=difficulty,
                prompt=prompt,
                response_content="",
                reasoning_content=None,
                ttfb_seconds=0.0,
                total_duration_seconds=result["total_duration_seconds"],
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cost_usd=0.0,
                test_passed=None,
                error=result["error"],
            )

        content, reasoning, usage = self._parse_response(result)

        # Check if we can run tests
        test_passed = None
        test_code = test_case.get("test_code")
        if test_code and category == "code_generation":
            code = self._extract_code(content)
            if code:
                test_passed, test_output = self._run_code_test(code, test_code)

        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        reasoning_tokens = usage.get("reasoning_tokens", 0)
        cost_usd = usage.get("cost_usd_total", 0.0)

        status = "PASS" if test_passed else ("FAIL" if test_passed is False else "N/A")
        tps = output_tokens / result["total_duration_seconds"] if result["total_duration_seconds"] > 0 else 0
        print(f"{status} | ttfb={result['ttfb_seconds']*1000:.0f}ms | tps={tps:.1f} | tokens={output_tokens}")

        return BenchmarkResult(
            test_id=test_id,
            category=category,
            difficulty=difficulty,
            prompt=prompt,
            response_content=content,
            reasoning_content=reasoning,
            ttfb_seconds=result["ttfb_seconds"],
            total_duration_seconds=result["total_duration_seconds"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_usd=cost_usd,
            test_passed=test_passed,
            error=None,
        )

    def run_category(self, category: str, max_tests: int = 5) -> list[BenchmarkResult]:
        """Run tests for a category."""
        prompts_dir = Path(__file__).parent / "test_prompts"
        file_path = prompts_dir / f"{category}.json"

        if not file_path.exists():
            print(f"  No test file for {category}")
            return []

        with open(file_path) as f:
            data = json.load(f)

        test_cases = data.get("test_cases", [])[:max_tests]
        results = []

        for tc in test_cases:
            result = self.run_test(tc, category)
            results.append(result)
            self.results.append(result)
            time.sleep(0.5)  # Small delay between requests

        return results

    def run_warmup(self):
        """Run warmup requests."""
        print("Running warmup...")
        messages = [{"role": "user", "content": "Write a Python function to add two numbers."}]
        result = self._make_request(messages, max_tokens=100)
        if result["error"]:
            print(f"  Warmup failed: {result['error']}")
        else:
            print(f"  Warmup complete: ttfb={result['ttfb_seconds']*1000:.0f}ms")

    def print_summary(self):
        """Print benchmark summary."""
        if not self.results:
            print("No results to summarize.")
            return

        print("\n" + "=" * 70)
        print("BENCHMARK SUMMARY")
        print("=" * 70)

        # Overall stats
        successful = [r for r in self.results if not r.error]
        failed = [r for r in self.results if r.error]

        print(f"\nTotal tests: {len(self.results)}")
        print(f"Successful:  {len(successful)}")
        print(f"Failed:      {len(failed)}")

        if not successful:
            print("\nNo successful tests to analyze.")
            return

        # Latency stats
        ttfb_values = [r.ttfb_seconds for r in successful]
        duration_values = [r.total_duration_seconds for r in successful]
        tps_values = [
            r.output_tokens / r.total_duration_seconds
            for r in successful
            if r.total_duration_seconds > 0
        ]

        print("\nLATENCY METRICS:")
        print(f"  TTFB Mean:   {statistics.mean(ttfb_values)*1000:.0f} ms")
        print(f"  TTFB Median: {statistics.median(ttfb_values)*1000:.0f} ms")
        if len(ttfb_values) >= 2:
            sorted_ttfb = sorted(ttfb_values)
            p95_idx = int(len(sorted_ttfb) * 0.95)
            print(f"  TTFB P95:    {sorted_ttfb[min(p95_idx, len(sorted_ttfb)-1)]*1000:.0f} ms")

        if tps_values:
            print(f"\n  TPS Mean:    {statistics.mean(tps_values):.1f} tokens/sec")
            print(f"  TPS Median:  {statistics.median(tps_values):.1f} tokens/sec")

        # Token stats
        total_input = sum(r.input_tokens for r in successful)
        total_output = sum(r.output_tokens for r in successful)
        total_reasoning = sum(r.reasoning_tokens for r in successful)
        total_cost = sum(r.cost_usd for r in successful)

        print("\nTOKEN USAGE:")
        print(f"  Total Input:     {total_input:,}")
        print(f"  Total Output:    {total_output:,}")
        print(f"  Total Reasoning: {total_reasoning:,}")
        print(f"  Total Cost:      ${total_cost:.4f}")

        # Category breakdown
        categories = set(r.category for r in successful)
        print("\nCATEGORY BREAKDOWN:")
        for cat in sorted(categories):
            cat_results = [r for r in successful if r.category == cat]
            cat_ttfb = statistics.mean([r.ttfb_seconds for r in cat_results])

            # Count test passes for code_generation
            if cat == "code_generation":
                passed = sum(1 for r in cat_results if r.test_passed is True)
                total = sum(1 for r in cat_results if r.test_passed is not None)
                pass_rate = f" | pass={passed}/{total}" if total > 0 else ""
            else:
                pass_rate = ""

            print(f"  {cat:20s}: {len(cat_results)} tests | ttfb={cat_ttfb*1000:.0f}ms{pass_rate}")

        print("\n" + "=" * 70)

    def save_results(self, output_dir: str = "benchmark_results"):
        """Save results to JSON file."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_path / f"glm45air_benchmark_{timestamp}.json"

        results_data = [
            {
                "test_id": r.test_id,
                "category": r.category,
                "difficulty": r.difficulty,
                "ttfb_seconds": r.ttfb_seconds,
                "total_duration_seconds": r.total_duration_seconds,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "reasoning_tokens": r.reasoning_tokens,
                "cost_usd": r.cost_usd,
                "test_passed": r.test_passed,
                "error": r.error,
                "response_length": len(r.response_content),
                "has_reasoning": r.reasoning_content is not None,
            }
            for r in self.results
        ]

        with open(filename, "w") as f:
            json.dump({
                "model": self.MODEL_ID,
                "timestamp": timestamp,
                "total_tests": len(self.results),
                "results": results_data,
            }, f, indent=2)

        print(f"\nResults saved to: {filename}")
        return filename


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="GLM-4.5-Air Benchmark")
    parser.add_argument("--tests-per-category", type=int, default=3,
                        help="Number of tests per category (default: 3)")
    parser.add_argument("--categories", type=str, default="code_generation,reasoning,debugging,refactoring",
                        help="Comma-separated categories to test")
    parser.add_argument("--output-dir", type=str, default="benchmark_results",
                        help="Output directory for results")
    parser.add_argument("--skip-warmup", action="store_true",
                        help="Skip warmup request")

    args = parser.parse_args()

    api_key = os.environ.get("SOUNDSGOOD_API_KEY")
    if not api_key:
        print("ERROR: SOUNDSGOOD_API_KEY environment variable not set")
        return 1

    benchmark = SoundsgoodBenchmark(api_key)

    print("=" * 70)
    print("GLM-4.5-Air Code Router Benchmark")
    print(f"Model: {SoundsgoodBenchmark.MODEL_ID}")
    print(f"Tests per category: {args.tests_per_category}")
    print("=" * 70)

    # Warmup
    if not args.skip_warmup:
        benchmark.run_warmup()
        print()

    # Run tests by category
    categories = [c.strip() for c in args.categories.split(",")]
    for category in categories:
        print(f"\n[{category.upper()}]")
        benchmark.run_category(category, max_tests=args.tests_per_category)

    # Print summary
    benchmark.print_summary()

    # Save results
    benchmark.save_results(args.output_dir)

    return 0


if __name__ == "__main__":
    exit(main())
