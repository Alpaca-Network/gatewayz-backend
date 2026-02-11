"""
Quality evaluation for GLM-4.5-Air benchmark.

Uses two evaluation strategies:
1. Code execution: For code_generation tests, run provided test cases
2. LLM-as-judge: For reasoning/debugging/refactoring, use GPT-4 for scoring
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

import httpx

from benchmark_config import Difficulty, TestCase, TestCategory

logger = logging.getLogger(__name__)


@dataclass
class QualityScore:
    """Quality evaluation result."""

    overall_score: float  # 0-100
    criteria_scores: dict[str, float]  # Individual criteria scores
    feedback: str  # Explanation of the score
    passed: bool  # Meets minimum threshold
    execution_result: str | None = None  # For code execution tests


class QualityEvaluator:
    """Evaluates model response quality using code execution and LLM-as-judge."""

    def __init__(
        self,
        judge_model: str = "gpt-4-turbo-preview",
        judge_api_key: str | None = None,
        min_passing_score: float = 60.0,
        timeout_seconds: float = 30.0,
    ):
        self.judge_model = judge_model
        self.judge_api_key = judge_api_key or os.environ.get("OPENAI_API_KEY", "")
        self.min_passing_score = min_passing_score
        self.timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "QualityEvaluator":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0)
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def evaluate(
        self,
        test_case: TestCase,
        model_response: str,
        reasoning_content: str | None = None,
    ) -> QualityScore:
        """
        Evaluate the quality of a model response.

        Args:
            test_case: The test case definition
            model_response: The model's response content
            reasoning_content: Optional reasoning field for reasoning models

        Returns:
            QualityScore with overall score and breakdown
        """
        if test_case.category == TestCategory.CODE_GENERATION:
            return await self._evaluate_code_generation(test_case, model_response)
        else:
            return await self._evaluate_with_judge(
                test_case, model_response, reasoning_content
            )

    async def _evaluate_code_generation(
        self, test_case: TestCase, model_response: str
    ) -> QualityScore:
        """Evaluate code generation by running test cases."""
        # Extract code from response
        code = self._extract_code(model_response)
        if not code:
            return QualityScore(
                overall_score=0.0,
                criteria_scores={"code_extraction": 0.0, "tests_passed": 0.0},
                feedback="Could not extract code from response",
                passed=False,
                execution_result="No code found",
            )

        # Run test cases if available
        if test_case.test_code:
            execution_result = await self._run_code_tests(code, test_case.test_code)
            tests_passed = execution_result.get("passed", False)
            test_output = execution_result.get("output", "")

            if tests_passed:
                # All tests passed - high score
                base_score = 85.0

                # Bonus for clean code
                code_quality_bonus = self._assess_code_quality(code)
                overall = min(100.0, base_score + code_quality_bonus)

                return QualityScore(
                    overall_score=overall,
                    criteria_scores={
                        "tests_passed": 100.0,
                        "code_quality": code_quality_bonus * 5,
                    },
                    feedback=f"All tests passed. Code quality bonus: {code_quality_bonus:.1f}",
                    passed=True,
                    execution_result=test_output[:500],
                )
            else:
                # Tests failed - use LLM to assess partial credit
                partial_score = await self._assess_partial_credit(
                    test_case, code, test_output
                )
                return QualityScore(
                    overall_score=partial_score,
                    criteria_scores={
                        "tests_passed": 0.0,
                        "partial_credit": partial_score,
                    },
                    feedback=f"Tests failed: {test_output[:200]}",
                    passed=partial_score >= self.min_passing_score,
                    execution_result=test_output[:500],
                )
        else:
            # No test code - use LLM judge
            return await self._evaluate_with_judge(test_case, model_response, None)

    def _extract_code(self, response: str) -> str | None:
        """Extract code block from model response."""
        # Try to find fenced code blocks
        patterns = [
            r"```python\n(.*?)```",
            r"```\n(.*?)```",
            r"```(.*?)```",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            if matches:
                # Return the largest code block
                return max(matches, key=len).strip()

        # If no fenced block, try to find function definition
        func_match = re.search(
            r"(def \w+\(.*?\):.*?)(?=\n\n|\nclass |\ndef |\Z)",
            response,
            re.DOTALL,
        )
        if func_match:
            return func_match.group(1).strip()

        return None

    async def _run_code_tests(
        self, code: str, test_code: str
    ) -> dict[str, Any]:
        """Run code with test cases in a sandboxed environment."""
        # Combine code and tests
        full_code = f"{code}\n\n{test_code}"

        # Write to temp file and execute
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(full_code)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["python", temp_path],
                capture_output=True,
                text=True,
                timeout=10,  # 10 second timeout
            )

            passed = result.returncode == 0
            output = result.stdout if passed else result.stderr

            return {
                "passed": passed,
                "output": output,
                "return_code": result.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "output": "Execution timed out (>10s)",
                "return_code": -1,
            }
        except Exception as e:
            return {
                "passed": False,
                "output": f"Execution error: {str(e)}",
                "return_code": -1,
            }
        finally:
            os.unlink(temp_path)

    def _assess_code_quality(self, code: str) -> float:
        """Simple heuristic-based code quality assessment."""
        bonus = 0.0

        # Has docstring
        if '"""' in code or "'''" in code:
            bonus += 2.0

        # Has type hints
        if "->" in code or ": int" in code or ": str" in code or ": list" in code:
            bonus += 2.0

        # Reasonable line length (no super long lines)
        lines = code.split("\n")
        if all(len(line) < 100 for line in lines):
            bonus += 1.0

        # Uses list comprehension or generator expressions
        if "[" in code and "for" in code:
            bonus += 1.0

        # Handles edge cases (checks for None, empty, etc)
        if "if not" in code or "is None" in code or "len(" in code:
            bonus += 2.0

        return min(bonus, 10.0)  # Cap at 10

    async def _assess_partial_credit(
        self, test_case: TestCase, code: str, error_output: str
    ) -> float:
        """Use LLM to assess partial credit for failed tests."""
        prompt = f"""You are evaluating a coding solution that failed its test cases.

TASK:
{test_case.prompt}

EXPECTED BEHAVIOR:
{test_case.expected_behavior}

SUBMITTED CODE:
```python
{code}
```

ERROR OUTPUT:
{error_output[:500]}

Assess this solution for partial credit. Consider:
1. Is the general approach correct?
2. Are there minor bugs that could be easily fixed?
3. Does it handle the core logic correctly?

Respond with a JSON object:
{{
    "score": <number 0-50>,
    "reasoning": "<brief explanation>"
}}

Only give significant partial credit (30+) if the approach is fundamentally correct with minor bugs.
"""

        try:
            response = await self._call_judge(prompt)
            data = self._parse_json_response(response)
            return float(data.get("score", 20.0))
        except Exception as e:
            logger.warning(f"Partial credit assessment failed: {e}")
            return 20.0  # Default partial credit

    async def _evaluate_with_judge(
        self,
        test_case: TestCase,
        model_response: str,
        reasoning_content: str | None,
    ) -> QualityScore:
        """Use GPT-4 as judge for reasoning/debugging/refactoring tasks."""
        criteria_text = "\n".join(
            f"- {c}" for c in test_case.evaluation_criteria
        )

        reasoning_section = ""
        if reasoning_content:
            reasoning_section = f"""
MODEL'S REASONING PROCESS:
{reasoning_content[:2000]}
"""

        difficulty_weight = {
            Difficulty.EASY: 1.0,
            Difficulty.MEDIUM: 1.0,
            Difficulty.HARD: 1.1,  # Slight bonus for hard problems
        }

        prompt = f"""You are an expert code reviewer evaluating an AI model's response to a coding task.

TASK:
{test_case.prompt}

EXPECTED BEHAVIOR:
{test_case.expected_behavior}

EVALUATION CRITERIA:
{criteria_text}
{reasoning_section}
MODEL'S RESPONSE:
{model_response[:4000]}

Evaluate the response on each criterion and provide an overall score.

For each criterion, score 0-100:
- 0-20: Does not address the criterion
- 21-40: Partially addresses with significant issues
- 41-60: Addresses but with notable gaps
- 61-80: Good solution with minor issues
- 81-100: Excellent solution

Respond with a JSON object:
{{
    "criteria_scores": {{
        "<criterion_name>": <score>,
        ...
    }},
    "overall_score": <weighted_average>,
    "feedback": "<2-3 sentence summary of strengths and weaknesses>"
}}
"""

        try:
            response = await self._call_judge(prompt)
            data = self._parse_json_response(response)

            criteria_scores = data.get("criteria_scores", {})
            overall = float(data.get("overall_score", 50.0))

            # Apply difficulty weight
            weight = difficulty_weight.get(test_case.difficulty, 1.0)
            overall = min(100.0, overall * weight)

            return QualityScore(
                overall_score=overall,
                criteria_scores=criteria_scores,
                feedback=data.get("feedback", "No feedback provided"),
                passed=overall >= self.min_passing_score,
            )

        except Exception as e:
            logger.error(f"Judge evaluation failed: {e}")
            return QualityScore(
                overall_score=0.0,
                criteria_scores={},
                feedback=f"Evaluation failed: {str(e)}",
                passed=False,
            )

    async def _call_judge(self, prompt: str) -> str:
        """Call the judge model (GPT-4) for evaluation."""
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")

        if not self.judge_api_key:
            raise ValueError("OPENAI_API_KEY not set for judge model")

        response = await self._client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.judge_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.judge_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert code reviewer. Respond only with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 1000,
                "temperature": 0.3,  # Lower temp for consistent scoring
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling markdown code blocks."""
        # Remove markdown code blocks if present
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            # Remove first and last lines (```json and ```)
            response = "\n".join(lines[1:-1])

        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise


class BatchEvaluator:
    """Batch evaluation for multiple test results."""

    def __init__(self, evaluator: QualityEvaluator):
        self.evaluator = evaluator

    async def evaluate_batch(
        self,
        test_cases: list[TestCase],
        responses: list[tuple[str, str | None]],  # (content, reasoning)
        concurrency: int = 5,
    ) -> list[QualityScore]:
        """
        Evaluate multiple responses concurrently.

        Args:
            test_cases: List of test case definitions
            responses: List of (content, reasoning) tuples
            concurrency: Max concurrent evaluations

        Returns:
            List of QualityScore results
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def evaluate_one(
            test_case: TestCase, content: str, reasoning: str | None
        ) -> QualityScore:
            async with semaphore:
                return await self.evaluator.evaluate(test_case, content, reasoning)

        tasks = [
            evaluate_one(tc, content, reasoning)
            for tc, (content, reasoning) in zip(test_cases, responses)
        ]

        return await asyncio.gather(*tasks)


async def test_evaluator():
    """Test the quality evaluator."""
    from benchmark_config import TestCategory, Difficulty

    # Create a simple test case
    test_case = TestCase(
        id="test_001",
        category=TestCategory.CODE_GENERATION,
        difficulty=Difficulty.EASY,
        prompt="Write a function to check if a number is prime",
        expected_behavior="Should correctly identify prime numbers",
        evaluation_criteria=["correctness", "efficiency", "edge_cases"],
        test_code="""
# Test cases
assert is_prime(2) == True
assert is_prime(3) == True
assert is_prime(4) == False
assert is_prime(17) == True
assert is_prime(1) == False
print("All tests passed!")
""",
    )

    # Sample model response
    model_response = '''Here's a function to check if a number is prime:

```python
def is_prime(n):
    """Check if n is a prime number."""
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True
```
'''

    async with QualityEvaluator() as evaluator:
        score = await evaluator.evaluate(test_case, model_response)
        print(f"Score: {score.overall_score}")
        print(f"Criteria: {score.criteria_scores}")
        print(f"Feedback: {score.feedback}")
        print(f"Passed: {score.passed}")
        if score.execution_result:
            print(f"Execution: {score.execution_result}")


if __name__ == "__main__":
    asyncio.run(test_evaluator())
