"""Unit tests for the pure quality-inference engine."""

from src.services.quality_inference import (
    NEUTRAL_BASE,
    TASK_TYPES,
    QualitySignals,
    infer_quality,
    infer_quality_from_row,
    is_coder_variant,
    parse_param_billions,
)


class TestParamParsing:
    def test_dense_sizes(self):
        assert parse_param_billions("Llama-3.3-70B-Instruct") == 70.0
        assert parse_param_billions("meta-llama/llama-3.1-8b-instant") == 8.0
        assert parse_param_billions("Qwen2.5-72B-Instruct") == 72.0
        assert parse_param_billions("llama-3.1-405b") == 405.0
        assert parse_param_billions("gemma-2-2b") == 2.0

    def test_decimal_size(self):
        assert parse_param_billions("some-model-1.5b") == 1.5

    def test_moe_active_param_wins(self):
        # "A17B" active-param suffix is what actually runs.
        assert parse_param_billions("Qwen3.5-397B-A17B") == 17.0
        assert parse_param_billions("qwen3-next-80b-a3b-thinking") == 3.0

    def test_moe_experts_notation(self):
        # 8x22B → geometric mean of total(176) and per-expert(22) ≈ 62.2
        v = parse_param_billions("Mixtral-8x22B-Instruct")
        assert v is not None
        assert 60.0 < v < 65.0

    def test_no_size_returns_none(self):
        assert parse_param_billions("GLM-5.1") is None
        assert parse_param_billions("DeepSeek-V4-Pro") is None
        assert parse_param_billions("gemini-2.5-pro") is None
        assert parse_param_billions("gpt-4o") is None
        assert parse_param_billions(None) is None

    def test_does_not_confuse_version_for_size(self):
        # "gemma-3" must not parse 3 as 3B (no trailing b).
        assert parse_param_billions("google/gemma-3-27b-it") == 27.0
        assert parse_param_billions("gemma-3") is None


class TestCoderDetection:
    def test_positive(self):
        assert is_coder_variant("Qwen2.5-Coder-32B-Instruct")
        assert is_coder_variant("deepseek-coder")
        assert is_coder_variant("codestral-latest")

    def test_negative(self):
        assert not is_coder_variant("Llama-3.3-70B-Instruct")
        assert not is_coder_variant(None)


class TestInferQuality:
    def test_returns_all_task_types(self):
        scores = infer_quality(QualitySignals(name="llama-70b"))
        assert set(scores.keys()) == set(TASK_TYPES)

    def test_all_scores_in_range(self):
        for name in ["gpt-4o", "llama-3.1-405b", "tiny-1b", "GLM-5.1", None]:
            for v in infer_quality(QualitySignals(name=name)).values():
                assert 0.0 <= v <= 100.0

    def test_bigger_model_scores_higher_overall(self):
        small = infer_quality(QualitySignals(name="model-8b"))["unknown"]
        big = infer_quality(QualitySignals(name="model-405b"))["unknown"]
        assert big > small

    def test_unknown_size_is_neutral_not_flagship(self):
        # Brand-only name must not earn a smartest-tier (>=85) overall score.
        overall = infer_quality(QualitySignals(name="GLM-5.1"))["unknown"]
        assert abs(overall - NEUTRAL_BASE) <= 1.0
        assert overall < 85.0

    def test_reasoning_boosts_reasoning_tasks(self):
        plain = infer_quality(QualitySignals(name="model-70b", is_reasoning=False))
        reason = infer_quality(QualitySignals(name="model-70b", is_reasoning=True))
        assert reason["complex_reasoning"] > plain["complex_reasoning"]
        assert reason["math_calculation"] > plain["math_calculation"]
        # Non-reasoning task unaffected.
        assert reason["translation"] == plain["translation"]

    def test_coder_boosts_code_tasks(self):
        plain = infer_quality(QualitySignals(name="qwen-32b"))
        coder = infer_quality(QualitySignals(name="qwen-coder-32b"))
        assert coder["code_generation"] > plain["code_generation"]
        assert coder["code_review"] > plain["code_review"]

    def test_deterministic(self):
        sig = QualitySignals(name="llama-3.1-70b", is_reasoning=True, context_length=131072)
        assert infer_quality(sig) == infer_quality(sig)

    def test_large_model_can_reach_smartest_threshold(self):
        # A genuinely large parseable model should be able to earn smartest (>=85).
        overall = infer_quality(QualitySignals(name="llama-3.1-405b"))["unknown"]
        assert overall >= 85.0


class TestInferFromRow:
    def test_reads_name_reasoning_context(self):
        row = {
            "name": "DeepSeek-R1-70B",
            "is_reasoning": True,
            "context_length": "131072",
        }
        scores = infer_quality_from_row(row)
        assert set(scores.keys()) == set(TASK_TYPES)
        assert scores["complex_reasoning"] > scores["translation"]

    def test_falls_back_to_canonical_id(self):
        row = {"canonical_id": "meta-llama/Llama-3.1-405B-Instruct"}
        scores = infer_quality_from_row(row)
        assert scores["unknown"] >= 85.0
