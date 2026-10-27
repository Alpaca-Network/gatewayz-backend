"""RFC 6962 Merkle tree over SHA-256 (the Certificate Transparency tree).

Used by daily usage anchoring (src/services/chain/usage_anchor.py): the
root of one UTC day's usage leaves is written on-chain, and a user can
later be handed an inclusion proof for their own ledger row.

    leaf hash  = SHA-256(0x00 || leaf_bytes)
    node hash  = SHA-256(0x01 || left || right)
    MTH(D[n])  = node(MTH(D[0:k]), MTH(D[k:n])), k = largest power of two < n

Proof verification follows RFC 9162 section 2.1.3.2. Pure -- no I/O.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"


class MerkleError(ValueError):
    """Raised for an empty tree or an out-of-range leaf index."""


def leaf_hash(leaf: bytes) -> bytes:
    return hashlib.sha256(_LEAF_PREFIX + leaf).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


def _split(n: int) -> int:
    """Largest power of two strictly less than n (n >= 2)."""
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def _root_of_hashes(hashes: Sequence[bytes]) -> bytes:
    n = len(hashes)
    if n == 1:
        return hashes[0]
    k = _split(n)
    return node_hash(_root_of_hashes(hashes[:k]), _root_of_hashes(hashes[k:]))


def root_from_leaf_hashes(hashes: Sequence[bytes]) -> bytes:
    """Root over already-hashed leaves. Raises MerkleError when empty --
    an empty day is never anchored, so there is no empty-tree root here."""
    if not hashes:
        raise MerkleError("cannot build a Merkle root over zero leaves")
    return _root_of_hashes(list(hashes))


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    return root_from_leaf_hashes([leaf_hash(leaf) for leaf in leaves])


def _path(index: int, hashes: Sequence[bytes]) -> list[bytes]:
    n = len(hashes)
    if n == 1:
        return []
    k = _split(n)
    if index < k:
        return _path(index, hashes[:k]) + [_root_of_hashes(hashes[k:])]
    return _path(index - k, hashes[k:]) + [_root_of_hashes(hashes[:k])]


def inclusion_proof(index: int, leaves: Sequence[bytes]) -> list[bytes]:
    """Audit path for leaf `index` (RFC 6962 PATH(m, D[n])), bottom-up."""
    return inclusion_proof_from_hashes(index, [leaf_hash(leaf) for leaf in leaves])


def inclusion_proof_from_hashes(index: int, hashes: Sequence[bytes]) -> list[bytes]:
    if not 0 <= index < len(hashes):
        raise MerkleError(f"leaf index {index} out of range for tree size {len(hashes)}")
    return _path(index, list(hashes))


def root_from_proof(index: int, tree_size: int, leaf_hash_: bytes, proof: Sequence[bytes]) -> bytes:
    """Recompute the root from a leaf hash and its audit path (RFC 9162
    2.1.3.2). Raises MerkleError if the path length doesn't fit the tree."""
    if not 0 <= index < tree_size:
        raise MerkleError(f"leaf index {index} out of range for tree size {tree_size}")
    fn, sn = index, tree_size - 1
    r = leaf_hash_
    for p in proof:
        if sn == 0:
            raise MerkleError("proof is longer than the tree allows")
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            while not fn & 1 and fn != 0:
                fn >>= 1
                sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        raise MerkleError("proof is shorter than the tree requires")
    return r


def verify_inclusion(
    index: int, tree_size: int, leaf: bytes, proof: Sequence[bytes], root: bytes
) -> bool:
    """True when `leaf` sits at `index` of a tree of `tree_size` with `root`."""
    try:
        return root_from_proof(index, tree_size, leaf_hash(leaf), proof) == root
    except MerkleError:
        return False
