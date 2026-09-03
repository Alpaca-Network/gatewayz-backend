"""Tests for src.services.gpu.spot_check (gatewayz-backend#2265)."""

import random
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.gpu import spot_check
from src.services.gpu.earnings import EarningResult


@pytest.fixture
def sb():
    return None


def _raw_response(text: str, completion_tokens: int):
    """A minimal stand-in for the OpenAI-SDK-shaped `raw` response object
    the ProviderAdapter contract (src/services/providers/base.py) documents:
    raw.choices[0].message.content, raw.usage.completion_tokens."""
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


# ---------------------------------------------------------------------------
# should_spot_check
# ---------------------------------------------------------------------------


def test_should_spot_check_uses_seeded_rng_below_rate(sb):
    with patch("src.services.gpu.spot_check.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_RATE = 0.5
        rng = MagicMock()
        rng.random.return_value = 0.3
        assert spot_check.should_spot_check("br-1", attested_expected=True, rng=rng) is True


def test_should_spot_check_uses_seeded_rng_above_rate(sb):
    with patch("src.services.gpu.spot_check.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_RATE = 0.5
        rng = MagicMock()
        rng.random.return_value = 0.7
        assert spot_check.should_spot_check("br-1", attested_expected=True, rng=rng) is False


def test_should_spot_check_doubles_rate_when_not_attested(sb):
    with patch("src.services.gpu.spot_check.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_RATE = 0.1
        rng = MagicMock()
        rng.random.return_value = 0.15  # between 0.1 (attested) and 0.2 (unattested)
        assert spot_check.should_spot_check("br-1", attested_expected=True, rng=rng) is False
        assert spot_check.should_spot_check("br-1", attested_expected=False, rng=rng) is True


def test_should_spot_check_caps_doubled_rate_at_one(sb):
    with patch("src.services.gpu.spot_check.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_RATE = 0.9
        rng = MagicMock()
        rng.random.return_value = 0.99
        assert spot_check.should_spot_check("br-1", attested_expected=False, rng=rng) is True


def test_should_spot_check_default_rng_is_random_module(sb):
    """Statistical smoke test with the real stdlib random module, seeded
    for reproducibility -- proves the default `rng=None` path actually
    draws from something real, not just that injection works."""
    with patch("src.services.gpu.spot_check.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_RATE = 0.5
        r = random.Random(1234)
        hits = sum(1 for _ in range(2000) if spot_check.should_spot_check("br", True, rng=r))
        assert 800 < hits < 1200  # ~50% +/- generous margin


# ---------------------------------------------------------------------------
# stash / get_stashed_prompt / maybe_stash
# ---------------------------------------------------------------------------


@patch("src.services.gpu.spot_check.get_redis_client")
def test_stash_prompt_writes_with_ttl(mock_get_redis, sb):
    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    assert (
        spot_check.stash_prompt_for_spot_check("br-1", [{"role": "user", "content": "hi"}], "m")
        is True
    )

    args, _ = mock_redis.setex.call_args
    assert args[0] == "gpu_spotcheck:br-1"
    assert args[1] == 1200


@patch("src.services.gpu.spot_check.get_redis_client")
def test_stash_prompt_returns_false_when_redis_unavailable(mock_get_redis, sb):
    mock_get_redis.return_value = None
    assert spot_check.stash_prompt_for_spot_check("br-1", [], "m") is False


@patch("src.services.gpu.spot_check.get_redis_client")
def test_get_stashed_prompt_round_trips_json(mock_get_redis, sb):
    mock_redis = MagicMock()
    mock_redis.get.return_value = b'{"messages": [{"role": "user", "content": "hi"}], "model": "m"}'
    mock_get_redis.return_value = mock_redis

    result = spot_check.get_stashed_prompt("br-1")
    assert result == {"messages": [{"role": "user", "content": "hi"}], "model": "m"}


@patch("src.services.gpu.spot_check.get_redis_client")
def test_get_stashed_prompt_returns_none_when_missing(mock_get_redis, sb):
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    mock_get_redis.return_value = mock_redis
    assert spot_check.get_stashed_prompt("br-1") is None


@patch("src.services.gpu.spot_check.stash_prompt_for_spot_check")
@patch("src.services.gpu.spot_check.should_spot_check")
def test_maybe_stash_reads_attested_heartbeat_from_node(mock_should, mock_stash, sb):
    mock_should.return_value = True
    mock_stash.return_value = True

    spot_check.maybe_stash("br-1", [], "m", {"attested_heartbeat": True})
    assert mock_should.call_args.kwargs["attested_expected"] is True

    spot_check.maybe_stash("br-1", [], "m", {"attested_heartbeat": False})
    assert mock_should.call_args.kwargs["attested_expected"] is False

    spot_check.maybe_stash("br-1", [], "m", None)
    assert mock_should.call_args.kwargs["attested_expected"] is False


@patch("src.services.gpu.spot_check.stash_prompt_for_spot_check")
@patch("src.services.gpu.spot_check.should_spot_check")
def test_maybe_stash_skips_stash_when_not_sampled(mock_should, mock_stash, sb):
    mock_should.return_value = False
    assert spot_check.maybe_stash("br-1", [], "m", None) is False
    mock_stash.assert_not_called()


# ---------------------------------------------------------------------------
# _verify_sampled_row verdicts
# ---------------------------------------------------------------------------


def _work_row(**overrides):
    base = {
        "id": 1,
        "billing_ref": "br-1",
        "node_id": 7,
        "provider_id": 3,
        "model": "community/llama-3.1-8b-instruct",
        "completion_tokens": 40,
    }
    base.update(overrides)
    return base


@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_skipped_when_no_stash(mock_get_stash, sb):
    mock_get_stash.return_value = None
    outcome = await spot_check._verify_sampled_row(_work_row())
    assert outcome == "skipped"


@patch("src.services.gpu.spot_check.get_node")
@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_skipped_when_node_missing(mock_get_stash, mock_get_node, sb):
    mock_get_stash.return_value = {"messages": [], "model": "m"}
    mock_get_node.return_value = None
    outcome = await spot_check._verify_sampled_row(_work_row())
    assert outcome == "skipped"


@patch("src.services.gpu.spot_check._get_node_adapter")
@patch("src.services.gpu.spot_check.get_node")
@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_skipped_when_adapter_unavailable(
    mock_get_stash, mock_get_node, mock_get_adapter, sb
):
    """adapter_for_node (W-A2's real community_adapter) can fail to build
    a client for a given node (e.g. a malformed/missing endpoint) --
    _get_node_adapter must return None for that, and this must degrade to
    'skipped', not crash."""
    mock_get_stash.return_value = {"messages": [{"role": "user", "content": "hi"}], "model": "m"}
    mock_get_node.return_value = {"id": 7}
    mock_get_adapter.return_value = None

    outcome = await spot_check._verify_sampled_row(_work_row())
    assert outcome == "skipped"


def test_get_node_adapter_returns_a_real_adapter_for_a_well_formed_node(sb):
    """Exercises the REAL, unmocked community_adapter.adapter_for_node
    (merged W-A2, gatewayz-backend#2287) -- there is no lazy-import/
    ImportError branch anymore. A node with no encrypted key needs
    nothing decrypted, and building an OpenAI client never makes a
    network call, so this is safe to run unmocked."""
    from src.services.providers.community_adapter import clear_adapter_cache

    clear_adapter_cache()
    try:
        node = {"id": 999999999, "endpoint_url": "http://127.0.0.1:1", "name": "test-node"}
        adapter = spot_check._get_node_adapter(node)
        assert adapter is not None
        assert hasattr(adapter, "request")
    finally:
        clear_adapter_cache()


def test_get_node_adapter_returns_none_on_a_malformed_node(sb):
    """A node missing required fields (e.g. endpoint_url) makes the real
    adapter_for_node raise -- _get_node_adapter must catch that and
    return None, not propagate."""
    assert spot_check._get_node_adapter({"id": 1}) is None


@patch("src.services.gpu.spot_check._get_reference_request_fn")
@patch("src.services.gpu.spot_check._get_node_adapter")
@patch("src.services.gpu.spot_check.get_node")
@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_verified_without_reference(
    mock_get_stash, mock_get_node, mock_get_adapter, mock_get_ref, sb
):
    mock_get_stash.return_value = {"messages": [{"role": "user", "content": "hi"}], "model": "m"}
    mock_get_node.return_value = {"id": 7}
    adapter = MagicMock()
    adapter.request = MagicMock(return_value=_raw_response("hello world", 40))
    mock_get_adapter.return_value = adapter
    mock_get_ref.return_value = None

    outcome = await spot_check._verify_sampled_row(_work_row(completion_tokens=40))
    assert outcome == "verified"


@patch("src.services.gpu.spot_check._get_reference_request_fn")
@patch("src.services.gpu.spot_check._get_node_adapter")
@patch("src.services.gpu.spot_check.get_node")
@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_failed_on_empty_reply(
    mock_get_stash, mock_get_node, mock_get_adapter, mock_get_ref, sb
):
    mock_get_stash.return_value = {"messages": [{"role": "user", "content": "hi"}], "model": "m"}
    mock_get_node.return_value = {"id": 7}
    adapter = MagicMock()
    adapter.request = MagicMock(return_value=_raw_response("", 0))
    mock_get_adapter.return_value = adapter
    mock_get_ref.return_value = None

    outcome = await spot_check._verify_sampled_row(_work_row(completion_tokens=40))
    assert outcome == "failed"


@patch("src.services.gpu.spot_check._get_reference_request_fn")
@patch("src.services.gpu.spot_check._get_node_adapter")
@patch("src.services.gpu.spot_check.get_node")
@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_failed_on_implausible_token_count(
    mock_get_stash, mock_get_node, mock_get_adapter, mock_get_ref, sb
):
    mock_get_stash.return_value = {"messages": [{"role": "user", "content": "hi"}], "model": "m"}
    mock_get_node.return_value = {"id": 7}
    adapter = MagicMock()
    # claimed 40, replay only produced 5 -- outside +/-25% tolerance.
    adapter.request = MagicMock(return_value=_raw_response("short", 5))
    mock_get_adapter.return_value = adapter
    mock_get_ref.return_value = None

    outcome = await spot_check._verify_sampled_row(_work_row(completion_tokens=40))
    assert outcome == "failed"


@patch("src.services.gpu.spot_check._get_reference_request_fn")
@patch("src.services.gpu.spot_check._get_node_adapter")
@patch("src.services.gpu.spot_check.get_node")
@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_failed_on_low_reference_similarity(
    mock_get_stash, mock_get_node, mock_get_adapter, mock_get_ref, sb
):
    mock_get_stash.return_value = {"messages": [{"role": "user", "content": "hi"}], "model": "m"}
    mock_get_node.return_value = {"id": 7}
    adapter = MagicMock()
    adapter.request = MagicMock(return_value=_raw_response("completely unrelated output text", 5))
    mock_get_adapter.return_value = adapter
    mock_get_ref.return_value = MagicMock(
        return_value=_raw_response("a totally different reply", 5)
    )

    outcome = await spot_check._verify_sampled_row(_work_row(completion_tokens=5))
    assert outcome == "failed"


@patch("src.services.gpu.spot_check._get_reference_request_fn")
@patch("src.services.gpu.spot_check._get_node_adapter")
@patch("src.services.gpu.spot_check.get_node")
@patch("src.services.gpu.spot_check.get_stashed_prompt")
@pytest.mark.asyncio
async def test_verify_sampled_row_verified_with_matching_reference(
    mock_get_stash, mock_get_node, mock_get_adapter, mock_get_ref, sb
):
    mock_get_stash.return_value = {"messages": [{"role": "user", "content": "hi"}], "model": "m"}
    mock_get_node.return_value = {"id": 7}
    text = "the quick brown fox jumps over the lazy dog"
    adapter = MagicMock()
    adapter.request = MagicMock(return_value=_raw_response(text, 9))
    mock_get_adapter.return_value = adapter
    mock_get_ref.return_value = MagicMock(return_value=_raw_response(text, 9))

    outcome = await spot_check._verify_sampled_row(_work_row(completion_tokens=9))
    assert outcome == "verified"


# ---------------------------------------------------------------------------
# _apply_sampled_outcome side effects
# ---------------------------------------------------------------------------


@patch("src.services.gpu.spot_check.record_earning_for_verified_work")
@patch("src.services.gpu.spot_check.set_verification")
def test_apply_outcome_verified_records_earning(mock_set_ver, mock_record, sb):
    mock_record.return_value = EarningResult(earning={"id": 1}, outcome="created")
    work = _work_row()

    final_outcome = spot_check._apply_sampled_outcome(work, "verified")

    assert final_outcome == "verified"
    mock_set_ver.assert_called_once_with(1, "verified")
    mock_record.assert_called_once_with(work)


@patch("src.services.gpu.spot_check.record_earning_for_verified_work")
@patch("src.services.gpu.spot_check.set_verification")
def test_apply_outcome_verified_but_not_payable_downgrades_to_skipped(
    mock_set_ver, mock_record, sb
):
    """PR #2288 review C1: a 'verified' row whose model isn't on the
    payout allow-list must be persisted as 'skipped', not 'verified' --
    the caller (run_spot_check_verification) must use this return value
    for stats, not the outcome it was called with."""
    mock_record.return_value = EarningResult(earning=None, outcome="not_payable")
    work = _work_row()

    final_outcome = spot_check._apply_sampled_outcome(work, "verified")

    assert final_outcome == "skipped"
    mock_set_ver.assert_called_once_with(1, "skipped")


@patch("src.services.gpu.spot_check.disable_node")
@patch("src.services.gpu.spot_check.node_verification_stats_since")
@patch("src.services.gpu.spot_check.adjust_health_score")
@patch("src.services.gpu.spot_check.void_earning_for_work")
@patch("src.services.gpu.spot_check.set_verification")
def test_apply_outcome_failed_voids_and_penalizes_health(
    mock_set_ver, mock_void, mock_adjust, mock_stats, mock_disable, sb
):
    mock_stats.return_value = (1, 5)  # 1 failure so far -- below disable threshold
    work = _work_row(node_id=7)
    spot_check._apply_sampled_outcome(work, "failed")

    mock_set_ver.assert_called_once_with(1, "failed")
    mock_void.assert_called_once_with(1)
    mock_adjust.assert_called_once_with(7, -20)
    mock_disable.assert_not_called()


@patch("src.services.gpu.spot_check.disable_node")
@patch("src.services.gpu.spot_check.node_verification_stats_since")
@patch("src.services.gpu.spot_check.adjust_health_score")
@patch("src.services.gpu.spot_check.void_earning_for_work")
@patch("src.services.gpu.spot_check.set_verification")
def test_apply_outcome_failed_disables_node_after_three_failures(
    mock_set_ver, mock_void, mock_adjust, mock_stats, mock_disable, sb
):
    mock_stats.return_value = (3, 5)
    work = _work_row(node_id=7)
    spot_check._apply_sampled_outcome(work, "failed")
    mock_disable.assert_called_once_with(7)


# ---------------------------------------------------------------------------
# _resolve_aged_row
# ---------------------------------------------------------------------------


@patch("src.services.gpu.spot_check.node_verification_stats_since")
def test_resolve_aged_row_verified_when_low_failure_rate(mock_stats, sb):
    mock_stats.return_value = (1, 100)  # 1% failure rate
    assert spot_check._resolve_aged_row(_work_row(node_id=7)) == "verified"


@patch("src.services.gpu.spot_check.node_verification_stats_since")
def test_resolve_aged_row_skipped_when_high_failure_rate(mock_stats, sb):
    mock_stats.return_value = (10, 100)  # 10% failure rate
    assert spot_check._resolve_aged_row(_work_row(node_id=7)) == "skipped"


@patch("src.services.gpu.spot_check.node_verification_stats_since")
def test_resolve_aged_row_verified_when_no_history(mock_stats, sb):
    mock_stats.return_value = (0, 0)
    assert spot_check._resolve_aged_row(_work_row(node_id=7)) == "verified"


# ---------------------------------------------------------------------------
# _resolve_verified_aged_row_outcome / _reconcile_missing_earnings (C1 / I1)
# ---------------------------------------------------------------------------


@patch("src.services.gpu.spot_check.record_earning_for_verified_work")
def test_resolve_verified_aged_row_outcome_verified_when_payable(mock_record, sb):
    mock_record.return_value = EarningResult(earning={"id": 1}, outcome="created")
    assert spot_check._resolve_verified_aged_row_outcome(_work_row()) == "verified"


@patch("src.services.gpu.spot_check.record_earning_for_verified_work")
def test_resolve_verified_aged_row_outcome_skipped_when_not_payable(mock_record, sb):
    mock_record.return_value = EarningResult(earning=None, outcome="not_payable")
    assert spot_check._resolve_verified_aged_row_outcome(_work_row()) == "skipped"


@patch("src.services.gpu.spot_check.record_earning_for_verified_work")
@patch("src.services.gpu.spot_check.list_verified_work_since")
def test_reconcile_missing_earnings_retries_every_recently_verified_row(
    mock_list_verified, mock_record, sb
):
    mock_list_verified.return_value = [_work_row(id=1), _work_row(id=2)]
    mock_record.side_effect = [
        EarningResult(earning=None, outcome="duplicate"),  # already paid -- no-op
        EarningResult(earning={"id": 9}, outcome="created"),  # recovers a lost payout
    ]

    created = spot_check._reconcile_missing_earnings()

    assert created == 1
    assert mock_record.call_count == 2


@patch("src.services.gpu.spot_check.record_earning_for_verified_work")
@patch("src.services.gpu.spot_check.list_verified_work_since")
def test_reconcile_missing_earnings_is_zero_when_nothing_verified(
    mock_list_verified, mock_record, sb
):
    mock_list_verified.return_value = []
    assert spot_check._reconcile_missing_earnings() == 0
    mock_record.assert_not_called()


# ---------------------------------------------------------------------------
# run_spot_check_verification (end to end orchestration)
# ---------------------------------------------------------------------------


@patch("src.services.gpu.spot_check._reconcile_missing_earnings")
@patch("src.services.gpu.spot_check._resolve_verified_aged_row_outcome")
@patch("src.services.gpu.spot_check.set_verification")
@patch("src.services.gpu.spot_check._resolve_aged_row")
@patch("src.services.gpu.spot_check.list_agable_pending_work")
@patch("src.services.gpu.spot_check._apply_sampled_outcome")
@patch("src.services.gpu.spot_check._verify_sampled_row")
@patch("src.services.gpu.spot_check.list_sampled_pending_work")
@pytest.mark.asyncio
async def test_run_spot_check_verification_aggregates_stats(
    mock_list_sampled,
    mock_verify,
    mock_apply,
    mock_list_agable,
    mock_resolve_aged,
    mock_set_ver,
    mock_resolve_verified_aged,
    mock_reconcile,
    sb,
):
    mock_list_sampled.return_value = [_work_row(id=1), _work_row(id=2), _work_row(id=3)]
    mock_verify.side_effect = ["verified", "failed", "skipped"]
    mock_apply.side_effect = lambda work, outcome: outcome  # no C1 downgrade in this test
    mock_list_agable.return_value = [_work_row(id=4)]
    mock_resolve_aged.return_value = "verified"
    mock_resolve_verified_aged.return_value = "verified"
    mock_reconcile.return_value = 0

    stats = await spot_check.run_spot_check_verification()

    assert stats == {"verified": 2, "failed": 1, "skipped": 1}
    mock_resolve_verified_aged.assert_called_once()  # the aged row went through the C1 payability check
    mock_reconcile.assert_called_once()


@patch("src.services.gpu.spot_check._reconcile_missing_earnings")
@patch("src.services.gpu.spot_check.list_agable_pending_work")
@patch("src.services.gpu.spot_check._verify_sampled_row")
@patch("src.services.gpu.spot_check.list_sampled_pending_work")
@pytest.mark.asyncio
async def test_run_spot_check_verification_never_raises_on_row_error(
    mock_list_sampled, mock_verify, mock_list_agable, mock_reconcile, sb
):
    mock_list_sampled.return_value = [_work_row(id=1)]
    mock_verify.side_effect = RuntimeError("boom")
    mock_list_agable.return_value = []
    mock_reconcile.return_value = 0

    stats = await spot_check.run_spot_check_verification()
    assert stats["skipped"] == 1


# ---------------------------------------------------------------------------
# Per-run / per-node replay caps (PR #2288 review I2)
# ---------------------------------------------------------------------------


@patch("src.services.gpu.spot_check._reconcile_missing_earnings")
@patch("src.services.gpu.spot_check.list_agable_pending_work")
@patch("src.services.gpu.spot_check.asyncio.sleep")
@patch("src.services.gpu.spot_check._apply_sampled_outcome")
@patch("src.services.gpu.spot_check._verify_sampled_row")
@patch("src.services.gpu.spot_check.list_sampled_pending_work")
@pytest.mark.asyncio
async def test_run_spot_check_verification_respects_per_run_replay_cap(
    mock_list_sampled, mock_verify, mock_apply, mock_sleep, mock_list_agable, mock_reconcile, sb
):
    rows = [_work_row(id=i, node_id=i) for i in range(10)]  # distinct nodes -- isolates the run cap
    mock_list_sampled.return_value = rows
    mock_verify.return_value = "verified"
    mock_apply.side_effect = lambda work, outcome: outcome
    mock_list_agable.return_value = []
    mock_reconcile.return_value = 0

    with patch("src.services.gpu.spot_check.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_RUN = 3
        mock_config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_NODE_PER_RUN = 100
        mock_config.COMMUNITY_SPOTCHECK_REPLAY_DELAY_SECONDS = 0
        stats = await spot_check.run_spot_check_verification()

    assert mock_verify.call_count == 3
    assert stats["verified"] == 3


@patch("src.services.gpu.spot_check._reconcile_missing_earnings")
@patch("src.services.gpu.spot_check.list_agable_pending_work")
@patch("src.services.gpu.spot_check.asyncio.sleep")
@patch("src.services.gpu.spot_check._apply_sampled_outcome")
@patch("src.services.gpu.spot_check._verify_sampled_row")
@patch("src.services.gpu.spot_check.list_sampled_pending_work")
@pytest.mark.asyncio
async def test_run_spot_check_verification_respects_per_node_replay_cap(
    mock_list_sampled, mock_verify, mock_apply, mock_sleep, mock_list_agable, mock_reconcile, sb
):
    # 5 rows all on the SAME node -- the per-node cap must bind before the
    # (much higher) per-run cap does.
    rows = [_work_row(id=i, node_id=7) for i in range(5)]
    mock_list_sampled.return_value = rows
    mock_verify.return_value = "verified"
    mock_apply.side_effect = lambda work, outcome: outcome
    mock_list_agable.return_value = []
    mock_reconcile.return_value = 0

    with patch("src.services.gpu.spot_check.Config") as mock_config:
        mock_config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_RUN = 50
        mock_config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_NODE_PER_RUN = 2
        mock_config.COMMUNITY_SPOTCHECK_REPLAY_DELAY_SECONDS = 0
        stats = await spot_check.run_spot_check_verification()

    assert mock_verify.call_count == 2
    assert stats["skipped"] == 3  # the 3 remaining rows for that node


@patch("src.services.gpu.spot_check._reconcile_missing_earnings")
@patch("src.services.gpu.spot_check.list_agable_pending_work")
@patch("src.services.gpu.spot_check._apply_sampled_outcome")
@patch("src.services.gpu.spot_check._verify_sampled_row")
@patch("src.services.gpu.spot_check.list_sampled_pending_work")
@pytest.mark.asyncio
async def test_run_spot_check_verification_sleeps_between_replays(
    mock_list_sampled, mock_verify, mock_apply, mock_list_agable, mock_reconcile, sb
):
    mock_list_sampled.return_value = [_work_row(id=1, node_id=1), _work_row(id=2, node_id=2)]
    mock_verify.return_value = "verified"
    mock_apply.side_effect = lambda work, outcome: outcome
    mock_list_agable.return_value = []
    mock_reconcile.return_value = 0

    with (
        patch("src.services.gpu.spot_check.Config") as mock_config,
        patch("src.services.gpu.spot_check.asyncio.sleep") as mock_sleep,
    ):
        mock_config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_RUN = 50
        mock_config.COMMUNITY_SPOTCHECK_MAX_REPLAYS_PER_NODE_PER_RUN = 50
        mock_config.COMMUNITY_SPOTCHECK_REPLAY_DELAY_SECONDS = 0.5
        await spot_check.run_spot_check_verification()

    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(0.5)
