#!/usr/bin/env python3
"""
Seed the testing database with sample data for staging environment testing.

Usage:
    python scripts/database/seed_test_data.py

Environment variables required:
    - SUPABASE_URL: Testing Supabase project URL
    - SUPABASE_KEY: Testing Supabase service role key
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config.supabase_config import get_supabase_client
from src.security.security import hash_api_key, encrypt_api_key


async def seed_test_users():
    """Create test users with various plans and credit balances."""
    supabase = get_supabase_client()

    test_users = [
        {
            "email": "test-free@gatewayz.ai",
            "name": "Free Plan User",
            "plan": "free",
            "credits": 100.0,
            "is_active": True,
        },
        {
            "email": "test-starter@gatewayz.ai",
            "name": "Starter Plan User",
            "plan": "starter",
            "credits": 1000.0,
            "is_active": True,
        },
        {
            "email": "test-pro@gatewayz.ai",
            "name": "Pro Plan User",
            "plan": "pro",
            "credits": 10000.0,
            "is_active": True,
        },
        {
            "email": "test-enterprise@gatewayz.ai",
            "name": "Enterprise User",
            "plan": "enterprise",
            "credits": 100000.0,
            "is_active": True,
        },
        {
            "email": "test-suspended@gatewayz.ai",
            "name": "Suspended User",
            "plan": "free",
            "credits": 0.0,
            "is_active": False,
        },
    ]

    print("🌱 Seeding test users...")
    created_users = []

    for user_data in test_users:
        try:
            # Check if user already exists
            existing = supabase.table("users").select("*").eq("email", user_data["email"]).execute()

            if existing.data:
                print(f"  ⚠️  User {user_data['email']} already exists, skipping...")
                created_users.append(existing.data[0])
                continue

            # Create user
            result = supabase.table("users").insert(user_data).execute()

            if result.data:
                user = result.data[0]
                created_users.append(user)
                print(f"  ✅ Created user: {user_data['email']} (Plan: {user_data['plan']}, Credits: {user_data['credits']})")
        except Exception as e:
            print(f"  ❌ Error creating user {user_data['email']}: {e}")

    return created_users


async def seed_test_api_keys(users):
    """Create test API keys for users."""
    supabase = get_supabase_client()

    print("\n🔑 Seeding test API keys...")

    test_keys = {
        "test-free@gatewayz.ai": "gw_test_free_key_12345",
        "test-starter@gatewayz.ai": "gw_test_starter_key_12345",
        "test-pro@gatewayz.ai": "gw_test_pro_key_12345",
        "test-enterprise@gatewayz.ai": "gw_test_enterprise_key_12345",
    }

    for user in users:
        if user["email"] not in test_keys:
            continue

        raw_key = test_keys[user["email"]]

        try:
            # Check if key already exists
            existing = supabase.table("api_keys").select("*").eq("user_id", user["id"]).execute()

            if existing.data:
                print(f"  ⚠️  API key for {user['email']} already exists, skipping...")
                continue

            # Encrypt and hash the key
            encrypted_key = encrypt_api_key(raw_key)
            key_hash = hash_api_key(raw_key)

            key_data = {
                "user_id": user["id"],
                "key_hash": key_hash,
                "encrypted_key": encrypted_key,
                "name": f"Test Key - {user['name']}",
                "is_active": True,
                "created_at": datetime.utcnow().isoformat(),
            }

            result = supabase.table("api_keys").insert(key_data).execute()

            if result.data:
                print(f"  ✅ Created API key for {user['email']}")
                print(f"     Raw key: {raw_key}")
        except Exception as e:
            print(f"  ❌ Error creating API key for {user['email']}: {e}")


async def seed_test_plans():
    """Create test subscription plans."""
    supabase = get_supabase_client()

    print("\n💳 Seeding test plans...")

    plans = [
        {
            "name": "Free",
            "slug": "free",
            "price": 0.0,
            "credits": 100.0,
            "rate_limit": 10,
            "features": ["Basic API access", "Community support"],
            "is_active": True,
        },
        {
            "name": "Starter",
            "slug": "starter",
            "price": 9.99,
            "credits": 1000.0,
            "rate_limit": 100,
            "features": ["Standard API access", "Email support", "Usage analytics"],
            "is_active": True,
        },
        {
            "name": "Pro",
            "slug": "pro",
            "price": 49.99,
            "credits": 10000.0,
            "rate_limit": 1000,
            "features": [
                "Priority API access",
                "Priority support",
                "Advanced analytics",
                "Custom integrations",
            ],
            "is_active": True,
        },
        {
            "name": "Enterprise",
            "slug": "enterprise",
            "price": 499.99,
            "credits": 100000.0,
            "rate_limit": 10000,
            "features": [
                "Dedicated infrastructure",
                "24/7 support",
                "Custom SLA",
                "Advanced security",
                "Dedicated account manager",
            ],
            "is_active": True,
        },
    ]

    for plan_data in plans:
        try:
            # Check if plan exists
            existing = supabase.table("plans").select("*").eq("slug", plan_data["slug"]).execute()

            if existing.data:
                print(f"  ⚠️  Plan {plan_data['name']} already exists, skipping...")
                continue

            result = supabase.table("plans").insert(plan_data).execute()

            if result.data:
                print(f"  ✅ Created plan: {plan_data['name']} (${plan_data['price']}/mo)")
        except Exception as e:
            print(f"  ❌ Error creating plan {plan_data['name']}: {e}")


async def seed_test_transactions(users):
    """Create test credit transactions."""
    supabase = get_supabase_client()

    print("\n💰 Seeding test transactions...")

    # Create some sample transactions for the pro user
    pro_user = next((u for u in users if u["email"] == "test-pro@gatewayz.ai"), None)

    if not pro_user:
        print("  ⚠️  Pro user not found, skipping transactions")
        return

    transactions = [
        {
            "user_id": pro_user["id"],
            "amount": -1.5,
            "model": "gpt-4",
            "provider": "openrouter",
            "tokens_used": 1500,
            "transaction_type": "inference",
            "created_at": (datetime.utcnow() - timedelta(days=2)).isoformat(),
        },
        {
            "user_id": pro_user["id"],
            "amount": -0.5,
            "model": "claude-3-haiku",
            "provider": "anthropic",
            "tokens_used": 500,
            "transaction_type": "inference",
            "created_at": (datetime.utcnow() - timedelta(days=1)).isoformat(),
        },
        {
            "user_id": pro_user["id"],
            "amount": 10.0,
            "transaction_type": "credit_purchase",
            "created_at": datetime.utcnow().isoformat(),
        },
    ]

    for txn_data in transactions:
        try:
            result = supabase.table("credit_transactions").insert(txn_data).execute()

            if result.data:
                print(f"  ✅ Created transaction: {txn_data['transaction_type']} ({txn_data.get('amount', 0)} credits)")
        except Exception as e:
            print(f"  ❌ Error creating transaction: {e}")


async def seed_test_coupons():
    """Create test coupon codes."""
    supabase = get_supabase_client()

    print("\n🎟️  Seeding test coupons...")

    coupons = [
        {
            "code": "TEST10",
            "discount_percent": 10.0,
            "max_uses": 100,
            "uses_count": 0,
            "expires_at": (datetime.utcnow() + timedelta(days=30)).isoformat(),
            "is_active": True,
        },
        {
            "code": "TEST50",
            "discount_percent": 50.0,
            "max_uses": 10,
            "uses_count": 0,
            "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
            "is_active": True,
        },
        {
            "code": "TESTFREE",
            "discount_percent": 100.0,
            "max_uses": 5,
            "uses_count": 0,
            "expires_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
            "is_active": True,
        },
    ]

    for coupon_data in coupons:
        try:
            # Check if coupon exists
            existing = supabase.table("coupons").select("*").eq("code", coupon_data["code"]).execute()

            if existing.data:
                print(f"  ⚠️  Coupon {coupon_data['code']} already exists, skipping...")
                continue

            result = supabase.table("coupons").insert(coupon_data).execute()

            if result.data:
                print(f"  ✅ Created coupon: {coupon_data['code']} ({coupon_data['discount_percent']}% off)")
        except Exception as e:
            print(f"  ❌ Error creating coupon {coupon_data['code']}: {e}")


async def main():
    """Main seeding function."""
    print("=" * 60)
    print("🌱 SEEDING TEST DATA FOR STAGING ENVIRONMENT")
    print("=" * 60)
    print()

    # Verify we're in a test environment
    app_env = os.getenv("APP_ENV", "development")

    if app_env == "production":
        print("❌ ERROR: Cannot seed test data in production environment!")
        print("   Set APP_ENV=staging or APP_ENV=development")
        sys.exit(1)

    print(f"Environment: {app_env}")
    print(f"Supabase URL: {os.getenv('SUPABASE_URL')}")
    print()

    try:
        # Seed in order (dependencies)
        users = await seed_test_users()
        await seed_test_api_keys(users)
        await seed_test_plans()
        await seed_test_transactions(users)
        await seed_test_coupons()

        print()
        print("=" * 60)
        print("✅ TEST DATA SEEDING COMPLETE!")
        print("=" * 60)
        print()
        print("Test API Keys:")
        print("  Free:       gw_test_free_key_12345")
        print("  Starter:    gw_test_starter_key_12345")
        print("  Pro:        gw_test_pro_key_12345")
        print("  Enterprise: gw_test_enterprise_key_12345")
        print()
        print("Test Coupons:")
        print("  TEST10:     10% off")
        print("  TEST50:     50% off")
        print("  TESTFREE:   100% off (free)")
        print()

    except Exception as e:
        print()
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
