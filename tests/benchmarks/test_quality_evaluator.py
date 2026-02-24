"""Tests for quality evaluator module."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add benchmark scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "benchmarks"))

from benchmark_config import Difficulty, TestCase, TestCategory
from quality_evaluator import QualityEvaluator, QualityScore


class TestQualityScore:
    """Tests for QualityScore dataclass."""

    def test_create_quality_score(self):
        """Test creating a quality score."""
        score = QualityScore(
            overall_score=85.5,
            criteria_scores={"correctness": 90.0, "style": 80.0},
            feedback="Good solution with minor style issues",
            passed=True,
            execution_result="All tests passed!",
        )

        assert score.overall_score == 85.5
        assert len(score.criteria_scores) == 2
        assert score.passed is True
        assert score.execution_result is not None

    def test_quality_score_optional_execution_result(self):
        """Test that execution_result is optional."""
        score = QualityScore(
            overall_score=75.0,
            criteria_scores={},
            feedback="Evaluated via LLM judge",
            passed=True,
        )

        assert score.execution_result is None


class TestQualityEvaluator:
    """Tests for QualityEvaluator class."""

    def test_init_defaults(self):
        """Test default initialization."""
        evaluator = QualityEvaluator()

        assert evaluator.judge_model == "gpt-4-turbo-preview"
        assert evaluator.min_passing_score == 60.0
        assert evaluator.timeout == 30.0

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        evaluator = QualityEvaluator(
            judge_model="gpt-4o",
            judge_api_key="test_key",
            min_passing_score=70.0,
            timeout_seconds=60.0,
        )

        assert evaluator.judge_model == "gpt-4o"
        assert evaluator.judge_api_key == "test_key"
        assert evaluator.min_passing_score == 70.0
        assert evaluator.timeout == 60.0

    def test_extract_code_fenced_python(self):
        """Test extracting Python code from fenced block."""
        evaluator = QualityEvaluator()

        response = """Here's the solution:

```python
def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True
```

This function checks if a number is prime.
"""

        code = evaluator._extract_code(response)

        assert code is not None
        assert "def is_prime(n):" in code
        assert "return True" in code

    def test_extract_code_generic_fenced(self):
        """Test extracting code from generic fenced block."""
        evaluator = QualityEvaluator()

        response = """Solution:

```
def add(a, b):
    return a + b
```
"""

        code = evaluator._extract_code(response)

        assert code is not None
        assert "def add(a, b):" in code

    def test_extract_code_no_fence(self):
        """Test extracting code without fence markers."""
        evaluator = QualityEvaluator()

        response = """Here's the function:

def multiply(x, y):
    return x * y
"""

        code = evaluator._extract_code(response)

        assert code is not None
        assert "def multiply" in code

    def test_extract_code_empty(self):
        """Test extracting from response with no code."""
        evaluator = QualityEvaluator()

        response = "This is just text without any code."

        code = evaluator._extract_code(response)

        assert code is None

    def test_assess_code_quality_with_docstring(self):
        """Test code quality assessment with docstring."""
        evaluator = QualityEvaluator()

        code = '''def is_prime(n: int) -> bool:
    """Check if n is a prime number."""
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True
'''

        bonus = evaluator._assess_code_quality(code)

        # Should get bonus for: docstring, type hints, edge case handling
        assert bonus >= 4.0

    def test_assess_code_quality_minimal(self):
        """Test code quality assessment with minimal code."""
        evaluator = QualityEvaluator()

        code = """def f(x):
    return x * 2
"""

        bonus = evaluator._assess_code_quality(code)

        # Minimal code, low bonus
        assert bonus <= 3.0

    def test_parse_json_response_clean(self):
        """Test parsing clean JSON response."""
        evaluator = QualityEvaluator()

        response = '{"score": 85, "reasoning": "Good solution"}'
        result = evaluator._parse_json_response(response)

        assert result["score"] == 85
        assert result["reasoning"] == "Good solution"

    def test_parse_json_response_with_markdown(self):
        """Test parsing JSON with markdown code block."""
        evaluator = QualityEvaluator()

        response = """```json
{"score": 90, "feedback": "Excellent!"}
```"""
        result = evaluator._parse_json_response(response)

        assert result["score"] == 90
        assert result["feedback"] == "Excellent!"

    def test_parse_json_response_embedded(self):
        """Test parsing JSON embedded in text."""
        evaluator = QualityEvaluator()

        response = 'Here is my evaluation: {"score": 75}'
        result = evaluator._parse_json_response(response)

        assert result["score"] == 75


class TestCodeExecutionEvaluation:
    """Tests for code execution-based evaluation."""

    @pytest.mark.asyncio
    async def test_run_code_tests_success(self):
        """Test running code tests that pass."""
        evaluator = QualityEvaluator()

        code = """def add(a, b):
    return a + b"""

        test_code = """assert add(1, 2) == 3
