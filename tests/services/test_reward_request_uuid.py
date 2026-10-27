"""Reward grants must hand the credit ledger a real, deterministic UUID.

credit_transactions.request_id and atomic_add_credits.p_request_id are UUID
columns; a readable key like "holdings_reward:<wallet>:<date>" is rejected and
drops the grant onto the non-idempotent legacy path.
"""

import uuid

from src.db.credit_transactions import reward_request_uuid
from src.services import staking_rewards
from src.services.holdings import rewards as holdings_rewards


def test_reward_request_uuid_is_a_valid_uuid():
    value = reward_request_uuid("holdings_reward:0xabc:2026-10-12")
    assert str(uuid.UUID(value)) == value


def test_reward_request_uuid_is_deterministic_and_key_specific():
    a = reward_request_uuid("holdings_reward:0xabc:2026-10-12")
    assert a == reward_request_uuid("holdings_reward:0xabc:2026-10-12")
    assert a != reward_request_uuid("holdings_reward:0xabc:2026-10-13")
    assert a != reward_request_uuid("staking_reward:0xabc:2026-10-12")


def test_holdings_and_staking_keys_are_uuids():
    for value in (
        holdings_rewards._request_id("0xabc", "2026-10-12"),
        staking_rewards._staking_request_id("0xabc", "2026-10-12"),
    ):
        uuid.UUID(value)
