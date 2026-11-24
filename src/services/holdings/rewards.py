"""Deterministic, date-rotated wallet ordering for a scarce daily budget.

The holdings-rewards accrual that used to live here is retired and removed.
``budget_order`` stays because the delegated-staking jobs
(src/services/delegation/rewards.py and attribution.py) import it.
"""

from __future__ import annotations

import hashlib


def budget_order(wallets: list[str], reward_date_str: str) -> list[str]:
    """The order wallets are considered in when the global budget is scarce.

    Sorting by address alone is deterministic but permanently unfair: the
    same low addresses would win every single day the budget runs out.
    Seeding the sort with the reward date instead rotates the order daily
    while keeping it fully reproducible for any given date -- a re-run of
    one date pays exactly the wallets the first run paid.

    The address is included as a tiebreaker so two wallets that hash alike
    still order stably.
    """

    def key(address: str) -> tuple[bytes, str]:
        digest = hashlib.sha256(f"{reward_date_str}:{address}".encode()).digest()
        return digest, address

    return sorted(wallets, key=key)
