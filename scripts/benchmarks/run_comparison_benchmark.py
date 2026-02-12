#!/usr/bin/env python3
"""
Comparison Benchmark: Z.AI GLM-4.5-Air vs Soundsgood GLM-4.5-Air (distilled)
"""

import json
import os
import re
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
    test_passed: bool | None
    error: str | None


class BaseBenchmark:
    """Base benchmark runner."""

    # Dangerous patterns that should not be executed
    DANGEROUS_PATTERNS = [
        r"\bimport\s+os\b",
        r"\bimport\s+subprocess\b",
        r"\bimport\s+shutil\b",
        r"\bimport\s+sys\b",
        r"\b__import__\b",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\bopen\s*\(",
        r"\bos\.\w+",
        r"\bsubprocess\.\w+",
        r"\bsys\.\w+",
    ]

    def __init__(self, api_key: str, provider_name: str):
        self.api_key = api_key
        self.provider_name = provider_name
        self.results: list[BenchmarkResult] = []
        self.ssl_context = ssl.create_default_context()

    def _check_code_safety(self, code: str) -> tuple[bool, str]:
        """Check if code contains potentially dangerous patterns."""
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                return False, f"Dangerous pattern detected: {pattern}"
        return True, ""

    def _extract_code(self, response: str) -> str | None:
        """Extract code from response."""
        patterns = [
            r"```python\n(.*?)```",
            r"```\n(.*?)```",
            r"```(.*?)```",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            if matches:
                return max(matches, key=len).strip()

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
        is_safe, safety_msg = self._check_code_safety(code)
        if not is_safe:
            return False, f"Code rejected for safety: {safety_msg}"

        full_code = f"{code}\n\n{test_code}"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(full_code)
            temp_path = f.name

        try:
            env = {
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": "",
                "HOME": "/tmp",
            }

            result = subprocess.run(
                ["python3", temp_path],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                cwd="/tmp",
            )

            passed = result.returncode == 0
            output = result.stdout if passed else result.stderr
            return passed, output[:500]

        except subprocess.TimeoutExpired:
            return False, "Execution timed out (>10s)"
        except Exception as e:
            return False, f"Execution error: {str(e)}"
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


class ZAIBenchmark(BaseBenchmark):
    """Benchmark runner for Z.AI GLM-4.5-Air (standard version)."""

    API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
    MODEL_ID = "glm-4.5-air"

    def __init__(self, api_key: str):
        super().__init__(api_key, "Z.AI (Standard)")

    def _make_request(
        self,
        messages: list[dict],
        max_tokens: int = 2000,
        temperature: float = 0.7,
        enable_thinking: bool = False,
    ) -> dict:
        """Make a streaming request to Z.AI API."""
        payload = {
            "model": self.MODEL_ID,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "thinking": {"type": "enabled" if enable_thinking else "disabled"},
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

        try:
            with urllib.request.urlopen(request, timeout=120, context=self.ssl_context) as response:
                ttfb = time.perf_counter() - start_time

                buffer = ""
                for chunk in response:
                    buffer += chunk.decode("utf-8")

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

                                choices = chunk_data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta and delta["content"]:
                                        content_chunks.append(delta["content"])
                                    if "reasoning_content" in delta and delta["reasoning_content"]:
                                        reasoning_chunks.append(delta["reasoning_content"])
                                    # finish_reason in final chunk not needed for metrics

                                if "usage" in chunk_data:
                                    usage_data = chunk_data["usage"]

                            except json.JSONDecodeError:
                                pass

                total_duration = time.perf_counter() - start_time

            full_content = "".join(content_chunks)
            full_reasoning = "".join(reasoning_chunks) if reasoning_chunks else None

            return {
                "content": full_content,
                "reasoning": full_reasoning,
                "usage": usage_data,
                "ttfb_seconds": ttfb or 0.0,
                "total_duration_seconds": total_duration,
                "error": None,
            }

        except Exception as e:
            return {
                "content": "",
                "reasoning": None,
                "usage": {},
                "ttfb_seconds": ttfb or 0.0,
                "total_duration_seconds": time.perf_counter() - start_time,
                "error": str(e),
            }

    def run_test(self, test_case: dict, category: str) -> BenchmarkResult:
        """Run a single test case."""
        test_id = test_case["id"]
        difficulty = test_case.get("difficulty", "medium")
        prompt = test_case["prompt"]

        print(f"  [{self.provider_name}] Running {test_id} ({difficulty})...", end=" ", flush=True)

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

        content = result["content"]
        reasoning = result["reasoning"]
        usage = result["usage"]

        test_passed = None
        test_code = test_case.get("test_code")
        if test_code and category == "code_generation":
            code = self._extract_code(content)
            if code:
                test_passed, _ = self._run_code_test(code, test_code)

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
            time.sleep(0.5)

        return results


class SoundsgoodBenchmark(BaseBenchmark):
    """Benchmark runner for Soundsgood GLM-4.5-Air (distilled)."""

    API_URL = "https://soundsgood.one/v1/chat/completions"
    MODEL_ID = "zai-org/GLM-4.5-Air"

    def __init__(self, api_key: str):
        super().__init__(api_key, "Soundsgood (Distilled)")

    def _make_request(
        self,
        messages: list[dict],
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> dict:
        """Make a streaming request to Soundsgood API."""
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

        try:
            with urllib.request.urlopen(request, timeout=120, context=self.ssl_context) as response:
                ttfb = time.perf_counter() - start_time

                buffer = ""
                for chunk in response:
                    buffer += chunk.decode("utf-8")

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

                                choices = chunk_data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta and delta["content"]:
                                        content_chunks.append(delta["content"])
                                    if "reasoning" in delta and delta["reasoning"]:
                                        reasoning_chunks.append(delta["reasoning"])
                                    # finish_reason in final chunk not needed for metrics

                                if "usage" in chunk_data:
                                    usage_data = chunk_data["usage"]

                            except json.JSONDecodeError:
                                pass

                total_duration = time.perf_counter() - start_time

            full_content = "".join(content_chunks)
            full_reasoning = "".join(reasoning_chunks) if reasoning_chunks else None

            return {
                "content": full_content,
                "reasoning": full_reasoning,
                "usage": usage_data,
                "ttfb_seconds": ttfb or 0.0,
                "total_duration_seconds": total_duration,
                "error": None,
            }

        except Exception as e:
            return {
                "content": "",
                "reasoning": None,
                "usage": {},
                "ttfb_seconds": ttfb or 0.0,
                "total_duration_seconds": time.perf_counter() - start_time,
                "error": str(e),
            }

    def run_test(self, test_case: dict, category: str) -> BenchmarkResult:
        """Run a single test case."""
        test_id = test_case["id"]
        difficulty = test_case.get("difficulty", "medium")
        prompt = test_case["prompt"]

        print(f"  [{self.provider_name}] Running {test_id} ({difficulty})...", end=" ", flush=True)

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

        content = result["content"]
        reasoning = result["reasoning"]
        usage = result["usage"]

        test_passed = None
        test_code = test_case.get("test_code")
        if test_code and category == "code_generation":
            code = self._extract_code(content)
            if code:
                test_passed, _ = self._run_code_test(code, test_code)

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
            time.sleep(0.5)

        return results


def print_summary(benchmark: BaseBenchmark):
    """Print benchmark summary."""
    results = benchmark.results
    if not results:
        print(f"\n[{benchmark.provider_name}] No results to summarize.")
        return

    print(f"\n{'=' * 70}")
    print(f"{benchmark.provider_name} SUMMARY")
    print("=" * 70)

    successful = [r for r in results if not r.error]
    failed = [r for r in results if r.error]

    print(f"\nTotal tests: {len(results)}")
    print(f"Successful:  {len(successful)}")
    print(f"Failed:      {len(failed)}")

    if not successful:
        print("\nNo successful tests to analyze.")
        return

    ttfb_values = [r.ttfb_seconds for r in successful]
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

    total_input = sum(r.input_tokens for r in successful)
    total_output = sum(r.output_tokens for r in successful)
    total_cost = sum(r.cost_usd for r in successful)

    print("\nTOKEN USAGE:")
    print(f"  Total Input:  {total_input:,}")
    print(f"  Total Output: {total_output:,}")
    print(f"  Total Cost:   ${total_cost:.4f}")

    # Code generation pass rate
    code_gen = [r for r in successful if r.category == "code_generation" and r.test_passed is not None]
    if code_gen:
        passed = sum(1 for r in code_gen if r.test_passed)
        print("\nCODE GENERATION:")
        print(f"  Pass Rate: {passed}/{len(code_gen)} ({100*passed/len(code_gen):.1f}%)")


def print_comparison(zai_benchmark: ZAIBenchmark, sg_benchmark: SoundsgoodBenchmark):
    """Print side-by-side comparison."""
    print("\n" + "=" * 70)
    print("COMPARISON: Z.AI (Standard) vs Soundsgood (Distilled)")
    print("=" * 70)

    zai_results = [r for r in zai_benchmark.results if not r.error]
    sg_results = [r for r in sg_benchmark.results if not r.error]

    if not zai_results or not sg_results:
        print("Insufficient data for comparison.")
        return

    # TTFB comparison
    zai_ttfb = statistics.mean([r.ttfb_seconds for r in zai_results]) * 1000
    sg_ttfb = statistics.mean([r.ttfb_seconds for r in sg_results]) * 1000

    print(f"\n{'Metric':<25} {'Z.AI (Standard)':<20} {'Soundsgood (Distilled)':<20} {'Winner':<15}")
    print("-" * 80)

    # TTFB
    winner = "Z.AI" if zai_ttfb < sg_ttfb else "Soundsgood"
    print(f"{'TTFB Mean (ms)':<25} {zai_ttfb:<20.0f} {sg_ttfb:<20.0f} {winner:<15}")

    # TPS
    zai_tps = statistics.mean([r.output_tokens / r.total_duration_seconds for r in zai_results if r.total_duration_seconds > 0])
    sg_tps = statistics.mean([r.output_tokens / r.total_duration_seconds for r in sg_results if r.total_duration_seconds > 0])
    winner = "Z.AI" if zai_tps > sg_tps else "Soundsgood"
    print(f"{'TPS Mean':<25} {zai_tps:<20.1f} {sg_tps:<20.1f} {winner:<15}")

    # Code generation pass rate
    zai_cg = [r for r in zai_results if r.category == "code_generation" and r.test_passed is not None]
    sg_cg = [r for r in sg_results if r.category == "code_generation" and r.test_passed is not None]

    if zai_cg and sg_cg:
        zai_pass = sum(1 for r in zai_cg if r.test_passed) / len(zai_cg) * 100
        sg_pass = sum(1 for r in sg_cg if r.test_passed) / len(sg_cg) * 100
        winner = "Z.AI" if zai_pass > sg_pass else "Soundsgood" if sg_pass > zai_pass else "Tie"
        print(f"{'Code Gen Pass Rate (%)':<25} {zai_pass:<20.1f} {sg_pass:<20.1f} {winner:<15}")

    # Cost (if available)
    zai_cost = sum(r.cost_usd for r in zai_results)
    sg_cost = sum(r.cost_usd for r in sg_results)
    if zai_cost > 0 or sg_cost > 0:
        winner = "Z.AI" if zai_cost < sg_cost else "Soundsgood" if sg_cost < zai_cost else "Tie"
        print(f"{'Total Cost ($)':<25} {zai_cost:<20.4f} {sg_cost:<20.4f} {winner:<15}")

    print("-" * 80)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="GLM-4.5-Air Comparison Benchmark")
    parser.add_argument("--tests-per-category", type=int, default=5,
                        help="Number of tests per category (default: 5)")
    parser.add_argument("--categories", type=str, default="code_generation",
                        help="Comma-separated categories to test")
    parser.add_argument("--output-dir", type=str, default="benchmark_results",
                        help="Output directory for results")

    args = parser.parse_args()

    zai_api_key = os.environ.get("ZAI_API_KEY")
    sg_api_key = os.environ.get("SOUNDSGOOD_API_KEY")

    if not zai_api_key:
        print("ERROR: ZAI_API_KEY environment variable not set")
        return 1
    if not sg_api_key:
        print("ERROR: SOUNDSGOOD_API_KEY environment variable not set")
        return 1

    print("=" * 70)
    print("GLM-4.5-Air Comparison Benchmark")
    print("Z.AI (Standard) vs Soundsgood (Distilled)")
    print(f"Tests per category: {args.tests_per_category}")
    print("=" * 70)

    zai_benchmark = ZAIBenchmark(zai_api_key)
    sg_benchmark = SoundsgoodBenchmark(sg_api_key)

    categories = [c.strip() for c in args.categories.split(",")]

    for category in categories:
        print(f"\n[{category.upper()}]")

        # Run Z.AI benchmark
        print("\n--- Z.AI (Standard) ---")
        zai_benchmark.run_category(category, max_tests=args.tests_per_category)

        # Run Soundsgood benchmark
        print("\n--- Soundsgood (Distilled) ---")
        sg_benchmark.run_category(category, max_tests=args.tests_per_category)

    # Print individual summaries
    print_summary(zai_benchmark)
    print_summary(sg_benchmark)

    # Print comparison
    print_comparison(zai_benchmark, sg_benchmark)

    # Save results
    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = output_path / f"comparison_benchmark_{timestamp}.json"

    comparison_data = {
        "timestamp": timestamp,
        "tests_per_category": args.tests_per_category,
        "categories": categories,
        "zai_results": [
            {
                "test_id": r.test_id,
                "category": r.category,
                "difficulty": r.difficulty,
                "ttfb_seconds": r.ttfb_seconds,
                "total_duration_seconds": r.total_duration_seconds,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": r.cost_usd,
                "test_passed": r.test_passed,
                "error": r.error,
            }
            for r in zai_benchmark.results
        ],
        "soundsgood_results": [
            {
                "test_id": r.test_id,
                "category": r.category,
                "difficulty": r.difficulty,
                "ttfb_seconds": r.ttfb_seconds,
                "total_duration_seconds": r.total_duration_seconds,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": r.cost_usd,
                "test_passed": r.test_passed,
                "error": r.error,
            }
            for r in sg_benchmark.results
        ],
    }

    with open(filename, "w") as f:
        json.dump(comparison_data, f, indent=2)

    print(f"\nResults saved to: {filename}")
    return 0


if __name__ == "__main__":
    exit(main())
