#!/usr/bin/env python3
"""Query users from @rccg-clf.org domain and analyze their usage."""

import os
import sys
from datetime import datetime, timezone

# Add the parent directory to the path to import from src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.supabase_config import get_supabase_client


def query_rccg_users():
    """Query all users with @rccg-clf.org email domain."""
    client = get_supabase_client()

    # Query users with rccg-clf.org domain
    result = client.table("users").select("*").ilike("email", "%@rccg-clf.org").execute()

    users = result.data
    print(f"\n{'='*80}")
    print(f"Found {len(users)} users from @rccg-clf.org domain")
    print(f"{'='*80}\n")

    if not users:
        print("No users found with @rccg-clf.org email domain.")
        return

    # Sort by registration date (newest first)
    # Handle null registration dates by placing them at the end
    users.sort(key=lambda x: x.get('registration_date') or '', reverse=True)

    total_credits_used = 0
    total_credits_remaining = 0

    for i, user in enumerate(users, 1):
        user_id = user.get('id')
        email = user.get('email')
        username = user.get('username', 'N/A')
        credits = user.get('credits', 0)
        is_active = user.get('is_active', False)
        subscription_status = user.get('subscription_status', 'N/A')
        tier = user.get('tier', 'N/A')
        registration_date = user.get('registration_date', 'N/A')

        total_credits_remaining += credits

        print(f"\n{i}. User ID: {user_id}")
        print(f"   Email: {email}")
        print(f"   Username: {username}")
        print(f"   Credits: ${credits:.2f}")
        print(f"   Status: {'Active' if is_active else 'Inactive'}")
        print(f"   Subscription: {subscription_status}")
        print(f"   Tier: {tier}")
        print(f"   Registered: {registration_date}")

        # Get credit transactions for this user
        try:
            transactions = client.table("credit_transactions").select("*").eq("user_id", user_id).execute()
            if transactions.data:
                print(f"   Total Transactions: {len(transactions.data)}")

                # Calculate total credits used
                credits_used = sum(
                    abs(t.get('amount', 0))
                    for t in transactions.data
                    if t.get('amount', 0) < 0
                )
                total_credits_used += credits_used

                if credits_used > 0:
                    print(f"   Credits Used: ${credits_used:.4f}")

                # Show recent transactions
                recent_transactions = sorted(
                    transactions.data,
                    key=lambda x: x.get('created_at', ''),
                    reverse=True
                )[:5]

                if recent_transactions:
                    print(f"   Recent Transactions:")
                    for txn in recent_transactions:
                        amount = txn.get('amount', 0)
                        description = txn.get('description', 'N/A')
                        created_at = txn.get('created_at', 'N/A')
                        print(f"     - {created_at}: ${amount:.4f} - {description}")
            else:
                print(f"   No transactions found")
        except Exception as e:
            print(f"   Error fetching transactions: {e}")

        # Get activity logs for this user
        try:
            activity = client.table("activity_log").select("*").eq("user_id", user_id).execute()
            if activity.data:
                print(f"   Total Activity Logs: {len(activity.data)}")

                # Count by action type
                action_counts = {}
                for log in activity.data:
                    action = log.get('action', 'unknown')
                    action_counts[action] = action_counts.get(action, 0) + 1

                if action_counts:
                    print(f"   Activity Breakdown:")
                    for action, count in sorted(action_counts.items(), key=lambda x: x[1], reverse=True):
                        print(f"     - {action}: {count}")
        except Exception as e:
            print(f"   Error fetching activity: {e}")

    print(f"\n{'='*80}")
    print(f"Summary Statistics")
    print(f"{'='*80}")
    print(f"Total Users: {len(users)}")
    print(f"Total Credits Remaining: ${total_credits_remaining:.2f}")
    print(f"Total Credits Used: ${total_credits_used:.4f}")
    print(f"Total Credits Allocated: ${total_credits_remaining + total_credits_used:.4f}")

    active_users = sum(1 for u in users if u.get('is_active'))
    print(f"Active Users: {active_users}/{len(users)}")

    # Subscription breakdown
    sub_breakdown = {}
    for u in users:
        status = u.get('subscription_status', 'N/A')
        sub_breakdown[status] = sub_breakdown.get(status, 0) + 1

    print(f"\nSubscription Status Breakdown:")
    for status, count in sorted(sub_breakdown.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {status}: {count}")

    # Tier breakdown
    tier_breakdown = {}
    for u in users:
        tier = u.get('tier', 'N/A')
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1

    print(f"\nTier Breakdown:")
    for tier, count in sorted(tier_breakdown.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {tier}: {count}")

    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    query_rccg_users()
