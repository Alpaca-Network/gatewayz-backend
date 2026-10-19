"""Tests for scripts/verify_usage_anchor.py -- the standalone proof verifier
must agree with the backend's own Merkle code and contract ABI."""

import json
from datetime import date
from unittest.mock import patch

import pytest
from web3 import Web3

from scripts.verify_usage_anchor import (
    ROOT_OF_SELECTOR,
    main,
    root_of_calldata,
    verify,
)
from src.services.chain.merkle import inclusion_proof_from_hashes
from src.services.chain.usage_anchor import build_day_tree, day_index, root_hex

DAY = date(2026, 10, 18)


@pytest.fixture
def sb():
    """No-op fixture whose presence bypasses the autouse DB-skip."""
    return None


def _proof_doc(n=5, index=3):
    rows = [
        {
            "id": i,
            "created_at": f"2026-10-18T{i:02d}:00:00+00:00",
            "amount": "-0.001",
            "metadata": {"model": "m", "prompt_tokens": 1, "completion_tokens": 2},
        }
        for i in range(1, n + 1)
    ]
    tree = build_day_tree(DAY, rows, "salt")
    return {
        "leaf": tree.leaves[index].decode(),
        "leaf_index": index,
        "tree_size": n,
        "proof": ["0x" + p.hex() for p in inclusion_proof_from_hashes(index, tree.leaf_hashes)],
        "root": root_hex(tree.root),
        "day": DAY.isoformat(),
        "day_index": day_index(DAY),
        "chain_id": 43113,
        "contract_address": "0x00000000000000000000000000000000000000aa",
    }, tree.root


def test_selector_matches_contract_abi(sb):
    assert ROOT_OF_SELECTOR == Web3.keccak(text="rootOf(uint64)")[:4].hex()
    assert root_of_calldata(1) == "0x" + ROOT_OF_SELECTOR + "00" * 31 + "01"


@pytest.mark.parametrize("n,index", [(1, 0), (2, 1), (5, 3), (17, 16)])
def test_offline_verification_matches_backend_tree(sb, n, index):
    doc, _ = _proof_doc(n, index)
    ok, _ = verify(doc, None)
    assert ok


def test_tampered_leaf_fails(sb):
    doc, _ = _proof_doc()
    doc["leaf"] = doc["leaf"].replace('"cost":"-0.00100000"', '"cost":"-0.00000001"')
    ok, lines = verify(doc, None)
    assert not ok and "MISMATCH" in lines[0]


def test_onchain_check_uses_rootof_and_compares(sb):
    doc, root = _proof_doc()
    with patch("scripts.verify_usage_anchor.fetch_onchain_root", return_value=root) as fetch:
        ok, _ = verify(doc, "http://rpc")
    assert ok
    fetch.assert_called_once_with("http://rpc", doc["contract_address"], doc["day_index"])
    with patch("scripts.verify_usage_anchor.fetch_onchain_root", return_value=b"\x00" * 32):
        assert verify(doc, "http://rpc")[0] is False


def test_main_exit_codes(sb, tmp_path):
    doc, _ = _proof_doc()
    good = tmp_path / "proof.json"
    good.write_text(json.dumps({"data": doc}))
    assert main([str(good), "--offline"]) == 0
    doc["root"] = "0x" + "00" * 32
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc))
    assert main([str(bad), "--offline"]) == 1
    junk = tmp_path / "junk.json"
    junk.write_text("not json")
    assert main([str(junk), "--offline"]) == 2
