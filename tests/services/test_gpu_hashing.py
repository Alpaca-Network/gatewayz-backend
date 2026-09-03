"""tests/services/test_gpu_hashing.py -- canonical_json / sha256_hex stability
(spec §4 item 4). The node agent (W-E) must reproduce these hashes byte-for-
byte, so the guarantees this file locks down are: key-order independence,
whitespace independence, and cross-call determinism.
"""

from __future__ import annotations

from src.services.gpu.hashing import canonical_json, sha256_hex


def test_canonical_json_is_order_independent():
    a = {"role": "user", "content": "hi"}
    b = {"content": "hi", "role": "user"}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_has_no_incidental_whitespace():
    rendered = canonical_json({"a": 1, "b": [1, 2, 3]})
    assert " " not in rendered
    assert rendered == '{"a":1,"b":[1,2,3]}'


def test_canonical_json_stable_for_message_lists():
    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert canonical_json(messages) == canonical_json(list(messages))


def test_sha256_hex_matches_known_vector():
    # sha256("") -- a well-known test vector, proves we're not silently
    # hashing something else (e.g. repr()) under the hood.
    assert sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sha256_hex_is_deterministic():
    text = canonical_json({"role": "user", "content": "hi"})
    assert sha256_hex(text) == sha256_hex(text)


def test_sha256_hex_differs_for_different_input():
    assert sha256_hex("a") != sha256_hex("b")


def test_canonical_json_unicode_not_escaped():
    # ensure_ascii=False: identical bytes whether the node agent's JSON lib
    # escapes unicode or not is NOT guaranteed unless we pin ensure_ascii;
    # this locks the choice down explicitly.
    rendered = canonical_json({"content": "héllo"})
    assert "héllo" in rendered
    assert "\\u" not in rendered
