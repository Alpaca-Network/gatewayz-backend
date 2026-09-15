"""Ed25519 signatures over usage exports, so a third party can verify them offline.

FlashyOS's compute-attribution plan asks for "a signed, verifiable usage feed
the transparency log can fold in". The point of a signature here is that the
consumer does not have to trust the transport, the dashboard, or us: they fetch
the public key once and check the bytes themselves, forever after.

Two rules this module exists to enforce:

1. THE SIGNATURE COVERS THE BYTES THAT ARE SERVED. Not a re-serialization of an
   equivalent object -- the exact string in the response. Sign one form and
   serve another and the signature verifies nothing, however correct the
   crypto. SafeCommit serves its charter's own bytes for the same reason.

2. NO KEY MEANS NO EXPORT. An unsigned export that looks signed is worse than
   no export: the consumer's verification step silently becomes a no-op. The
   caller gets an explicit failure instead.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ALG = "ed25519"
ENV_KEY = "USAGE_SIGNING_KEY"


class SigningUnavailable(RuntimeError):
    """No usable signing key is configured."""


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The one serialization that gets signed AND served.

    Sorted keys, no incidental whitespace, UTF-8. `allow_nan=False` because a
    NaN would serialize to a token no other JSON parser accepts, producing a
    document that verifies here and fails everywhere else.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _private_key() -> ed25519.Ed25519PrivateKey:
    raw = (os.environ.get(ENV_KEY) or "").strip()
    if not raw:
        raise SigningUnavailable(f"{ENV_KEY} is not set")
    try:
        seed = base64.b64decode(raw, validate=True)
    except Exception as e:  # noqa: BLE001
        raise SigningUnavailable(f"{ENV_KEY} is not valid base64") from e
    if len(seed) != 32:
        raise SigningUnavailable(f"{ENV_KEY} must decode to 32 bytes, got {len(seed)}")
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed)


def public_key_b64() -> str:
    """The verifying key, base64. Safe to publish -- that is its whole job."""
    pub = (
        _private_key()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    return base64.b64encode(pub).decode()


def key_id() -> str:
    """A short, stable name for the key, so a rotation is visible in the feed."""
    pub = base64.b64decode(public_key_b64())
    return hashlib.sha256(pub).hexdigest()[:16]


def sign(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    """Sign `payload`; return the exact bytes to serve, plus the envelope.

    The bytes come back with the envelope deliberately: a caller that
    re-serializes the dict to build the response has broken the signature
    without any error appearing.
    """
    body = canonical_bytes(payload)
    signature = _private_key().sign(body)
    return body, {
        "alg": ALG,
        "key_id": key_id(),
        "signature": base64.b64encode(signature).decode(),
    }


def verify(body: bytes, signature_b64: str, public_b64: str) -> bool:
    """Offline verification. The same check a consumer runs, so it is tested."""
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
        pub.verify(base64.b64decode(signature_b64), body)
        return True
    except Exception:  # noqa: BLE001 - any failure is a failed verification
        return False
