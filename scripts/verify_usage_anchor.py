#!/usr/bin/env python3
"""Offline verifier for a Gatewayz usage-anchor inclusion proof.

Standalone on purpose: standard library only, no repo imports, so anyone
can audit a proof without trusting Gatewayz code. Given the JSON returned by
GET /v1/anchors/proof/{ledger_id}, it

  1. hashes the leaf exactly as published (RFC 6962: SHA-256(0x00 || leaf)),
  2. walks the audit path to a root (RFC 9162 section 2.1.3.2), using the
     `tree_size` from the proof JSON -- the chain stores only the root, never
     the day's leaf count,
  3. checks that root against the proof's `root`, and
  4. (unless --offline) calls UsageAnchor.rootOf(day) over JSON-RPC and
     checks the chain holds the same root.

Usage:
    python scripts/verify_usage_anchor.py proof.json --rpc https://api.avax.network/ext/bc/C/rpc
    python scripts/verify_usage_anchor.py proof.json --offline

Exit code 0 = verified, 1 = mismatch, 2 = bad input / RPC error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request

# keccak256("rootOf(uint64)")[:4] -- pinned by tests/scripts/test_verify_usage_anchor.py.
ROOT_OF_SELECTOR = "9d7384d9"
DEFAULT_RPC = {
    43114: "https://api.avax.network/ext/bc/C/rpc",
    43113: "https://api.avax-test.network/ext/bc/C/rpc",
}


def _h(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def root_from_proof(index: int, tree_size: int, leaf: bytes, proof: list[bytes]) -> bytes:
    if not 0 <= index < tree_size:
        raise ValueError("leaf index out of range")
    fn, sn = index, tree_size - 1
    r = _h(b"\x00" + leaf)
    for p in proof:
        if sn == 0:
            raise ValueError("proof is longer than the tree allows")
        if fn & 1 or fn == sn:
            r = _h(b"\x01" + p + r)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = _h(b"\x01" + r + p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        raise ValueError("proof is shorter than the tree requires")
    return r


def _hex(value: str) -> bytes:
    return bytes.fromhex(value.removeprefix("0x"))


def root_of_calldata(day_index: int) -> str:
    return "0x" + ROOT_OF_SELECTOR + day_index.to_bytes(32, "big").hex()


def fetch_onchain_root(rpc_url: str, contract: str, day_index: int) -> bytes:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": contract, "data": root_of_calldata(day_index)}, "latest"],
    }
    req = urllib.request.Request(
        rpc_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - user-supplied RPC URL
        body = json.loads(resp.read())
    if "error" in body:
        raise RuntimeError(f"RPC error: {body['error']}")
    return _hex(body["result"])


def verify(proof_doc: dict, rpc_url: str | None) -> tuple[bool, list[str]]:
    lines: list[str] = []
    leaf = proof_doc["leaf"].encode("utf-8")
    path = [_hex(p) for p in proof_doc["proof"]]
    claimed = _hex(proof_doc["root"])
    try:
        computed = root_from_proof(
            int(proof_doc["leaf_index"]), int(proof_doc["tree_size"]), leaf, path
        )
    except ValueError as e:
        # The path doesn't fit the claimed tree_size: not a valid proof.
        lines.append(f"leaf -> root      MISMATCH  ({e})")
        return False, lines
    ok = computed == claimed
    lines.append(f"leaf -> root      {'OK' if ok else 'MISMATCH'}  0x{computed.hex()}")
    if not ok:
        return False, lines
    if rpc_url:
        onchain = fetch_onchain_root(
            rpc_url, proof_doc["contract_address"], int(proof_doc["day_index"])
        )
        chain_ok = onchain == claimed
        lines.append(
            f"on-chain rootOf  {'OK' if chain_ok else 'MISMATCH'}  0x{onchain.hex()} "
            f"(chain {proof_doc.get('chain_id')}, day {proof_doc.get('day')})"
        )
        ok = chain_ok
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("proof", help="proof JSON file (or - for stdin)")
    parser.add_argument("--rpc", help="C-Chain JSON-RPC URL (default: public RPC for chain_id)")
    parser.add_argument("--offline", action="store_true", help="skip the on-chain check")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.proof == "-" else open(args.proof).read()
        doc = json.loads(raw)
        doc = doc.get("data", doc)
        rpc = None if args.offline else (args.rpc or DEFAULT_RPC.get(int(doc["chain_id"])))
        if not args.offline and not rpc:
            print("No RPC URL for this chain; pass --rpc", file=sys.stderr)
            return 2
        ok, lines = verify(doc, rpc)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    print("VERIFIED" if ok else "NOT VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
