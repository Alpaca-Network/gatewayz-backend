"""Unit tests for cost-routing model canonicalization.

Scope: the offers grouping key only — NOT models.canonical_id (which the quality
join owns).
"""

from __future__ import annotations

from src.services.model_canonicalization import (
    normalization_key,
    offer_group_key,
    suspicious_merges,
)


# --------------------------------------------------------------------------- #
# normalization_key
# --------------------------------------------------------------------------- #

def test_casing_and_separators_collapse():
    assert normalization_key("meta-llama/Llama-3.3-70B-Instruct") == normalization_key(
        "meta-llama/llama-3.3-70b-instruct"
    )
    assert normalization_key("Qwen/Qwen2.5-72B-Instruct") == normalization_key(
        "qwen/qwen-2.5-72b-instruct"
    )


def test_rehost_prefix_stripped():
    assert normalization_key("near/minimax/minimax-m2.5") == normalization_key(
        "minimax/minimax-m2.5"
    )


def test_different_orgs_do_not_collide():
    assert normalization_key("meta-llama/llama-3-70b") != normalization_key(
        "someorg/llama-3-70b"
    )


def test_empty_and_single_segment():
    assert normalization_key("") == ""
    assert normalization_key("gpt-4o") == normalization_key("GPT_4O")


# --------------------------------------------------------------------------- #
# offer_group_key — alias-then-normalize; projection and router share this
# --------------------------------------------------------------------------- #

def test_group_key_matches_across_casing_and_separators():
    assert offer_group_key("Qwen/Qwen2.5-72B-Instruct") == offer_group_key(
        "qwen/qwen-2.5-72b-instruct"
    )


def test_group_key_strips_rehost_prefix():
    assert offer_group_key("near/minimax/minimax-m2.5") == offer_group_key(
        "minimax/minimax-m2.5"
    )


def test_alias_merges_across_orgs():
    # z-ai vs zai-org differ by org → deterministic key keeps them apart;
    # a curated alias merges them onto one canonical id.
    alias_map = {"zai-org/glm-4.7": "z-ai/glm-4.7"}
    assert offer_group_key("zai-org/GLM-4.7", alias_map) == offer_group_key(
        "z-ai/glm-4.7", alias_map
    )


def test_group_key_without_alias_keeps_orgs_apart():
    assert offer_group_key("zai-org/GLM-4.7") != offer_group_key("z-ai/glm-4.7")


def test_group_key_empty():
    assert offer_group_key("") == ""
    assert offer_group_key(None) == ""


# --------------------------------------------------------------------------- #
# suspicious_merges — QA aid
# --------------------------------------------------------------------------- #

def test_suspicious_merge_flagged_on_context_mismatch():
    rows = [
        {"provider_model_id": "org/m", "context_length": 8000},
        {"provider_model_id": "org/M", "context_length": 200000},
    ]
    flags = suspicious_merges(rows)
    assert len(flags) == 1
    assert flags[0]["group_key"] == offer_group_key("org/m")


def test_no_flag_when_contexts_close():
    rows = [
        {"provider_model_id": "org/m", "context_length": 128000},
        {"provider_model_id": "org/M", "context_length": 128000},
    ]
    assert suspicious_merges(rows) == []
