"""
CM-7  Plans  --  Conceptual-Model Unit Tests

Tests verify that the codebase aligns with the Conceptual Model specification
for plan tiers and credit provisioning.

Markers:
    cm_verified  -- CM claim matches the code; test should PASS.
    cm_gap       -- CM claim differs from code; test documents the gap.

NOTE: Trial-specific tests (CM-7.1, CM-7.3–CM-7.7) were removed when the
trials subsystem was cut (MVP refactor, Task 4). Plan tests (D1: plans stay)
are retained below.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# CM-7.8  Plan tiers exist: Trial, Dev, Team, Enterprise
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_plan_tiers_exist(mock_supabase):
    """get_all_plans returns plans; verify that 4 canonical tiers can be
    retrieved (Trial, Dev, Team, Enterprise).

    We mock the DB to return realistic plan data and verify the function
    properly returns all tiers with expected hierarchy (price ordering).
    """
    from src.db.plans import get_all_plans

    expected_tiers = [
        {
            "id": 1,
            "name": "Trial",
            "price_per_month": 0,
            "is_active": True,
            "daily_request_limit": 1000,
            "monthly_request_limit": 25000,
            "daily_token_limit": 500_000,
            "monthly_token_limit": 15_000_000,
        },
        {
            "id": 2,
            "name": "Dev",
            "price_per_month": 29,
            "is_active": True,
            "daily_request_limit": 5000,
            "monthly_request_limit": 100000,
            "daily_token_limit": 2_000_000,
            "monthly_token_limit": 60_000_000,
        },
        {
            "id": 3,
            "name": "Team",
            "price_per_month": 99,
            "is_active": True,
            "daily_request_limit": 25000,
            "monthly_request_limit": 500000,
            "daily_token_limit": 10_000_000,
            "monthly_token_limit": 300_000_000,
        },
        {
            "id": 4,
            "name": "Enterprise",
            "price_per_month": 499,
            "is_active": True,
            "daily_request_limit": 100000,
            "monthly_request_limit": 2000000,
            "daily_token_limit": 50_000_000,
            "monthly_token_limit": 1_500_000_000,
        },
    ]

    mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = (
        expected_tiers
    )

    plans = get_all_plans()

    # Verify all 4 tiers returned
    tier_names = [p["name"] for p in plans]
    assert "Trial" in tier_names
    assert "Dev" in tier_names
    assert "Team" in tier_names
    assert "Enterprise" in tier_names
    assert len(plans) == 4

    # Verify plans come back ordered by price (ascending) as requested via .order()
    prices = [p["price_per_month"] for p in plans]
    assert prices == sorted(prices), "Plans should be ordered by price ascending"

    # Verify tier hierarchy: each tier has higher limits than the previous
    for i in range(1, len(plans)):
        assert (
            plans[i]["daily_request_limit"] > plans[i - 1]["daily_request_limit"]
        ), f"{plans[i]['name']} daily_request_limit should exceed {plans[i-1]['name']}"


# ---------------------------------------------------------------------------
# CM-7.9  Team has higher rate limits than Dev
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_team_has_higher_rate_limits_than_dev(mock_supabase):
    """Team plan limits exceed Dev plan limits when queried via check_plan_entitlements.

    We mock get_user_plan to return Dev for user 1 and Team for user 2,
    then call check_plan_entitlements for each and compare the limits.
    """
    from src.db.plans import check_plan_entitlements

    dev_plan_data = {
        "user_plan_id": 10,
        "user_id": 1,
        "plan_id": 2,
        "plan_name": "Dev",
        "plan_description": "",
        "daily_request_limit": 5000,
        "monthly_request_limit": 100000,
        "daily_token_limit": 2_000_000,
        "monthly_token_limit": 60_000_000,
        "price_per_month": 29,
        "features": ["basic_models", "standard_support"],
        "start_date": datetime.now(UTC).isoformat(),
        "end_date": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "is_active": True,
    }

    team_plan_data = {
        "user_plan_id": 20,
        "user_id": 2,
        "plan_id": 3,
        "plan_name": "Team",
        "plan_description": "",
        "daily_request_limit": 25000,
        "monthly_request_limit": 500000,
        "daily_token_limit": 10_000_000,
        "monthly_token_limit": 300_000_000,
        "price_per_month": 99,
        "features": ["basic_models", "premium_models", "priority_support"],
        "start_date": datetime.now(UTC).isoformat(),
        "end_date": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "is_active": True,
    }

    with (
        patch("src.db.plans.is_admin_tier_user", return_value=False),
        patch("src.db.plans.get_user_plan") as mock_get_plan,
    ):
        # First call for Dev user, second for Team user
        mock_get_plan.side_effect = [dev_plan_data, team_plan_data]

        dev_entitlements = check_plan_entitlements(user_id=1)
        team_entitlements = check_plan_entitlements(user_id=2)

    assert (
        team_entitlements["daily_request_limit"] > dev_entitlements["daily_request_limit"]
    ), "Team daily request limit should exceed Dev"
    assert (
        team_entitlements["monthly_request_limit"] > dev_entitlements["monthly_request_limit"]
    ), "Team monthly request limit should exceed Dev"
    assert (
        team_entitlements["daily_token_limit"] > dev_entitlements["daily_token_limit"]
    ), "Team daily token limit should exceed Dev"
    assert (
        team_entitlements["monthly_token_limit"] > dev_entitlements["monthly_token_limit"]
    ), "Team monthly token limit should exceed Dev"


# ---------------------------------------------------------------------------
# CM-7.10  Purchased credits survive plan change
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_purchased_credits_survive_plan_change(mock_supabase):
    """assign_user_plan changes the plan but does NOT modify the user's
    credit balance.  Credits are stored in the users table, and
    assign_user_plan only updates subscription_status -- never credits.
    """
    from src.db.plans import assign_user_plan

    # Set up: user exists with $42.50 credits
    user_credits_before = 42.50

    # Mock plan lookup
    plan_data = {
        "id": 3,
        "name": "Team",
        "price_per_month": 99,
        "is_active": True,
        "daily_request_limit": 25000,
        "monthly_request_limit": 500000,
        "daily_token_limit": 10_000_000,
        "monthly_token_limit": 300_000_000,
        "features": ["basic_models", "premium_models"],
    }

    # get_plan_by_id mock
    plan_lookup_result = MagicMock()
    plan_lookup_result.data = [plan_data]

    # Deactivate existing plans mock
    deactivate_result = MagicMock()
    deactivate_result.data = []

    # Insert new user_plan mock
    insert_result = MagicMock()
    insert_result.data = [{"id": 99, "user_id": 1, "plan_id": 3, "is_active": True}]

    # Update subscription status mock
    update_status_result = MagicMock()
    update_status_result.data = [{"id": 1, "subscription_status": "active"}]

    # Chain the table mock to return appropriate results
    table_mock = MagicMock()
    table_mock.select.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.update.return_value = table_mock
    table_mock.insert.return_value = table_mock

    call_count = {"n": 0}
    original_execute = table_mock.execute

    def execute_side_effect():
        call_count["n"] += 1
        n = call_count["n"]
        if n == 1:
            return plan_lookup_result  # get_plan_by_id
        elif n == 2:
            return deactivate_result  # deactivate old plans
        elif n == 3:
            return insert_result  # insert new plan
        elif n == 4:
            return update_status_result  # update subscription_status
        return MagicMock(data=[])

    table_mock.execute.side_effect = execute_side_effect
    mock_supabase.table.return_value = table_mock

    result = assign_user_plan(user_id=1, plan_id=3)
    assert result is True

    # Verify that no call updated the credits column.
    # Inspect all update() calls to ensure none touched 'credits'.
    for call in table_mock.update.call_args_list:
        update_payload = call[0][0] if call[0] else call[1].get("data", {})
        assert (
            "credits" not in update_payload
        ), "assign_user_plan must not modify credits during plan change"
