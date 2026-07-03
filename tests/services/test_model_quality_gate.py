"""Tests for the catalog quality gate — precision matters more than recall.

Legit models MUST NOT be dropped (false positives corrupt the catalog); obvious
quant/merge/RP junk SHOULD be dropped.
"""

from src.services.model_quality_gate import assess

# Real models we must never drop.
LEGIT_IDS = [
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4",
    "meta-llama/Llama-3.3-70B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",
    "deepseek-ai/DeepSeek-V3",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "google/gemma-2-27b-it",
    "microsoft/phi-4",
    "nvidia/Llama-3.1-Nemotron-70B-Instruct",  # 'int'? no — must stay
]

# Junk that should be filtered, with the expected reason prefix.
JUNK_CASES = [
    ("TheBloke/Llama-2-7B-GGUF", "quant:gguf"),
    ("bartowski/Qwen2.5-7B-Instruct-GGUF", "quant:gguf"),
    ("mradermacher/SomeModel-i1-GGUF", "quant:gguf"),
    ("model-Q4_K_M", "quant:llamacpp"),
    ("SomeModel-Q5_0", "quant:llamacpp"),
    ("Llama-3-8B-6.0bpw-exl2", "quant:bpw"),
    ("Qwen2.5-32B-GPTQ-Int4", "quant:weightformat"),
    ("model-AWQ", "quant:weightformat"),
    ("Mistral-7B-int4", "quant:bits"),
    ("Frankenstein-11B-slerp", "merge"),
    ("SomeMerge-passthrough-20B", "merge"),
    ("Silicon-Maid-7B-uncensored", "adult-rp"),
    ("PornModel-NSFW-13B", "adult-rp"),
]


def _m(model_id: str) -> dict:
    return {"id": model_id, "name": model_id}


def test_legit_models_are_kept():
    for mid in LEGIT_IDS:
        v = assess(_m(mid), "featherless")
        assert v.keep, f"false positive: {mid} dropped as {v.reason}"


def test_junk_models_are_dropped_with_reason():
    for mid, expected_reason in JUNK_CASES:
        v = assess(_m(mid), "featherless")
        assert not v.keep, f"junk not caught: {mid}"
        assert v.reason == expected_reason, f"{mid}: got {v.reason}, want {expected_reason}"


def test_empty_model_is_invalid():
    assert not assess({}, "featherless").keep
    assert assess({}, "featherless").reason == "invalid:no-id"


def test_matches_across_dict_shapes():
    # id absent but model_id present
    assert not assess({"model_id": "foo-GGUF"}, "x").keep
    # name carries the junk marker
    assert not assess({"id": "clean-id", "name": "clean but Q4_K_M quant"}, "x").keep


def test_kept_model_reason_is_ok():
    assert assess(_m("openai/gpt-4o"), "openrouter").reason == "ok"
