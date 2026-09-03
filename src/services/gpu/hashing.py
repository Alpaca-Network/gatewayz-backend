"""Canonical hashing for community-provider work receipts (spec §4, §2).

``provider_work`` never stores prompt/response content (threat model G3) --
only a hash of each, so a later spot-check (W-B) can prove a node served
what it claims without Gatewayz retaining the content itself.

CROSS-REPO CONTRACT: the gateway and the node agent
(``scripts/gpu_node_agent.py``, W-E -- its own ``_canonical_json``/
``hash_prompt``/``hash_response``) must derive byte-identical hashes from
the same messages/response, or attestation signatures silently stop
verifying. This module (mirrored in ``docs/gpu/attestation.md``) is the
single definition of "canonical": sorted keys, no incidental whitespace,
UTF-8, and **``ensure_ascii=True`` (json.dumps' default)** -- non-ASCII
characters are ``\\uXXXX``-escaped, matching the node agent's
``json.dumps(obj, sort_keys=True, separators=(",", ":"))`` exactly (it does
not pass ``ensure_ascii``, so it gets the default). Do not add
``ensure_ascii=False`` here even though it reads more naturally -- the two
sides would then hash any non-ASCII prompt/response differently.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def canonical_json(value: Any) -> str:
    """Render *value* as a stable JSON string: sorted keys, no whitespace,
    non-ASCII escaped (module docstring's cross-repo contract).

    Two calls with the same logical content (regardless of dict key order)
    always produce the same string, so ``sha256_hex(canonical_json(...))``
    is stable across processes/languages that follow the same rule.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    """Hex-encoded SHA-256 digest of *text* (UTF-8)."""
    return sha256(text.encode("utf-8")).hexdigest()


def hash_prompt(messages: list[dict[str, Any]]) -> str:
    """sha256 hex digest of the canonical JSON of a request's ``messages``.

    Same name and behavior as ``scripts/gpu_node_agent.py``'s
    ``hash_prompt`` -- see the cross-repo contract in this module's
    docstring. Prefer this (and ``hash_response``) over calling
    ``canonical_json``/``sha256_hex`` directly for prompt/response hashing,
    so both sides of the contract read identically.
    """
    return sha256_hex(canonical_json(messages))


def hash_response(response_text: str) -> str:
    """sha256 hex digest of the raw response text (not re-encoded as JSON --
    the response IS text). Same name and behavior as
    ``scripts/gpu_node_agent.py``'s ``hash_response``.
    """
    return sha256_hex(response_text)
