"""genlayer-py 0.19 compatibility helpers in src/services/genlayer_verify.py."""

from types import SimpleNamespace

import pytest

from src.services.genlayer_verify import TxState, _hex_chain_id_in_gas_estimates, status_name


@pytest.mark.parametrize(
    "tx,expected",
    [
        ({"status_name": "FINALIZED"}, "FINALIZED"),  # genlayer-py <= 0.18
        ({"lifecycle": {"state": "processing", "phase": "uninitialized"}}, "PENDING"),
        ({"lifecycle": {"state": "processing", "phase": "revealing"}}, "REVEALING"),
        ({"lifecycle": {"state": "decided", "outcome": "accepted"}}, "ACCEPTED"),
        ({"lifecycle": {"state": "decided", "outcome": "undetermined"}}, "UNDETERMINED"),
        ({"lifecycle": {"state": "decided", "outcome": "leader_timeout"}}, "LEADER_TIMEOUT"),
        ({"lifecycle": {"state": "finalized"}}, "FINALIZED"),
        ({"lifecycle": {"state": "canceled"}}, "CANCELED"),
        ({}, ""),
    ],
)
def test_status_name(tx, expected):
    assert status_name(tx) == expected


def test_lifecycle_states_drive_txstate():
    accepted = TxState(
        status_name({"lifecycle": {"state": "decided", "outcome": "accepted"}}),
        "MAJORITY_AGREE",
        None,
        "0xVJ",
    )
    assert accepted.decided and not accepted.dead
    timeout = TxState(
        status_name({"lifecycle": {"state": "decided", "outcome": "leader_timeout"}}),
        None,
        None,
        "0xVJ",
    )
    assert timeout.dead


def test_gas_estimates_send_chain_id_as_hex():
    sent = []
    client = SimpleNamespace(
        provider=SimpleNamespace(make_request=lambda m, p: sent.append((m, p)) or {"result": "0x1"})
    )
    _hex_chain_id_in_gas_estimates(client)
    client.provider.make_request("eth_estimateGas", [{"chainId": 4221}])
    client.provider.make_request("eth_call", [{"chainId": 4221}])
    assert sent == [("eth_estimateGas", [{"chainId": "0x107d"}]), ("eth_call", [{"chainId": 4221}])]


def test_leader_rollback_counts_as_failed_execution():
    from src.services.genlayer_verify import _leader_rollback

    rollback = {"status": "rollback", "payload": "submitter_not_allowed"}
    refused = {"consensus_data": {"leader_receipt": [{"result": rollback}]}}
    assert _leader_rollback(refused) == "FINISHED_WITH_ERROR"
    assert TxState("ACCEPTED", "MAJORITY_AGREE", _leader_rollback(refused), "0xVJ").failed_execution
    returned = {"consensus_data": {"leader_receipt": [{"result": {"status": "return"}}]}}
    assert _leader_rollback(returned) is None
    assert _leader_rollback({}) is None
