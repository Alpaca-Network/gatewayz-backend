"""Shared Ethereum wallet-address validation (gatewayz-backend#2249).

Extracted from src/routes/faucet.py's duplicated `_validate_wallet_address`
so every call site (faucet, SIWE wallet auth) applies IDENTICAL
normalization -- an address that validates on one endpoint and not another
would be a real bug, not just inconsistent style.
"""

import re

_WALLET_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def normalize_wallet_address(v: str) -> str:
    """Validate a 0x-prefixed 40-hex-char address and return it lower-cased.

    Raises ValueError on a malformed address -- the shape pydantic
    `@field_validator` methods and route handlers expect, so this function
    can be called directly from either:

        @field_validator("wallet_address")
        @classmethod
        def validate_wallet_address(cls, v):
            return normalize_wallet_address(v)
    """
    if not isinstance(v, str) or not _WALLET_ADDRESS_RE.match(v):
        raise ValueError("wallet_address must be a 0x-prefixed 40-character hex address")
    return v.lower()
