"""Job-scoped keys + sealed usage records (inference escrow).

The Merkle fixture below was computed by gzgl/usage.py in
Alpaca-Network/gatewayz-genlayer — the same hashing InferenceEscrow.verifyUsageLeaf
checks on-chain. If this file's root ever differs, a root Gatewayz seals would not
verify on the escrow, so this is a cross-repo contract test, not a self-check.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.security.deps import ceiling_http_exception
from src.services import job_usage as ju

COSTS = ["0.0123", "0.5", "1", "0.00001", "2.10"]
ENTRIES = [
    {
        "ts": f"2026-10-05T12:00:0{i}Z",
        "model": "anthropic/claude-sonnet-5",
        "provider": "anthropic",
        "tokens_in": 1000 + i,
        "tokens_out": 400 + i,
        "cost_usd": COSTS[i],
        "commit": f"req-{i}",
    }
    for i in range(5)
]
GZGL_ROOT = "0x9a4ae32bbe8b6ea2a179eb7a2957b58bab2e56e105ccf83fecfd0c8e31fabf8a"
GZGL_PROOF_3 = [
    "0x24ebfc29c2df691147b50336efa889b40fbeab756ef172e9e5b09459342b0617",
    "0x3a7ab10fd2b6d2e80b5dff1da4ce289dbe3cc5b779f38a390b8f3c86296c8a57",
    "0xfb493934ba746c1732c0b1896d548ed23ae7596b2691ac15e51e533df5a9c9b6",
]
JOB = "0x" + "ab" * 32


# ------------------------------------------------------------------ hashing


def test_root_matches_the_escrow_repo():
    s = ju.seal(ENTRIES)
    assert s["root"] == GZGL_ROOT
    assert (s["requests"], s["tokens_in"], s["tokens_out"], s["cost_usd"]) == (
        5,
        5010,
        2010,
        "3.61231",
    )


def test_proof_matches_the_escrow_repo():
    p = ju.proof_for(ENTRIES, 3)
    assert p["proof"] == GZGL_PROOF_3
    assert p["leaf"] == "0x9c57649abac73d3ef7516c6dcae8babb0ed47ccba9c127f59358b096c5200819"


def test_empty_job_seals_to_zero_root():
    assert ju.seal([]) == {
        "root": ju.ZERO_ROOT,
        "requests": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": "0",
    }


def test_entry_refuses_content_fields():
    with pytest.raises(ValueError):
        ju.canonical_entry({**ENTRIES[0], "prompt": "secret"})


def test_cost_format_does_not_change_the_leaf():
    assert ju.leaf_hash({**ENTRIES[0], "cost_usd": "0.0123"}) == ju.leaf_hash(
        {**ENTRIES[0], "cost_usd": 0.0123}
    )


# ------------------------------------------------------------------ enforce_job_key


def _client(rows):
    client = MagicMock()
    q = client.table.return_value.select.return_value.eq.return_value
    q.execute.return_value = MagicMock(data=rows)
    return client


def _job(**over):
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    return {"status": "running", "cap_usd": "5", "spent_usd": "1.25", "deadline": deadline, **over}


def test_ordinary_keys_never_touch_the_jobs_table():
    client = MagicMock()
    ju.enforce_job_key({"id": 1, "key_name": "my key"}, client)
    ju.enforce_job_key({"id": 1, "key_name": None}, client)
    client.table.assert_not_called()


def test_running_job_under_cap_passes():
    ju.enforce_job_key({"id": 7, "key_name": f"job:{JOB}"}, _client([_job()]))


@pytest.mark.parametrize(
    "job,msg",
    [
        (_job(spent_usd="5"), ju.JOB_CAP_REACHED),
        (_job(spent_usd="5.0000001"), ju.JOB_CAP_REACHED),
        (_job(status="closed"), ju.JOB_NOT_RUNNING),
        (_job(deadline=(datetime.now(UTC) - timedelta(seconds=1)).isoformat()), ju.JOB_NOT_RUNNING),
    ],
)
def test_job_key_refused(job, msg):
    with pytest.raises(ValueError, match=msg):
        ju.enforce_job_key({"id": 7, "key_name": f"job:{JOB}"}, _client([job]))


def test_lookalike_key_name_without_a_job_is_not_blocked():
    ju.enforce_job_key({"id": 7, "key_name": "job:my-own-name"}, _client([]))


def test_failures_map_to_typed_errors():
    cap = ceiling_http_exception(ju.JOB_CAP_REACHED)
    assert cap.status_code == 402 and cap.detail["error"]["code"] == "job_cap_exhausted"
    closed = ceiling_http_exception(ju.JOB_NOT_RUNNING)
    assert closed.status_code == 409 and closed.detail["error"]["code"] == "job_not_running"
    # the pre-existing request cap is unchanged
    assert (
        ceiling_http_exception("API key request limit reached").detail["error"]["code"]
        == "request_cap_exhausted"
    )


# ------------------------------------------------------------------ record_job_usage


def test_record_is_a_noop_for_ordinary_keys():
    with patch("src.db.inference_jobs.append_usage") as append:
        assert ju.record_job_usage({"key_name": "prod"}, "m", "p", 1, 1, 0.1, "r") is None
        assert ju.record_job_usage(None, "m", "p", 1, 1, 0.1, "r") is None
        append.assert_not_called()


def test_record_appends_billing_metadata_only():
    with patch("src.db.inference_jobs.append_usage", return_value=4) as append:
        seq = ju.record_job_usage(
            {"key_name": f"job:{JOB}"}, "openai/gpt-5-mini", "openai", 120, 30, 0.000375, "req-9"
        )
    assert seq == 4
    job_id, entry = append.call_args.args
    assert job_id == JOB
    assert set(entry) == set(ju.ENTRY_FIELDS)
    assert entry["cost_usd"] == "0.000375" and entry["tokens_in"] == 120
    ju.canonical_entry(entry)  # hashable as-is


def test_a_lost_line_is_logged_loudly_not_raised(caplog):
    with patch("src.db.inference_jobs.append_usage", side_effect=RuntimeError("job_not_running")):
        assert ju.record_job_usage({"key_name": f"job:{JOB}"}, "m", "p", 1, 1, 0.5, "r") is None
    assert any(r.levelname == "ERROR" and "LOST" in r.getMessage() for r in caplog.records)
