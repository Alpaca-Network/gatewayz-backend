"""tests/services/test_gpu_hashing.py -- canonical_json / sha256_hex stability
(spec §4 item 4). The node agent (W-E, ``scripts/gpu_node_agent.py``) must
reproduce these hashes byte-for-byte, so the guarantees this file locks
down are: key-order independence, whitespace independence, ASCII-escaping
(matching the node agent's un-pinned ``ensure_ascii`` default), and
cross-call determinism. The parity block at the bottom vendors the node
agent's own hand-computed values (its
``tests/scripts/test_gpu_node_agent.py`` does the same in reverse) so a
divergence on either side fails a test instead of silently breaking every
attestation signature.
"""

from __future__ import annotations

import hashlib
import json

from src.services.gpu.hashing import canonical_json, hash_prompt, hash_response, sha256_hex


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


def test_canonical_json_unicode_is_escaped():
    # Cross-repo contract (see module docstring): the node agent's
    # json.dumps(..., sort_keys=True, separators=(",", ":")) does NOT pass
    # ensure_ascii, so it gets json.dumps' default (True) -- non-ASCII is
    # \uXXXX-escaped. This side must match that exactly, or a prompt with
    # any non-ASCII character hashes differently on the two sides.
    rendered = canonical_json({"content": "héllo"})
    assert "héllo" not in rendered
    assert "\\u00e9" in rendered  # é


# --- Cross-repo parity with scripts/gpu_node_agent.py (W-E) -----------------
# Hand-computed independently of both implementations, using stdlib
# json.dumps/hashlib directly -- mirrors the vendored-expected-values
# pattern in the node agent's own tests/scripts/test_gpu_node_agent.py
# (which does the same thing in reverse, since this module didn't exist yet
# when that PR was written). If either side's canonicalisation drifts, one
# of the two test files (this one or theirs) fails.


def _hand_computed_canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def test_hash_prompt_matches_hand_computed_canonical_json_sha256():
    messages = [{"role": "user", "content": "hi"}]
    expected = hashlib.sha256(_hand_computed_canonical_json(messages).encode("utf-8")).hexdigest()
    assert hash_prompt(messages) == expected


def test_hash_prompt_is_key_order_independent():
    a = [{"role": "user", "content": "hi"}]
    b = [{"content": "hi", "role": "user"}]
    assert hash_prompt(a) == hash_prompt(b)


def test_hash_response_matches_hand_computed_sha256_of_raw_text():
    text = "The answer is 42."
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert hash_response(text) == expected


def test_hash_prompt_and_hash_response_are_thin_wrappers():
    # hash_prompt/hash_response must stay byte-identical to composing
    # canonical_json + sha256_hex directly -- community_adapter.py and any
    # future caller can use either spelling interchangeably.
    messages = [{"role": "user", "content": "hi"}]
    assert hash_prompt(messages) == sha256_hex(canonical_json(messages))
    assert hash_response("hello!") == sha256_hex("hello!")
