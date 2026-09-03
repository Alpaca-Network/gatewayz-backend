"""Regression: wallet linked-accounts carry a 0x address, not an email.

gatewayz-frontend#1010 started sending Privy wallet/smart_wallet linked
accounts in the /auth payload; PrivyLinkedAccount.address was validated as an
email for every account type, which 422'd every login for users with an
embedded wallet (production incident, 2026-09-03).
"""

import pytest
from pydantic import ValidationError

from src.schemas.auth import PrivyLinkedAccount


def test_wallet_address_is_not_email_validated():
    acct = PrivyLinkedAccount(
        type="wallet",
        address="0xA90CC1b5f433305BF6Aa17a969d697ae436684F4",
        chainType="ethereum",
        walletClientType="privy",
    )
    assert acct.address == "0xA90CC1b5f433305BF6Aa17a969d697ae436684F4"
    assert acct.chain_type == "ethereum"
    assert acct.wallet_client_type == "privy"


def test_email_field_is_still_validated():
    with pytest.raises(ValidationError):
        PrivyLinkedAccount(type="email", email="not-an-email")
