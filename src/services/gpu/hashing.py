"""Canonical hashing for community-provider work receipts (spec §4, §2).

``provider_work`` never stores prompt/response content (threat model G3) --
only a hash of each, so a later spot-check (W-B) can prove a node served
what it claims without Gatewayz retaining the content itself. Both the
gateway and the node agent (``scripts/gpu_node_agent.py``, W-E) must derive
byte-identical hashes from the same messages/response, so this module (and
its mirror documented in ``docs/gpu/attestation.md``) is the single
definition of "canonical" -- sorted keys, no incidental whitespace, UTF-8.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def canonical_json(value: Any) -> str:
    """Render *value* as a stable JSON string: sorted keys, no whitespace.

    Two calls with the same logical content (regardless of dict key order)
    always produce the same string, so ``sha256_hex(canonical_json(...))``
    is stable across processes/languages that follow the same rule.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    """Hex-encoded SHA-256 digest of *text* (UTF-8)."""
    return sha256(text.encode("utf-8")).hexdigest()