assert add(0, 0) == 0
print("Tests passed!")"""

        result = await evaluator._run_code_tests(code, test_code)

        assert result["passed"] is True
        assert "Tests passed!" in result["output"]

    @pytest.mark.asyncio
    async def test_run_code_tests_failure(self):
        """Test running code tests that fail."""
        evaluator = QualityEvaluator()

        code = """def add(a, b):
    return a - b  # Bug!"""

        test_code = """assert add(1, 2) == 3"""

        result = await evaluator._run_code_tests(code, test_code)

        assert result["passed"] is False
        assert "AssertionError" in result["output"]

    @pytest.mark.asyncio
    async def test_run_code_tests_syntax_error(self):
        """Test running code with syntax error."""
        evaluator = QualityEvaluator()

        code = """def broken(
    return None"""

        test_code = """pass"""

        result = await evaluator._run_code_tests(code, test_code)

        assert result["passed"] is False
        assert "SyntaxError" in result["output"]


class TestEvaluateCodeGeneration:
    """Tests for code generation evaluation."""

    @pytest.mark.asyncio
    async def test_evaluate_code_gen_passed(self):
        """Test evaluating passing code generation."""
        test_case = TestCase(
            id="cg_001",
            category=TestCategory.CODE_GENERATION,
            difficulty=Difficulty.EASY,
            prompt="Write a function to add two numbers",
            expected_behavior="Returns sum of two numbers",
            evaluation_criteria=["correctness"],
            test_code="""assert add(1, 2) == 3
assert add(-1, 1) == 0
print("All tests passed!")""",
        )

        response = """```python
def add(a, b):
    \"\"\"Add two numbers.\"\"\"
    return a + b
```"""

        async with QualityEvaluator() as evaluator:
            score = await evaluator._evaluate_code_generation(test_case, response)

        assert score.overall_score >= 85.0
        assert score.passed is True
        assert (
            "tests passed" in score.feedback.lower()
            or score.criteria_scores.get("tests_passed", 0) > 0
        )

    @pytest.mark.asyncio
    async def test_evaluate_code_gen_no_code(self):
        """Test evaluating response with no extractable code."""
        test_case = TestCase(
            id="cg_002",
            category=TestCategory.CODE_GENERATION,
            difficulty=Difficulty.EASY,
            prompt="Write a function",
            expected_behavior="Should work",
            evaluation_criteria=["correctness"],
            test_code="pass",
        )

        response = "I cannot provide code for this request."

        async with QualityEvaluator() as evaluator:
            score = await evaluator._evaluate_code_generation(test_case, response)

        assert score.overall_score == 0.0
        assert score.passed is False
        assert "could not extract code" in score.feedback.lower()


class TestIntegration:
    """Integration tests for the evaluator."""

    @pytest.fixture
    def mock_judge_response(self):
        """Create mock judge response."""
        return """{
            "criteria_scores": {
                "bug_identified": 90.0,
                "correct_fix": 85.0
            },
            "overall_score": 87.5,
            "feedback": "Good bug identification and fix."
        }"""

    @pytest.mark.asyncio
    async def test_evaluate_with_mock_judge(self, mock_judge_response):
        """Test evaluation with mocked judge call."""
        evaluator = QualityEvaluator(judge_api_key="test_key")
        evaluator._client = MagicMock()

        # Mock the HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": mock_judge_response}}]
        }
        mock_response.raise_for_status = MagicMock()

        evaluator._client.post = AsyncMock(return_value=mock_response)

        test_case = TestCase(
            id="db_001",
            category=TestCategory.DEBUGGING,
            difficulty=Difficulty.MEDIUM,
            prompt="Find and fix the bug",
            expected_behavior="Should fix the bug",
            evaluation_criteria=["bug_identified", "correct_fix"],
        )

        score = await evaluator._evaluate_with_judge(
            test_case, "Fixed code here", "I noticed the bug was..."
        )

        assert score.overall_score == pytest.approx(87.5, rel=0.1)
        assert score.passed is True
        assert "bug_identified" in score.criteria_scores
