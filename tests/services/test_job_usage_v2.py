"""v2 usage lines (cost disputes): hashing pinned to gatewayz-genlayer, token split,
price sheets, and the v2 entry record_job_usage writes next to the v1 one.

tests/fixtures/genlayer_usage_v2.json is a verbatim copy of
Alpaca-Network/gatewayz-genlayer fixtures/usage-v2.json (commit 192aa5e), generated
by gzgl/billing.py. Every value must reproduce byte for byte; if this file fails,
the backend and the CostDispute contract disagree.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services import job_usage as ju
from src.services import job_usage_v2 as v2

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "genlayer_usage_v2.json").read_text()
)
JOB = "0x" + "ef" * 32


@pytest.fixture(autouse=True)
def _v2_on(monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", True, raising=False)


# --------------------------------------------------------------------------- fixture


@pytest.mark.parametrize("i", range(len(FIXTURE["lines"])))
def test_fixture_content_hash_and_bundle(i):
    line = FIXTURE["lines"][i]
    bundle = v2.canonical_json({"messages": line["messages"], "output": line["output"]})
    assert bundle.decode() == line["content_bundle_utf8"]
    assert v2.content_hash(line["messages"], line["output"]) == line["entry"]["content_hash"]


@pytest.mark.parametrize("i", range(len(FIXTURE["lines"])))
def test_fixture_entry_leaf_and_proof(i):
    entries = [ln["entry"] for ln in FIXTURE["lines"]]
    line = FIXTURE["lines"][i]
    assert v2.canonical_entry(line["entry"]).decode() == line["canonical_entry_utf8"]
    p = v2.proof_for(entries, i)
    assert p["leaf"] == line["leaf"]
    assert p["proof"] == line["proof"]
    assert p["root"] == FIXTURE["root"]


def test_fixture_root_and_price_sheet_hash():
    entries = [ln["entry"] for ln in FIXTURE["lines"]]
    assert v2.seal(entries)["root"] == FIXTURE["root"]
    assert v2.sha256_hex(v2.canonical_json(FIXTURE["price_sheet"])) == FIXTURE["price_sheet_hash"]


def test_fixture_covers_the_cases_the_spec_names():
    entries = [ln["entry"] for ln in FIXTURE["lines"]]
    assert any(e["tokens_reasoning"] > 0 for e in entries)
    assert any(e["finish_reason"] == "length" for e in entries)
    assert any(
        any(ord(ch) > 127 for ch in json.dumps(ln["messages"], ensure_ascii=False))
        for ln in FIXTURE["lines"]
    )


def test_v2_differs_from_v1_hashing():
    """sha256 tree, not the keccak one; the v1 fixture is pinned in test_job_usage.py."""
    entry = FIXTURE["lines"][0]["entry"]
    assert v2.leaf_hash(entry) == hashlib.sha256(b"\x00" + v2.canonical_entry(entry)).digest()


def test_entry_is_strict():
    entry = dict(FIXTURE["lines"][0]["entry"])
    with pytest.raises(ValueError):
        v2.canonical_entry({**entry, "prompt": "leak"})
    with pytest.raises(ValueError):
        v2.canonical_entry({k: v for k, v in entry.items() if k != "tokens_reasoning"})
    with pytest.raises(ValueError):
        v2.canonical_entry({**entry, "tokens_out": -1})
    with pytest.raises(ValueError):
        v2.canonical_entry({**entry, "cost_usd": "0.0000000001"})  # 10 decimals


def test_empty_job_seals_to_zero():
    assert v2.seal([]) == {"root": v2.ZERO_ROOT, "requests": 0, "cost_usd": "0"}


# --------------------------------------------------------------------------- amounts


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0"),
        (0.0000195, "0.0000195"),
        (0.00311025, "0.00311025"),
        (1.23456789049, "1.23456789"),
        (1.2345678905, "1.234567891"),  # half-up
        (12, "12"),
    ],
)
def test_usd9(value, expected):
    assert v2.usd9(value) == expected
    v2.to_nano(v2.usd9(value))  # always parseable by the contract


# --------------------------------------------------------------------------- content


def test_content_hash_is_reproducible_by_the_client():
    raw = '{"model":"m","messages":[{"role":"user","content":"Zürich?"}]}'
    msgs = json.loads(raw)["messages"]
    expected = (
        "0x"
        + hashlib.sha256(
            json.dumps(
                {"messages": msgs, "output": "Ja."},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    assert v2.content_hash(msgs, "Ja.") == expected


def test_unencodable_content_records_zero_hash():
    assert v2.content_hash([{"role": "user", "content": "\ud800"}], "x") == v2.ZERO_HASH


# --------------------------------------------------------------------------- tokens


@pytest.mark.parametrize(
    "usage,prompt,completion,expected",
    [
        # OpenAI / OpenRouter: reasoning is inside completion_tokens.
        (
            {"completion_tokens_details": {"reasoning_tokens": 1536}, "total_tokens": 1583},
            25,
            1558,
            (22, 1536),
        ),
        (
            SimpleNamespace(
                completion_tokens_details=SimpleNamespace(reasoning_tokens=40), total_tokens=60
            ),
            10,
            50,
            (10, 40),
        ),
        # xAI-style: reasoning reported on top of completion_tokens (total covers all
        # three). It was never in the billed count, so it is not on the line either.
        ({"reasoning_tokens": 300, "total_tokens": 410}, 10, 100, (100, 0)),
        ({"reasoning_tokens": 300}, 10, 100, (100, 0)),  # reasoning > completion
        # No reasoning reported (Anthropic, Vertex, plain models).
        ({"total_tokens": 15}, 10, 5, (5, 0)),
        (None, 0, 0, (0, 0)),
    ],
)
def test_split_completion_tokens(usage, prompt, completion, expected):
    assert v2.split_completion_tokens(usage, prompt, completion) == expected


# --------------------------------------------------------------------------- price sheet


def test_price_sheet_body_hashes_to_its_key():
    h, body = v2.price_sheet("openai/gpt-5-mini", 0.25e-6, 2e-6, 1.0)
    assert h == "0x" + hashlib.sha256(body).hexdigest()
    sheet = json.loads(body)
    assert sheet["models"]["openai/gpt-5-mini"] == {"in_per_mtok": "0.25", "out_per_mtok": "2"}
    assert body == v2.canonical_json(sheet)  # served bytes are the canonical form


def test_price_sheet_applies_markup_and_is_stable():
    a = v2.price_sheet("m", 1e-6, 4e-6, 1.1)
    b = v2.price_sheet("m", 1e-6, 4e-6, 1.1)
    assert a == b  # same rates, same hash: one stored sheet
    assert json.loads(a[1])["models"]["m"] == {"in_per_mtok": "1.1", "out_per_mtok": "4.4"}


def test_price_sheet_matches_the_contracts_cost_formula():
    """The contract recomputes the charge from the sheet; it must land on our cost."""
    prompt_rate, completion_rate, markup = 0.25e-6, 2e-6, 1.1
    _, body = v2.price_sheet("openai/gpt-5-mini", prompt_rate, completion_rate, markup)
    rates = json.loads(body)["models"]["openai/gpt-5-mini"]
    tokens_in, visible, reasoning = 25, 22, 1536
    # What pricing.calculate_cost bills: billed completion tokens include reasoning.
    billed = v2.to_nano(
        v2.usd9((tokens_in * prompt_rate + (visible + reasoning) * completion_rate) * markup)
    )
    expected = (
        tokens_in * v2.to_nano(rates["in_per_mtok"])
        + (visible + reasoning) * v2.to_nano(rates["out_per_mtok"])
    ) // 10**6
    assert abs(billed - expected) <= 1  # nano-dollar rounding only


# --------------------------------------------------------------------------- record_job_usage


def _record(**kw):
    user = {"id": 1, "key_name": f"job:{JOB}"}
    # model, provider, tokens_in, tokens_out (billed), cost_usd, commit
    args = ("openai/gpt-5-mini", "openai", 25, 1558, 0.00311025, "ref-9")
    with (
        patch("src.db.inference_jobs.append_usage", return_value=4) as app,
        patch(
            "src.services.pricing.get_model_pricing",
            return_value={"prompt": 0.25e-6, "completion": 2e-6, "source": "db"},
        ),
        patch("src.config.config.Config.PRICING_MARKUP", 1.0),
    ):
        seq = ju.record_job_usage(user, *args, **kw)
    return seq, app


def test_record_writes_v1_unchanged_and_v2_alongside():
    ch = "0x" + "aa" * 32
    seq, app = _record(
        request_id="ref-9",
        tokens_reasoning=1536,
        finish_reason="stop",
        content_hash=ch,
        priced_model="openai/gpt-5-mini",
    )
    assert seq == 4
    job_id, entry = app.call_args.args
    assert job_id == JOB
    # v1: exactly the old fields and values (billed completion tokens).
    assert set(entry) == set(ju.ENTRY_FIELDS) and entry["tokens_out"] == 1558
    kw = app.call_args.kwargs
    e2 = kw["entry_v2"]
    assert set(e2) == set(v2.ENTRY_FIELDS)
    assert (e2["tokens_out"], e2["tokens_reasoning"]) == (22, 1536)
    assert e2["cost_usd"] == "0.00311025" and e2["status"] == 200
    assert e2["content_hash"] == ch and e2["request_id"] == "ref-9"
    assert e2["ts"] == entry["ts"] and e2["commit"] == entry["commit"]
    # The stored sheet is exactly the bytes the line's hash names.
    assert e2["price_sheet"] == v2.sha256_hex(kw["price_sheet_body"].encode())
    assert kw["usage_estimated"] is False


def test_record_never_puts_content_in_either_entry():
    _, app = _record(content_hash="0x" + "bb" * 32, finish_reason="stop")
    blob = json.dumps([app.call_args.args[1], app.call_args.kwargs["entry_v2"]])
    assert "messages" not in blob and "output" not in blob


def test_record_defaults_are_safe():
    _, app = _record()
    e2 = app.call_args.kwargs["entry_v2"]
    assert e2["finish_reason"] == "unknown"
    assert e2["content_hash"] == v2.ZERO_HASH  # not captured: judged on metadata only
    assert e2["request_id"] == "ref-9"  # falls back to the billing ref (commit)


def test_record_flags_estimated_usage():
    _, app = _record(usage_estimated=True)
    assert app.call_args.kwargs["usage_estimated"] is True


def test_reasoning_never_exceeds_billed_tokens():
    _, app = _record(tokens_reasoning=10**6)
    e2 = app.call_args.kwargs["entry_v2"]
    assert e2["tokens_out"] == 0 and e2["tokens_reasoning"] == 1558


def test_bad_content_hash_is_replaced_not_trusted():
    _, app = _record(content_hash="0xNOTAHASH")
    assert app.call_args.kwargs["entry_v2"]["content_hash"] == v2.ZERO_HASH


def test_missing_pricing_records_zero_sheet_and_keeps_the_line():
    user = {"id": 1, "key_name": f"job:{JOB}"}
    with (
        patch("src.db.inference_jobs.append_usage", return_value=0) as app,
        patch("src.services.pricing.get_model_pricing", side_effect=ValueError("no pricing")),
    ):
        assert ju.record_job_usage(user, "m", "p", 1, 1, 0.01, "c") == 0
    kw = app.call_args.kwargs
    assert kw["entry_v2"]["price_sheet"] == v2.ZERO_HASH and kw["price_sheet_body"] is None


def test_v2_failure_never_costs_the_v1_line():
    user = {"id": 1, "key_name": f"job:{JOB}"}
    with (
        patch("src.db.inference_jobs.append_usage", return_value=3) as app,
        patch.object(ju, "_build_entry_v2", side_effect=RuntimeError("boom")),
    ):
        assert ju.record_job_usage(user, "m", "p", 1, 1, 0.01, "c") == 3
    assert app.call_args.kwargs == {}  # v1-only append


def test_ordinary_keys_still_record_nothing():
    with patch("src.db.inference_jobs.append_usage") as app:
        assert ju.record_job_usage({"key_name": "prod"}, "m", "p", 1, 1, 0.01, "c") is None
    app.assert_not_called()


def test_flag_off_writes_exactly_the_v1_line(monkeypatch):
    from src.config.config import Config

    monkeypatch.setattr(Config, "JOB_USAGE_V2_ENABLED", False)
    _, app = _record(tokens_reasoning=1536, content_hash="0x" + "aa" * 32)
    assert app.call_args.kwargs == {}  # the pre-migration append_job_usage call


def test_cache_write_rate_is_published_only_above_the_input_rate():
    _, with_write = v2.price_sheet(
        "anthropic/claude-x", 3e-6, 15e-6, 1.0, cache_write_per_token=3.75e-6
    )
    assert json.loads(with_write)["models"]["anthropic/claude-x"]["cache_write_per_mtok"] == "3.75"
    _, no_premium = v2.price_sheet("openai/gpt-x", 3e-6, 15e-6, 1.0, cache_write_per_token=3e-6)
    assert "cache_write_per_mtok" not in json.loads(no_premium)["models"]["openai/gpt-x"]
    # Without the field the sheet (and its hash) is exactly what it was before.
    assert v2.price_sheet("m", 1e-6, 2e-6, 1.0) == v2.price_sheet("m", 1e-6, 2e-6, 1.0, None)


def test_a_total_inflated_by_something_else_is_not_unbilled_reasoning():
    usage = {"completion_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 999}
    assert v2.split_completion_tokens(usage, 10, 50) == (40, 10)


def test_record_publishes_the_cache_write_rate_for_premium_providers():
    user = {"id": 1, "key_name": f"job:{JOB}"}
    with (
        patch("src.db.inference_jobs.append_usage", return_value=0) as app,
        patch(
            "src.services.pricing.get_model_pricing",
            return_value={"prompt": 3e-6, "completion": 15e-6, "cache_write": 3.75e-6},
        ),
        patch("src.config.config.Config.PRICING_MARKUP", 1.0),
    ):
        ju.record_job_usage(user, "anthropic/claude-x", "anthropic", 1, 1, 0.01, "c")
    body = json.loads(app.call_args.kwargs["price_sheet_body"])
    assert body["models"]["anthropic/claude-x"]["cache_write_per_mtok"] == "3.75"
