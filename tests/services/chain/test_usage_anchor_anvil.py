"""End-to-end rehearsal of usage anchoring against a real EVM (Anvil).

Runs the production code paths -- job, client, sweep, proof, offline
verifier -- against the real UsageAnchor bytecode on a local chain. Only the
Supabase layer is replaced with an in-memory store.

Marked `integration`, so the default run skips it. Needs Foundry (`anvil`,
`forge`) and a checkout of gatewayz-protocol (USAGE_ANCHOR_PROTOCOL_DIR,
default ../gatewayz-protocol). Run:

    pytest -m integration tests/services/chain/test_usage_anchor_anvil.py -p no:xdist
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from eth_account import Account
from web3 import Web3

pytestmark = [pytest.mark.integration, pytest.mark.timeout(180)]

PROTOCOL_DIR = Path(
    os.environ.get(
        "USAGE_ANCHOR_PROTOCOL_DIR", Path(__file__).resolve().parents[3] / "../gatewayz-protocol"
    )
).resolve()
# Anvil's well-known dev keys -- public, local chain only.
DEPLOYER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
ANCHORER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
ANVIL_CHAIN_ID = 31337

if not (shutil.which("anvil") and shutil.which("forge") and (PROTOCOL_DIR / "src").exists()):
    pytest.skip("needs Foundry and gatewayz-protocol", allow_module_level=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def chain():
    port = _free_port()
    proc = subprocess.Popen(
        ["anvil", "--port", str(port), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rpc = f"http://127.0.0.1:{port}"
    w3 = Web3(Web3.HTTPProvider(rpc))
    for _ in range(50):
        if w3.is_connected():
            break
        time.sleep(0.1)
    try:
        bytecode = subprocess.check_output(
            ["forge", "inspect", "UsageAnchor", "bytecode"], cwd=PROTOCOL_DIR, text=True
        ).strip()
        abi = json.loads(
            subprocess.check_output(
                ["forge", "inspect", "UsageAnchor", "abi", "--json"], cwd=PROTOCOL_DIR, text=True
            )
        )
        deployer = Account.from_key(DEPLOYER_KEY)
        anchorer = Account.from_key(ANCHORER_KEY)
        tx = (
            w3.eth.contract(abi=abi, bytecode=bytecode)
            .constructor(anchorer.address)
            .build_transaction(
                {
                    "from": deployer.address,
                    "nonce": w3.eth.get_transaction_count(deployer.address),
                    "chainId": ANVIL_CHAIN_ID,
                }
            )
        )
        signed = deployer.sign_transaction(tx)
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(signed.raw_transaction)
        )
        contract = w3.eth.contract(address=receipt.contractAddress, abi=abi)
        yield {"rpc": rpc, "w3": w3, "contract": contract}
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def store(monkeypatch, chain):
    """In-memory replacement for src.db.usage_anchors + live-chain Config."""
    from src.config.config import Config
    from src.db import usage_anchors as db

    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    rows = [
        {
            "id": 1000 + i,
            "user_id": 7 if i % 2 else 8,
            "created_at": f"{yesterday.isoformat()}T{10 + i:02d}:00:00+00:00",
            "amount": f"-{0.001 * (i + 1):.6f}",
            "metadata": {
                "model": "anthropic/claude-haiku-4-5",
                "prompt_tokens": 10 * i,
                "completion_tokens": 5 * i,
            },
        }
        for i in range(5)
    ]
    anchors: dict[tuple, dict] = {}

    def fetch(day):
        return [r for r in rows if r["created_at"].startswith(day.isoformat())]

    def get_row(ledger_id):
        return next((r for r in rows if r["id"] == int(ledger_id)), None)

    def get_anchor(chain_id, contract_address, day):
        return anchors.get((chain_id, contract_address.lower(), day.isoformat()))

    def upsert(chain_id, contract_address, day, **fields):
        key = (chain_id, contract_address.lower(), day.isoformat())
        anchors.setdefault(key, {"day": day.isoformat()}).update(fields)

    def list_(chain_id, contract_address, limit=30, statuses=None):
        out = [
            a
            for (c, addr, _), a in anchors.items()
            if c == chain_id
            and addr == contract_address.lower()
            and (not statuses or a.get("status") in statuses)
        ]
        return sorted(out, key=lambda a: a["day"], reverse=True)[:limit]

    monkeypatch.setattr(db, "fetch_usage_rows_for_day", fetch)
    monkeypatch.setattr(db, "get_usage_row", get_row)
    monkeypatch.setattr(db, "get_anchor", get_anchor)
    monkeypatch.setattr(db, "upsert_anchor", upsert)
    monkeypatch.setattr(db, "list_anchors", list_)

    monkeypatch.setattr(Config, "USAGE_ANCHOR_ENABLED", True)
    monkeypatch.setattr(Config, "USAGE_ANCHOR_CHAIN_ID", ANVIL_CHAIN_ID)
    monkeypatch.setattr(Config, "USAGE_ANCHOR_RPC_URL", chain["rpc"])
    monkeypatch.setattr(Config, "USAGE_ANCHOR_CONTRACT_ADDRESS", chain["contract"].address)
    monkeypatch.setattr(Config, "USAGE_ANCHOR_PRIVATE_KEY", ANCHORER_KEY)
    monkeypatch.setattr(Config, "USAGE_ANCHOR_SALT_KEY", "ab" * 32)
    monkeypatch.setattr(Config, "USAGE_ANCHOR_MIN_CONFIRMATIONS", 1)
    return {"rows": rows, "anchors": anchors, "day": yesterday}


def _day_index(day: date) -> int:
    return (day - date(1970, 1, 1)).days


def test_full_cycle_anchor_confirm_prove_verify(chain, store, tmp_path, capsys):
    from scripts.verify_usage_anchor import main as verify_main
    from src.services.chain import usage_anchor as ua

    contract, w3, day = chain["contract"], chain["w3"], store["day"]

    result = ua.run_usage_anchor_once(day)
    assert result["status"] in ("pending", "confirmed"), result

    # Mine past the confirmation threshold, then let the sweep settle it.
    w3.provider.make_request("anvil_mine", [3])
    ua.sweep_usage_anchors()
    row = ua.db.get_anchor(ANVIL_CHAIN_ID, contract.address, day)
    assert row["status"] == "confirmed", row

    onchain = contract.functions.rootOf(_day_index(day)).call()
    assert "0x" + onchain.hex() == row["root"]
    assert contract.functions.anchorCount().call() == 1

    # Idempotent: a second run sends nothing.
    assert ua.run_usage_anchor_once(day).get("noop") is True
    assert contract.functions.anchorCount().call() == 1

    # A user's own record proves against the chain with the offline verifier.
    proof = ua.build_proof(1003, user_id=7)
    assert proof["tree_size"] == 5 and proof["status"] == "confirmed"
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(proof))
    assert verify_main([str(path), "--rpc", chain["rpc"]]) == 0
    assert "NOT VERIFIED" not in capsys.readouterr().out

    # Someone else's record is not provable by this user.
    with pytest.raises(ua.ProofNotFoundError):
        ua.build_proof(1002, user_id=7)

    # A tampered leaf fails verification against the on-chain root.
    bad = dict(proof, leaf=proof["leaf"].replace('"tokens_in":30', '"tokens_in":31'))
    path.write_text(json.dumps(bad))
    assert verify_main([str(path), "--rpc", chain["rpc"]]) == 1


def test_changed_history_is_refused_not_reanchored(chain, store):
    from src.services.chain import usage_anchor as ua

    contract, day = chain["contract"], store["day"] - timedelta(days=1)
    for r in store["rows"]:
        r["created_at"] = r["created_at"].replace(store["day"].isoformat(), day.isoformat())

    first = ua.run_usage_anchor_once(day)
    assert first["status"] in ("pending", "confirmed")
    count = contract.functions.anchorCount().call()

    # Ledger history changes after anchoring: refuse, alert, send nothing.
    store["rows"][0]["amount"] = "-9.999999"
    store["anchors"][(ANVIL_CHAIN_ID, contract.address.lower(), day.isoformat())][
        "status"
    ] = "pending"
    result = ua.run_usage_anchor_once(day)
    assert result["status"] == "failed", result
    assert contract.functions.anchorCount().call() == count
