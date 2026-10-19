"""Tests for src.services.chain.merkle -- RFC 6962 SHA-256 Merkle tree used
by daily usage anchoring."""

import hashlib

import pytest

from src.services.chain.merkle import (
    MerkleError,
    inclusion_proof,
    leaf_hash,
    merkle_root,
    root_from_proof,
    verify_inclusion,
)


@pytest.fixture
def sb():
    """No-op fixture whose presence bypasses the autouse DB-skip in
    tests/conftest.py -- pure unit tests."""
    return None


# Certificate Transparency reference vectors (RFC 6962 test data used by
# google/certificate-transparency and transparency-dev/merkle): the root of
# the first n of these leaves.
_CT_LEAVES = [
    bytes.fromhex(h)
    for h in [
        "",
        "00",
        "10",
        "2021",
        "3031",
        "40414243",
        "5051525354555657",
        "606162636465666768696a6b6c6d6e6f",
    ]
]
_CT_ROOTS = [
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]


def _naive_root(leaves):
    """Independent, deliberately simple RFC 6962 definition: recursive split
    at the largest power of two below n, computed from scratch."""
    if len(leaves) == 1:
        return hashlib.sha256(b"\x00" + leaves[0]).digest()
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    left, right = _naive_root(leaves[:k]), _naive_root(leaves[k:])
    return hashlib.sha256(b"\x01" + left + right).digest()


def _leaves(n):
    return [f"leaf-{i}".encode() for i in range(n)]


@pytest.mark.parametrize("n", range(1, len(_CT_LEAVES) + 1))
def test_root_matches_ct_reference_vectors(sb, n):
    assert merkle_root(_CT_LEAVES[:n]).hex() == _CT_ROOTS[n - 1]


def test_single_empty_leaf_is_sha256_of_zero_byte(sb):
    assert leaf_hash(b"") == hashlib.sha256(b"\x00").digest()


@pytest.mark.parametrize("n", range(1, 18))
def test_root_matches_naive_implementation(sb, n):
    assert merkle_root(_leaves(n)) == _naive_root(_leaves(n))


@pytest.mark.parametrize("n", range(1, 18))
def test_every_inclusion_proof_verifies(sb, n):
    leaves = _leaves(n)
    root = merkle_root(leaves)
    for i, leaf in enumerate(leaves):
        proof = inclusion_proof(i, leaves)
        assert root_from_proof(i, n, leaf_hash(leaf), proof) == root
        assert verify_inclusion(i, n, leaf, proof, root)


@pytest.mark.parametrize("n", range(2, 18))
def test_proof_rejects_wrong_leaf_index_or_root(sb, n):
    leaves = _leaves(n)
    root = merkle_root(leaves)
    proof = inclusion_proof(0, leaves)
    assert not verify_inclusion(0, n, b"forged", proof, root)
    assert not verify_inclusion(1, n, leaves[0], proof, root)
    assert not verify_inclusion(0, n, leaves[0], proof, b"\x00" * 32)


def test_proof_rejects_wrong_tree_size(sb):
    leaves = _leaves(5)
    proof = inclusion_proof(4, leaves)
    root = merkle_root(leaves)
    assert not verify_inclusion(4, 6, leaves[4], proof, root)
    assert not verify_inclusion(4, 4, leaves[4], proof, root)


def test_truncated_or_extended_proof_is_rejected(sb):
    leaves = _leaves(9)
    root = merkle_root(leaves)
    proof = inclusion_proof(3, leaves)
    assert not verify_inclusion(3, 9, leaves[3], proof[:-1], root)
    assert not verify_inclusion(3, 9, leaves[3], proof + [b"\x00" * 32], root)


def test_ct_inclusion_proof_vector(sb):
    # transparency-dev/merkle: leaf 0 of the 8-leaf tree.
    proof = inclusion_proof(0, _CT_LEAVES)
    assert [p.hex() for p in proof] == [
        "96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
        "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
        "6b47aaf29ee3c2af9af889bc1fb9254dabd31177f16232dd6aab035ca39bf6e4",
    ]


def test_empty_tree_and_bad_index_raise(sb):
    with pytest.raises(MerkleError):
        merkle_root([])
    with pytest.raises(MerkleError):
        inclusion_proof(3, _leaves(3))
    with pytest.raises(MerkleError):
        root_from_proof(0, 0, b"", [])
