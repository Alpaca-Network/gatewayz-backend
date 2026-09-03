"""EOA wallet-signature verification (gatewayz-backend#2249).

Wraps `eth_account.Account.recover_message` behind a single function so the
faucet and SIWE wallet auth share ONE call site into eth_account -- the
pattern that let three real bugs (get_logs kwargs, HexBytes.hex() losing
its 0x prefix, an unverified sign_message().signature) ship past mocked
tests earlier this project. Every caller into eth_account gets its own
test using the REAL library (see tests/security/test_wallet_signature.py).

EOA (externally-owned account) signatures only. Smart-contract wallets
(EIP-1271, `isValidSignature`) are a non-goal for Milestone 2 -- this
function is the extension point: a future EIP-1271 path would live here,
behind the same `verify_wallet_signature` signature, without touching any
route.
"""

import logging

from eth_account import Account
from eth_account.messages import encode_defunct

logger = logging.getLogger(__name__)


def verify_wallet_signature(address: str, message: str, signature: str) -> bool:
    """Return True iff `signature` is a valid EOA signature of `message` by `address`.

    Never raises -- every eth_account/eth_utils failure mode (malformed
    signature, wrong length, bad hex, etc.) is caught and mapped to False,
    same as the faucet's existing verification path.
    """
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as e:
        logger.info("Wallet signature recovery failed: %s", e)
        return False
    return recovered.lower() == address.lower()
