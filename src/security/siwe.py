"""SIWE (EIP-4361) message construction (gatewayz-backend#2249/#2251).

The server ALWAYS builds this message and stores it verbatim (see
src/routes/wallet_auth.py's nonce/verify pair) -- the client never gets to
choose the message contents, which is what makes the nonce flow resistant
to a signed-message-from-another-dapp replay. No `siwe` package is in
requirements.txt; this hand-builds the exact EIP-4361 layout per the M2
design spec (docs/superpowers/specs/2026-09-03-wallet-identity-auth-design.md
section 4.1) rather than adding a new dependency for one fixed-shape string.
"""

from datetime import datetime, timedelta

from eth_utils import to_checksum_address

from src.config.config import Config

# Spec section 4.1: the signed nonce/message is valid for 5 minutes.
SIWE_MESSAGE_TTL_SECONDS = 300


def build_siwe_message(
    address: str,
    nonce: str,
    chain_id: int,
    statement: str,
    issued_at: datetime,
) -> str:
    """Build the exact EIP-4361 message text the client must sign verbatim.

    `address` is checksummed (EIP-55) in the message body regardless of the
    case it was supplied in -- callers are expected to have already
    normalized/validated it via src.utils.wallet_address. `domain`/`uri`
    come from Config (SIWE_DOMAIN/SIWE_URI) per the M2 design spec section
    4.1, not from the caller, so every message this backend issues carries
    the same fixed domain/URI -- a signature bound to a different
    domain/URI (e.g. phished from another dapp) simply won't verify here.
    """
    checksummed = to_checksum_address(address)
    domain = Config.SIWE_DOMAIN
    uri = Config.SIWE_URI
    expiration_time = issued_at + timedelta(seconds=SIWE_MESSAGE_TTL_SECONDS)
    issued_at_str = issued_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    expiration_time_str = expiration_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{checksummed}\n"
        f"\n"
        f"{statement}\n"
        f"\n"
        f"URI: {uri}\n"
        f"Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at_str}\n"
        f"Expiration Time: {expiration_time_str}"
    )
