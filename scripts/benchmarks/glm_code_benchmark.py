#!/usr/bin/env python3
"""
GLM-4.5-Air Code Router Benchmark

Main orchestrator for benchmarking the distilled GLM-4.5-Air model
for code router integration.

Usage:
    python glm_code_benchmark.py [--streaming] [--iterations N] [--output-dir DIR]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from benchmark_config import (
    SOUNDSGOOD_GLM_45_AIR,
    BenchmarkConfig,
    BenchmarkSummary,
    CategoryResult,
    Difficulty,
    TestCase,
    TestCategory,
    TestResult,
)
from latency_tracker import (
    CategoryLatencyTracker,
    format_latency_report,
)
from quality_evaluator import QualityEvaluator
from soundsgood_client import SoundsgoodClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class BenchmarkOrchestrator:
    """Orchestrates the benchmark execution."""

    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.run_id = config.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = Path(config.output_dir) / self.run_id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.results: list[TestResult] = []
        self.category_tracker = CategoryLatencyTracker()
        self.total_cost = 0.0

    async def run(self, use_streaming: bool = True) -> BenchmarkSummary:
        """
        Run the complete benchmark suite.

        Args:
            use_streaming: Whether to use streaming for latency measurement

        Returns:
            BenchmarkSummary with aggregated results
        """
        start_time = datetime.now()
        logger.info(f"Starting benchmark run: {self.run_id}")
        logger.info(f"Model: {self.config.model.model_id}")
        logger.info(f"Streaming: {use_streaming}")

        # Load test cases
        test_cases = self._load_test_cases()
        logger.info(f"Loaded {len(test_cases)} test cases")

        # Initialize clients
        async with SoundsgoodClient(self.config.model) as model_client:
            async with QualityEvaluator(
                judge_model=self.config.judge_model,
                judge_api_key=os.environ.get(self.config.judge_api_key_env),
            ) as evaluator:
                # Run warmup
                await self._run_warmup(model_client, use_streaming)

                # Run tests by category
                for category in list(TestCategory):
                    category_cases = [
                        tc for tc in test_cases if tc.category == category
                    ]
                    if category_cases:
                        await self._run_category(
                            category,
                            category_cases,
                            model_client,
                            evaluator,
                            use_streaming,
                        )

        end_time = datetime.now()

        # Generate summary
        summary = self._generate_summary(start_time, end_time)

        # Save results
        self._save_results(summary)

        return summary

    def _load_test_cases(self) -> list[TestCase]:
        """Load test cases from JSON files."""
        test_cases = []
        prompts_dir = Path(__file__).parent / "test_prompts"

        for category in list(TestCategory):
            file_path = prompts_dir / f"{category.value}.json"
            if file_path.exists():
                with open(file_path) as f:
                    data = json.load(f)

                for tc_data in data.get("test_cases", []):
                    test_cases.append(
                        TestCase(
                            id=tc_data["id"],
                            category=category,
                            difficulty=Difficulty(tc_data.get("difficulty", "medium")),
                            prompt=tc_data["prompt"],
                            expected_behavior=tc_data.get("expected_behavior", ""),
                            evaluation_criteria=tc_data.get("evaluation_criteria", []),
                            test_code=tc_data.get("test_code"),
                            reference_answer=tc_data.get("reference_answer"),
                            tags=tc_data.get("tags", []),
                        )
                    )
                logger.info(f"Loaded {len(data.get('test_cases', []))} {category.value} tests")

        return test_cases

    async def _run_warmup(
        self, client: SoundsgoodClient, use_streaming: bool
    ) -> None:
        """Run warmup iterations to stabilize latency measurements."""
        logger.info(f"Running {self.config.warmup_iterations} warmup iterations...")

        warmup_prompts = [
            "Write a Python function to add two numbers.",
            "Explain what a for loop does in Python.",
        ]

        for i in range(self.config.warmup_iterations):
            prompt = warmup_prompts[i % len(warmup_prompts)]
            try:
                await client.complete(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100,
                    stream=use_streaming,
                )
                logger.debug(f"Warmup {i + 1} complete")
            except Exception as e:
                logger.warning(f"Warmup {i + 1} failed: {e}")

        logger.info("Warmup complete")

    async def _run_category(
        self,
        category: TestCategory,
        test_cases: list[TestCase],
        client: SoundsgoodClient,
        evaluator: QualityEvaluator,
        use_streaming: bool,
    ) -> None:
        """Run all tests in a category."""
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Running {category.value} tests ({len(test_cases)} cases)")
        logger.info("=" * 60)

        for tc in test_cases:
            for iteration in range(self.config.iterations_per_test):
                result = await self._run_single_test(
                    tc, iteration, client, evaluator, use_streaming
                )
                self.results.append(result)

                # Track latency and cost for all completed API calls
                # (not just quality-passed ones) to get accurate performance metrics
                if result.ttfb_seconds > 0:  # API call completed successfully
                    self.category_tracker.record(
                        category.value,
                        ttfb_seconds=result.ttfb_seconds,
                        total_duration_seconds=result.total_duration_seconds,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        reasoning_tokens=result.reasoning_tokens,
                        ttfc_seconds=result.ttfc_seconds,
                    )
                    self.total_cost += result.cost_usd

                # Log progress
                status = "PASS" if result.success else "FAIL"
                logger.info(
                    f"  [{tc.id}] iter={iteration + 1} "
                    f"score={result.quality_score:.1f} "
                    f"ttfb={result.ttfb_seconds * 1000:.0f}ms "
                    f"status={status}"
                )

    async def _run_single_test(
        self,
        test_case: TestCase,
        iteration: int,
        client: SoundsgoodClient,
        evaluator: QualityEvaluator,
        use_streaming: bool,
    ) -> TestResult:
        """Run a single test case."""
        try:
            # Make request
            response = await client.complete(
                messages=[{"role": "user", "content": test_case.prompt}],
                max_tokens=2000,
                temperature=0.7,
                stream=use_streaming,
            )

            # Evaluate quality
            quality = await evaluator.evaluate(
                test_case,
                response.content,
                response.reasoning,
            )

            return TestResult(
                test_case=test_case,
                iteration=iteration,
                response_content=response.content,
                reasoning_content=response.reasoning,
                raw_response=response.raw_response,
                ttfb_seconds=response.ttfb_seconds,
                ttfc_seconds=getattr(response, "ttfc_seconds", None),
                total_duration_seconds=response.total_duration_seconds,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                reasoning_tokens=response.reasoning_tokens,
                quality_score=quality.overall_score,
                quality_breakdown=quality.criteria_scores,
                quality_feedback=quality.feedback,
                cost_usd=response.cost_usd_total,
                success=quality.passed,
            )

        except Exception as e:
            logger.error(f"Test {test_case.id} failed: {e}")
            return TestResult(
                test_case=test_case,
                iteration=iteration,
                response_content="",
                reasoning_content=None,
                raw_response={},
                ttfb_seconds=0.0,
                ttfc_seconds=None,
                total_duration_seconds=0.0,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                quality_score=0.0,
                quality_breakdown={},
                quality_feedback=str(e),
                cost_usd=0.0,
                success=False,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def _generate_summary(
        self, start_time: datetime, end_time: datetime
    ) -> BenchmarkSummary:
        """Generate the benchmark summary."""
        total_duration = (end_time - start_time).total_seconds()

        # Calculate category results
        category_results: dict[TestCategory, CategoryResult] = {}
        for category in list(TestCategory):
            cat_results = [r for r in self.results if r.test_case.category == category]
            if cat_results:
                category_results[category] = self._calculate_category_result(
                    category, cat_results
                )

        # Calculate weighted score
        weighted_score = 0.0
        for category, result in category_results.items():
            weight = self.config.category_weights.get(category, 0.0)
            weighted_score += result.avg_quality_score * weight

        # Determine tier
        tier, tier_reasoning = self._determine_tier(weighted_score, category_results)

        # Get overall latency stats
        try:
            overall_latency = self.category_tracker.get_overall_stats()
            overall_ttfb_p95 = overall_latency.ttfb_p95
            overall_tps_mean = overall_latency.tps_mean
        except ValueError:
            overall_ttfb_p95 = 0.0
            overall_tps_mean = 0.0

        # Check latency targets
        meets_latency = (
            overall_ttfb_p95 <= self.config.ttfb_target_p95
            and overall_tps_mean >= self.config.tps_target
        )

        # Identify strengths and weaknesses
        strengths, weaknesses = self._analyze_performance(category_results)

        # Baseline comparison
        baseline_comparison = self._compare_to_baselines(weighted_score)

        return BenchmarkSummary(
            run_id=self.run_id,
            model_id=self.config.model.model_id,
            started_at=start_time.isoformat(),
            completed_at=end_time.isoformat(),
            total_duration_seconds=total_duration,
            weighted_quality_score=weighted_score,
            category_results=category_results,
            recommended_tier=tier,
            tier_reasoning=tier_reasoning,
            overall_ttfb_p95=overall_ttfb_p95,
            overall_tps_mean=overall_tps_mean,
            meets_latency_targets=meets_latency,
            total_cost_usd=self.total_cost,
            estimated_cost_per_1k_requests=self.total_cost / len(self.results) * 1000
            if self.results
            else 0.0,
            baseline_comparison=baseline_comparison,
            strengths=strengths,
            weaknesses=weaknesses,
        )

    def _calculate_category_result(
        self, category: TestCategory, results: list[TestResult]
    ) -> CategoryResult:
        """Calculate aggregated result for a category."""
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]

        scores = [r.quality_score for r in results]

        # Get latency stats
        try:
            latency = self.category_tracker.get_tracker(category.value).get_stats()
        except (KeyError, ValueError):
            latency = None

        return CategoryResult(
            category=category,
            test_count=len(results),
            pass_count=len(successful),
            fail_count=len(failed),
            avg_quality_score=sum(scores) / len(scores) if scores else 0.0,
            min_quality_score=min(scores) if scores else 0.0,
            max_quality_score=max(scores) if scores else 0.0,
            ttfb_mean=latency.ttfb_mean if latency else 0.0,
            ttfb_p50=latency.ttfb_p50 if latency else 0.0,
            ttfb_p95=latency.ttfb_p95 if latency else 0.0,
            ttfb_p99=latency.ttfb_p99 if latency else 0.0,
            ttfc_mean=latency.ttfc_mean if latency else None,
            ttfc_p50=latency.ttfc_p50 if latency else None,
            ttfc_p95=latency.ttfc_p95 if latency else None,
            tps_mean=latency.tps_mean if latency else 0.0,
            tps_p50=latency.tps_p50 if latency else 0.0,
            total_cost_usd=sum(r.cost_usd for r in results),
            avg_cost_per_test=sum(r.cost_usd for r in results) / len(results)
            if results
            else 0.0,
            error_rate=len(failed) / len(results) if results else 0.0,
        )

    def _determine_tier(
        self,
        weighted_score: float,
        category_results: dict[TestCategory, CategoryResult],
    ) -> tuple[int, str]:
        """Determine tier placement based on score and performance."""
        # Check tier thresholds
        for tier, threshold in sorted(self.config.tier_thresholds.items()):
            if weighted_score >= threshold:
                return tier, f"Score {weighted_score:.1f} meets Tier {tier} threshold ({threshold})"

        # Below all thresholds
        return 5, f"Score {weighted_score:.1f} below Tier 4 threshold"

    def _analyze_performance(
        self, category_results: dict[TestCategory, CategoryResult]
    ) -> tuple[list[str], list[str]]:
        """Identify strengths and weaknesses from results."""
        strengths = []
        weaknesses = []

        for category, result in category_results.items():
            if result.avg_quality_score >= 80:
                strengths.append(f"Strong {category.value}: {result.avg_quality_score:.1f}")
            elif result.avg_quality_score < 60:
                weaknesses.append(f"Weak {category.value}: {result.avg_quality_score:.1f}")

            if result.error_rate > 0.1:
                weaknesses.append(
                    f"High error rate in {category.value}: {result.error_rate * 100:.1f}%"
                )

            if result.ttfb_p95 > self.config.ttfb_target_p95:
                weaknesses.append(
                    f"High TTFB P95 in {category.value}: {result.ttfb_p95 * 1000:.0f}ms"
                )

        return strengths, weaknesses

    def _compare_to_baselines(self, score: float) -> dict[str, dict[str, float]]:
        """Compare score to baseline models."""
        from benchmark_config import BASELINE_MODELS

        comparison = {}
        for tier_name, models in BASELINE_MODELS.items():
            comparison[tier_name] = {}
            for model_name, metrics in models.items():
                # Rough approximation: our score as percentage of HumanEval
                human_eval = metrics.get("human_eval", 85.0)
                comparison[tier_name][model_name] = {
                    "their_humaneval": human_eval,
                    "our_score": score,
                    "relative": score / human_eval * 100,
                }

        return comparison

    def _save_results(self, summary: BenchmarkSummary) -> None:
        """Save all results and summary to files."""
        # Save summary
        summary_path = self.output_dir / "summary.json"
        with open(summary_path, "w") as f:
            json.dump(self._summary_to_dict(summary), f, indent=2, default=str)
        logger.info(f"Summary saved to {summary_path}")

        # Save detailed results
        results_path = self.output_dir / "detailed_results.json"
        with open(results_path, "w") as f:
            json.dump(
                [self._result_to_dict(r) for r in self.results],
                f,
                indent=2,
                default=str,
            )
        logger.info(f"Detailed results saved to {results_path}")

        # Save latency report
        try:
            latency_stats = self.category_tracker.get_overall_stats()
            latency_path = self.output_dir / "latency_report.txt"
            with open(latency_path, "w") as f:
                f.write(format_latency_report(latency_stats))
            logger.info(f"Latency report saved to {latency_path}")
        except ValueError:
            logger.warning("No latency data to save")

        # Print summary to console
        self._print_summary(summary)

    def _summary_to_dict(self, summary: BenchmarkSummary) -> dict[str, Any]:
        """Convert summary to serializable dict."""
        return {
            "run_id": summary.run_id,
            "model_id": summary.model_id,
            "started_at": summary.started_at,
            "completed_at": summary.completed_at,
            "total_duration_seconds": summary.total_duration_seconds,
            "weighted_quality_score": summary.weighted_quality_score,
            "recommended_tier": summary.recommended_tier,
            "tier_reasoning": summary.tier_reasoning,
            "overall_ttfb_p95_ms": summary.overall_ttfb_p95 * 1000,
            "overall_tps_mean": summary.overall_tps_mean,
            "meets_latency_targets": summary.meets_latency_targets,
            "total_cost_usd": summary.total_cost_usd,
            "estimated_cost_per_1k_requests": summary.estimated_cost_per_1k_requests,
            "strengths": summary.strengths,
            "weaknesses": summary.weaknesses,
            "category_results": {
                cat.value: {
                    "test_count": result.test_count,
                    "pass_count": result.pass_count,
                    "fail_count": result.fail_count,
                    "avg_quality_score": result.avg_quality_score,
                    "min_quality_score": result.min_quality_score,
                    "max_quality_score": result.max_quality_score,
                    "ttfb_mean_ms": result.ttfb_mean * 1000,
                    "ttfb_p95_ms": result.ttfb_p95 * 1000,
                    "tps_mean": result.tps_mean,
                    "total_cost_usd": result.total_cost_usd,
                    "error_rate": result.error_rate,
                }
                for cat, result in summary.category_results.items()
            },
            "baseline_comparison": summary.baseline_comparison,
        }

    def _result_to_dict(self, result: TestResult) -> dict[str, Any]:
        """Convert test result to serializable dict."""
        return {
            "test_id": result.test_case.id,
            "category": result.test_case.category.value,
            "difficulty": result.test_case.difficulty.value,
            "iteration": result.iteration,
            "quality_score": result.quality_score,
            "quality_breakdown": result.quality_breakdown,
            "quality_feedback": result.quality_feedback,
            "success": result.success,
            "ttfb_seconds": result.ttfb_seconds,
            "total_duration_seconds": result.total_duration_seconds,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "cost_usd": result.cost_usd,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "response_content": result.response_content[:1000]
            if self.config.save_raw_responses
            else None,
            "reasoning_content": result.reasoning_content[:500]
            if result.reasoning_content and self.config.save_raw_responses
            else None,
        }

    def _print_summary(self, summary: BenchmarkSummary) -> None:
        """Print formatted summary to console."""
        print("\n" + "=" * 70)
        print("BENCHMARK SUMMARY")
        print("=" * 70)
        print(f"Run ID:     {summary.run_id}")
        print(f"Model:      {summary.model_id}")
        print(f"Duration:   {summary.total_duration_seconds:.1f}s")
        print(f"Total Cost: ${summary.total_cost_usd:.4f}")
        print()
        print(f"WEIGHTED QUALITY SCORE: {summary.weighted_quality_score:.1f}/100")
        print(f"RECOMMENDED TIER:       {summary.recommended_tier}")
        print(f"REASONING:              {summary.tier_reasoning}")
        print()
        print("CATEGORY BREAKDOWN:")
        for cat, result in summary.category_results.items():
            print(
                f"  {cat.value:20s}: {result.avg_quality_score:5.1f} "
                f"(pass={result.pass_count}/{result.test_count}, "
                f"ttfb_p95={result.ttfb_p95 * 1000:.0f}ms)"
            )
        print()
        print("LATENCY:")
        print(f"  TTFB P95:  {summary.overall_ttfb_p95 * 1000:.0f}ms")
        print(f"  TPS Mean:  {summary.overall_tps_mean:.1f}")
        print(f"  Meets Targets: {'YES' if summary.meets_latency_targets else 'NO'}")
        print()
        if summary.strengths:
            print("STRENGTHS:")
            for s in summary.strengths:
                print(f"  + {s}")
        if summary.weaknesses:
            print("WEAKNESSES:")
            for w in summary.weaknesses:
                print(f"  - {w}")
        print("=" * 70)


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="GLM-4.5-Air Code Router Benchmark"
    )
    parser.add_argument(
        "--no-streaming",
        action="store_true",
        default=False,
        help="Disable streaming mode (streaming is enabled by default)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Iterations per test case (default: 3)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=2,
        help="Warmup iterations (default: 2)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmark_results",
        help="Output directory for results",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="",
        help="Custom run ID (default: timestamp)",
    )

    args = parser.parse_args()

    # Validate API keys
    if not os.environ.get("SOUNDSGOOD_API_KEY"):
        logger.error("SOUNDSGOOD_API_KEY environment variable not set")
        sys.exit(1)

    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY not set - quality evaluation may fail")

    # Create config
    config = BenchmarkConfig(
        run_id=args.run_id,
        model=SOUNDSGOOD_GLM_45_AIR,
        iterations_per_test=args.iterations,
        warmup_iterations=args.warmup,
        output_dir=args.output_dir,
    )

    # Run benchmark
    orchestrator = BenchmarkOrchestrator(config)
    use_streaming = not args.no_streaming

    try:
        summary = await orchestrator.run(use_streaming=use_streaming)
        logger.info(f"Benchmark complete! Results in {orchestrator.output_dir}")

        # Exit with appropriate code
        if summary.weighted_quality_score >= 65:
            sys.exit(0)  # Pass
        else:
            sys.exit(1)  # Fail

    except KeyboardInterrupt:
        logger.info("Benchmark interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Benchmark failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
