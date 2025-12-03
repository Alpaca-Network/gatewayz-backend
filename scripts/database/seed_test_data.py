#!/usr/bin/env python3
"""
Seed Test Data Script for Supabase Test Instance

This script populates a Supabase test instance with realistic mock data
for development and testing purposes.

Usage:
    # Using environment variables
    python scripts/database/seed_test_data.py

    # Using command line arguments
    python scripts/database/seed_test_data.py --url YOUR_SUPABASE_URL --key YOUR_SERVICE_KEY

    # Clear existing data first
    python scripts/database/seed_test_data.py --clear

    # Specify amount of data
    python scripts/database/seed_test_data.py --users 50 --api-keys 100
"""

import argparse
import hashlib
import hmac
import json
import os
import random
import secrets
import sys
from datetime import datetime, timedelta
from typing import Any

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

try:
    from supabase import create_client, Client
except ImportError:
    print("Error: supabase package not installed. Run: pip install supabase")
    sys.exit(1)


# Configuration
DEFAULT_SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:54321")
DEFAULT_SUPABASE_KEY = os.getenv(
    "SUPABASE_SERVICE_KEY", os.getenv("SUPABASE_KEY", "")
)

# Test data constants
TEST_MODELS = [
    ("openai/gpt-4-turbo", "OpenAI", 128000, 10.0, 30.0),
    ("openai/gpt-4o", "OpenAI", 128000, 5.0, 15.0),
    ("openai/gpt-3.5-turbo", "OpenAI", 16385, 0.5, 1.5),
    ("anthropic/claude-3-opus", "Anthropic", 200000, 15.0, 75.0),
    ("anthropic/claude-3-sonnet", "Anthropic", 200000, 3.0, 15.0),
    ("anthropic/claude-3-haiku", "Anthropic", 200000, 0.25, 1.25),
    ("google/gemini-pro", "Google", 32768, 0.5, 1.5),
    ("google/gemini-pro-vision", "Google", 16384, 0.5, 1.5),
    ("meta-llama/llama-3-70b-instruct", "Meta", 8192, 0.9, 0.9),
    ("mistralai/mistral-large", "Mistral", 32768, 4.0, 12.0),
]

TEST_PROVIDERS = [
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", True),
    ("portkey", "Portkey", "https://api.portkey.ai/v1", True),
    ("featherless", "Featherless", "https://api.featherless.ai/v1", True),
    ("deepinfra", "DeepInfra", "https://api.deepinfra.com/v1", True),
    ("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", True),
    ("together", "Together AI", "https://api.together.xyz/v1", True),
    ("huggingface", "HuggingFace", "https://api-inference.huggingface.co", True),
]

FIRST_NAMES = [
    "Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Henry",
    "Ivy", "Jack", "Kate", "Leo", "Maya", "Noah", "Olivia", "Paul",
    "Quinn", "Rose", "Sam", "Tina", "Uma", "Victor", "Wendy", "Xavier", "Yara", "Zack"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson"
]

APPS = ["ChatGPT Clone", "Code Assistant", "Writing Helper", "API Gateway", "Research Tool"]


class TestDataSeeder:
    """Handles seeding of test data into Supabase"""

    def __init__(self, url: str, key: str):
        self.client: Client = create_client(url, key)
        self.created_users: list[dict[str, Any]] = []
        self.created_api_keys: list[dict[str, Any]] = []
        self.encryption_key = os.getenv(
            "ENCRYPTION_KEY", "test-encryption-key-32-chars-xx"
        )

    def _generate_api_key(self, environment: str = "test") -> str:
        """Generate a realistic API key"""
        prefix = "gw"
        env_prefix = {"live": "live", "test": "test", "staging": "stg", "development": "dev"}
        return f"{prefix}_{env_prefix.get(environment, 'test')}_{secrets.token_hex(16)}"

    def _hash_api_key(self, api_key: str) -> str:
        """Create HMAC hash of API key"""
        return hmac.new(
            self.encryption_key.encode(),
            api_key.encode(),
            hashlib.sha256
        ).hexdigest()

    def _random_date(self, start_days_ago: int = 90, end_days_ago: int = 0) -> str:
        """Generate a random date between start and end days ago"""
        start = datetime.utcnow() - timedelta(days=start_days_ago)
        end = datetime.utcnow() - timedelta(days=end_days_ago)
        random_date = start + timedelta(
            seconds=random.randint(0, int((end - start).total_seconds()))
        )
        return random_date.isoformat()

    def clear_test_data(self):
        """Clear existing test data (be careful!)"""
        print("Clearing existing test data...")

        # Delete in order to respect foreign keys
        tables = [
            "chat_messages",
            "chat_sessions",
            "credit_transactions",
            "coupon_redemptions",
            "activity_log",
            "rate_limit_usage",
            "api_keys_new",
            "payments",
            "coupons",
            "user_plans",
            "users",
        ]

        for table in tables:
            try:
                # Delete all rows (use with caution!)
                self.client.table(table).delete().neq("id", -999999).execute()
                print(f"  Cleared: {table}")
            except Exception as e:
                print(f"  Warning: Could not clear {table}: {e}")

        print("Test data cleared!")

    def seed_users(self, count: int = 20) -> list[dict[str, Any]]:
        """Create test users"""
        print(f"Creating {count} test users...")

        users = []
        roles = ["user"] * 15 + ["developer"] * 4 + ["admin"]
        subscription_statuses = ["active"] * 10 + ["trial"] * 7 + ["expired"] * 2 + ["cancelled"]

        for i in range(count):
            first_name = random.choice(FIRST_NAMES)
            last_name = random.choice(LAST_NAMES)
            username = f"{first_name.lower()}_{last_name.lower()}_{i}"
            email = f"{username}@test.example.com"

            role = roles[i % len(roles)]
            status = subscription_statuses[i % len(subscription_statuses)]

            # Vary credits based on subscription status
            if status == "trial":
                credits = round(random.uniform(5, 50), 2)
            elif status == "active":
                credits = round(random.uniform(50, 500), 2)
            else:
                credits = round(random.uniform(0, 10), 2)

            user = {
                "username": username,
                "email": email,
                "credits": credits,
                "is_active": status != "cancelled",
                "auth_method": random.choice(["email", "google", "github"]),
                "subscription_status": status,
                "role": role,
                "welcome_email_sent": True,
                "privy_user_id": f"privy_{secrets.token_hex(8)}",
                "role_metadata": json.dumps({"source": "test_seed"}),
                "created_at": self._random_date(90, 1),
            }

            # Set trial expiration for trial users
            if status == "trial":
                trial_end = datetime.utcnow() + timedelta(days=random.randint(-3, 7))
                user["trial_expires_at"] = trial_end.isoformat()

            users.append(user)

        # Insert users
        result = self.client.table("users").insert(users).execute()
        self.created_users = result.data
        print(f"  Created {len(self.created_users)} users")
        return self.created_users

    def seed_api_keys(self, keys_per_user: int = 2) -> list[dict[str, Any]]:
        """Create API keys for users"""
        if not self.created_users:
            print("No users found. Run seed_users first.")
            return []

        print(f"Creating API keys ({keys_per_user} per user)...")

        api_keys = []
        environments = ["live", "test", "staging", "development"]

        for user in self.created_users:
            for i in range(keys_per_user):
                env = environments[i % len(environments)]
                api_key = self._generate_api_key(env)
                key_hash = self._hash_api_key(api_key)

                key_data = {
                    "user_id": user["id"],
                    "api_key": api_key,
                    "key_hash": key_hash,
                    "key_name": f"{env.title()} Key {i + 1}",
                    "environment_tag": env,
                    "is_primary": i == 0,
                    "is_active": random.random() > 0.1,  # 90% active
                    "scope_permissions": json.dumps({"read": ["*"], "write": ["chat"]}),
                    "ip_allowlist": [],
                    "domain_referrers": [],
                    "requests_used": random.randint(0, 10000),
                    "last4": api_key[-4:],
                    "created_at": self._random_date(60, 1),
                }

                # Some keys have expiration
                if random.random() > 0.7:
                    exp_date = datetime.utcnow() + timedelta(days=random.randint(-10, 90))
                    key_data["expiration_date"] = exp_date.isoformat()

                # Some keys have IP restrictions
                if random.random() > 0.8:
                    key_data["ip_allowlist"] = ["192.168.1.0/24", "10.0.0.0/8"]

                api_keys.append(key_data)

        # Insert in batches
        batch_size = 50
        all_created = []
        for i in range(0, len(api_keys), batch_size):
            batch = api_keys[i:i + batch_size]
            result = self.client.table("api_keys_new").insert(batch).execute()
            all_created.extend(result.data)

        self.created_api_keys = all_created
        print(f"  Created {len(self.created_api_keys)} API keys")
        return self.created_api_keys

    def seed_payments(self, count: int = 30) -> list[dict[str, Any]]:
        """Create payment records"""
        if not self.created_users:
            print("No users found. Run seed_users first.")
            return []

        print(f"Creating {count} payment records...")

        payments = []
        statuses = ["succeeded"] * 20 + ["pending"] * 5 + ["failed"] * 3 + ["refunded"] * 2

        for i in range(count):
            user = random.choice(self.created_users)
            status = statuses[i % len(statuses)]

            amount_usd = random.choice([5, 10, 20, 50, 100, 200])
            amount_cents = amount_usd * 100

            # Credits based on amount (roughly $1 = 100 credits)
            credits_purchased = amount_usd * 100
            bonus_credits = int(credits_purchased * 0.1) if amount_usd >= 50 else 0

            payment = {
                "user_id": user["id"],
                "amount_usd": amount_usd,
                "amount_cents": amount_cents,
                "credits_purchased": credits_purchased,
                "bonus_credits": bonus_credits,
                "currency": "usd",
                "payment_method": "stripe",
                "status": status,
                "stripe_payment_intent_id": f"pi_{secrets.token_hex(12)}",
                "stripe_checkout_session_id": f"cs_{secrets.token_hex(12)}",
                "metadata": json.dumps({"source": "test_seed"}),
                "created_at": self._random_date(90, 0),
            }

            if status == "succeeded":
                payment["completed_at"] = payment["created_at"]
            elif status == "failed":
                payment["failed_at"] = payment["created_at"]

            payments.append(payment)

        result = self.client.table("payments").insert(payments).execute()
        print(f"  Created {len(result.data)} payments")
        return result.data

    def seed_credit_transactions(self, count: int = 100) -> list[dict[str, Any]]:
        """Create credit transaction history"""
        if not self.created_users:
            print("No users found. Run seed_users first.")
            return []

        print(f"Creating {count} credit transactions...")

        transactions = []
        tx_types = ["deduction"] * 60 + ["purchase"] * 25 + ["bonus"] * 10 + ["refund"] * 5

        for i in range(count):
            user = random.choice(self.created_users)
            tx_type = tx_types[i % len(tx_types)]

            # Set amount based on transaction type
            if tx_type == "deduction":
                amount = -round(random.uniform(0.01, 5.0), 4)
                description = f"API usage: {random.choice(TEST_MODELS)[0]}"
            elif tx_type == "purchase":
                amount = round(random.choice([500, 1000, 2000, 5000, 10000]), 2)
                description = f"Credit purchase: ${amount/100:.2f}"
            elif tx_type == "bonus":
                amount = round(random.uniform(10, 100), 2)
                description = "Promotional bonus credits"
            else:  # refund
                amount = round(random.uniform(50, 500), 2)
                description = "Service credit refund"

            # Calculate balances (simplified)
            balance_before = round(random.uniform(0, 1000), 2)
            balance_after = round(balance_before + amount, 2)

            transaction = {
                "user_id": user["id"],
                "amount": amount,
                "transaction_type": tx_type,
                "description": description,
                "balance_before": max(0, balance_before),
                "balance_after": max(0, balance_after),
                "metadata": json.dumps({"source": "test_seed"}),
                "created_at": self._random_date(60, 0),
                "created_by": "system",
            }

            transactions.append(transaction)

        # Insert in batches
        batch_size = 50
        all_created = []
        for i in range(0, len(transactions), batch_size):
            batch = transactions[i:i + batch_size]
            result = self.client.table("credit_transactions").insert(batch).execute()
            all_created.extend(result.data)

        print(f"  Created {len(all_created)} credit transactions")
        return all_created

    def seed_activity_log(self, count: int = 200) -> list[dict[str, Any]]:
        """Create activity log entries"""
        if not self.created_users:
            print("No users found. Run seed_users first.")
            return []

        print(f"Creating {count} activity log entries...")

        activities = []
        finish_reasons = ["stop"] * 80 + ["length"] * 15 + ["error"] * 5

        for i in range(count):
            user = random.choice(self.created_users)
            model_info = random.choice(TEST_MODELS)
            model_id, provider, _, input_cost, output_cost = model_info

            tokens = random.randint(100, 10000)
            # Cost calculation (simplified)
            cost = round((tokens / 1_000_000) * ((input_cost + output_cost) / 2), 6)

            activity = {
                "user_id": user["id"],
                "model": model_id,
                "provider": provider.lower(),
                "tokens": tokens,
                "cost": cost,
                "speed": round(random.uniform(10, 100), 2),  # tokens/sec
                "finish_reason": finish_reasons[i % len(finish_reasons)],
                "app": random.choice(APPS),
                "metadata": json.dumps({
                    "prompt_tokens": int(tokens * 0.3),
                    "completion_tokens": int(tokens * 0.7),
                }),
                "timestamp": self._random_date(30, 0),
            }

            activities.append(activity)

        # Insert in batches
        batch_size = 50
        all_created = []
        for i in range(0, len(activities), batch_size):
            batch = activities[i:i + batch_size]
            result = self.client.table("activity_log").insert(batch).execute()
            all_created.extend(result.data)

        print(f"  Created {len(all_created)} activity log entries")
        return all_created

    def seed_chat_sessions(self, sessions_per_user: int = 3) -> list[dict[str, Any]]:
        """Create chat sessions and messages"""
        if not self.created_users:
            print("No users found. Run seed_users first.")
            return []

        print(f"Creating chat sessions ({sessions_per_user} per user)...")

        sessions = []
        chat_titles = [
            "Help with Python code",
            "Writing assistance",
            "API integration questions",
            "Debug my function",
            "Explain machine learning",
            "Code review request",
            "Database optimization",
            "General questions",
        ]

        for user in self.created_users:
            for i in range(sessions_per_user):
                model = random.choice(TEST_MODELS)[0]
                session = {
                    "user_id": user["id"],
                    "title": random.choice(chat_titles),
                    "model": model,
                    "is_active": random.random() > 0.2,  # 80% active
                    "created_at": self._random_date(30, 0),
                }
                sessions.append(session)

        # Insert sessions
        result = self.client.table("chat_sessions").insert(sessions).execute()
        created_sessions = result.data
        print(f"  Created {len(created_sessions)} chat sessions")

        # Create messages for each session
        print("Creating chat messages...")
        messages = []
        sample_user_msgs = [
            "Hello, can you help me with something?",
            "I have a question about Python programming.",
            "Can you explain how this code works?",
            "What's the best way to optimize this function?",
            "I'm getting an error, can you help debug?",
        ]
        sample_assistant_msgs = [
            "Of course! I'd be happy to help. What would you like to know?",
            "Sure, I can help with that. Could you provide more details?",
            "Let me analyze that for you. Here's what I found...",
            "Great question! Here's a detailed explanation...",
            "I see the issue. Let me explain the solution...",
        ]

        for session in created_sessions:
            # 2-6 messages per session
            num_messages = random.randint(2, 6)
            for j in range(num_messages):
                role = "user" if j % 2 == 0 else "assistant"
                content = (
                    random.choice(sample_user_msgs)
                    if role == "user"
                    else random.choice(sample_assistant_msgs)
                )

                message = {
                    "session_id": session["id"],
                    "role": role,
                    "content": content,
                    "model": session["model"] if role == "assistant" else None,
                    "tokens": random.randint(10, 500),
                    "created_at": self._random_date(30, 0),
                }
                messages.append(message)

        # Insert messages in batches
        batch_size = 100
        total_messages = 0
        for i in range(0, len(messages), batch_size):
            batch = messages[i:i + batch_size]
            result = self.client.table("chat_messages").insert(batch).execute()
            total_messages += len(result.data)

        print(f"  Created {total_messages} chat messages")
        return created_sessions

    def seed_coupons(self, count: int = 10) -> list[dict[str, Any]]:
        """Create coupon codes"""
        print(f"Creating {count} coupons...")

        coupons = []
        coupon_types = ["promotional", "referral", "compensation", "partnership"]

        for i in range(count):
            code = f"TEST{secrets.token_hex(4).upper()}"
            value = random.choice([5, 10, 20, 50, 100])
            max_uses = random.choice([1, 5, 10, 50, 100])

            coupon = {
                "code": code,
                "value_usd": value,
                "coupon_scope": "global",
                "max_uses": max_uses,
                "times_used": random.randint(0, max_uses - 1),
                "valid_from": self._random_date(30, 0),
                "valid_until": (datetime.utcnow() + timedelta(days=random.randint(30, 180))).isoformat(),
                "description": f"Test coupon worth ${value}",
                "coupon_type": random.choice(coupon_types),
                "is_active": random.random() > 0.2,  # 80% active
            }
            coupons.append(coupon)

        result = self.client.table("coupons").insert(coupons).execute()
        print(f"  Created {len(result.data)} coupons")
        return result.data

    def seed_providers(self) -> list[dict[str, Any]]:
        """Seed AI providers"""
        print("Creating AI providers...")

        providers = []
        for slug, name, base_url, is_active in TEST_PROVIDERS:
            provider = {
                "name": name,
                "slug": slug,
                "description": f"{name} AI inference provider",
                "base_url": base_url,
                "is_active": is_active,
                "supports_streaming": True,
                "supports_function_calling": slug in ["openrouter", "portkey"],
                "supports_vision": slug in ["openrouter", "portkey"],
                "health_status": random.choice(["healthy", "healthy", "healthy", "degraded"]),
                "metadata": json.dumps({"source": "test_seed"}),
            }
            providers.append(provider)

        # Try to insert, ignore duplicates
        for provider in providers:
            try:
                self.client.table("providers").upsert(
                    provider, on_conflict="slug"
                ).execute()
            except Exception as e:
                print(f"  Warning: Could not insert provider {provider['name']}: {e}")

        print(f"  Created/updated {len(providers)} providers")
        return providers

    def seed_plans(self) -> list[dict[str, Any]]:
        """Seed subscription plans"""
        print("Creating subscription plans...")

        plans = [
            {
                "name": "Free",
                "description": "Free tier with limited usage",
                "price_per_month": 0,
                "daily_request_limit": 100,
                "daily_token_limit": 10000,
                "monthly_request_limit": 1000,
                "monthly_token_limit": 100000,
                "features": json.dumps(["Basic models", "Community support"]),
                "is_active": True,
                "max_concurrent_requests": 2,
            },
            {
                "name": "Pro",
                "description": "Professional tier for developers",
                "price_per_month": 29,
                "daily_request_limit": 1000,
                "daily_token_limit": 100000,
                "monthly_request_limit": 30000,
                "monthly_token_limit": 3000000,
                "features": json.dumps(["All models", "Priority support", "API analytics"]),
                "is_active": True,
                "max_concurrent_requests": 10,
            },
            {
                "name": "Enterprise",
                "description": "Enterprise tier with unlimited access",
                "price_per_month": 99,
                "daily_request_limit": 10000,
                "daily_token_limit": 1000000,
                "monthly_request_limit": 300000,
                "monthly_token_limit": 30000000,
                "features": json.dumps(["All models", "24/7 support", "SLA", "Custom integrations"]),
                "is_active": True,
                "max_concurrent_requests": 50,
            },
        ]

        # Upsert plans
        for plan in plans:
            try:
                self.client.table("plans").upsert(
                    plan, on_conflict="name"
                ).execute()
            except Exception as e:
                print(f"  Warning: Could not insert plan {plan['name']}: {e}")

        print(f"  Created/updated {len(plans)} plans")
        return plans

    def seed_all(
        self,
        num_users: int = 20,
        api_keys_per_user: int = 2,
        num_payments: int = 30,
        num_transactions: int = 100,
        num_activities: int = 200,
        sessions_per_user: int = 3,
        num_coupons: int = 10,
    ):
        """Seed all test data"""
        print("\n" + "=" * 50)
        print("  Seeding Test Data")
        print("=" * 50 + "\n")

        # Seed reference data first
        self.seed_providers()
        self.seed_plans()

        # Seed users and related data
        self.seed_users(num_users)
        self.seed_api_keys(api_keys_per_user)
        self.seed_payments(num_payments)
        self.seed_credit_transactions(num_transactions)
        self.seed_activity_log(num_activities)
        self.seed_chat_sessions(sessions_per_user)
        self.seed_coupons(num_coupons)

        print("\n" + "=" * 50)
        print("  Seeding Complete!")
        print("=" * 50)
        print(f"\nCreated:")
        print(f"  - {len(self.created_users)} users")
        print(f"  - {len(self.created_api_keys)} API keys")
        print(f"  - {num_payments} payments")
        print(f"  - {num_transactions} credit transactions")
        print(f"  - {num_activities} activity logs")
        print(f"  - {len(self.created_users) * sessions_per_user} chat sessions")
        print(f"  - {num_coupons} coupons")
        print(f"  - {len(TEST_PROVIDERS)} providers")
        print(f"  - 3 subscription plans")

        # Print sample credentials for testing
        if self.created_users and self.created_api_keys:
            print("\n" + "-" * 50)
            print("Sample Test Credentials:")
            print("-" * 50)
            sample_user = self.created_users[0]
            sample_keys = [k for k in self.created_api_keys if k["user_id"] == sample_user["id"]]
            print(f"  User: {sample_user['email']}")
            print(f"  User ID: {sample_user['id']}")
            print(f"  Credits: {sample_user['credits']}")
            if sample_keys:
                print(f"  API Key: {sample_keys[0]['api_key']}")


def main():
    parser = argparse.ArgumentParser(
        description="Seed test data into Supabase instance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using environment variables
  python seed_test_data.py

  # Custom connection
  python seed_test_data.py --url http://localhost:54321 --key YOUR_KEY

  # Clear and reseed
  python seed_test_data.py --clear

  # Custom amounts
  python seed_test_data.py --users 50 --api-keys 3 --activities 500
        """
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_SUPABASE_URL,
        help="Supabase URL (default: from SUPABASE_URL env var)"
    )
    parser.add_argument(
        "--key",
        default=DEFAULT_SUPABASE_KEY,
        help="Supabase service key (default: from SUPABASE_SERVICE_KEY env var)"
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing test data before seeding"
    )
    parser.add_argument("--users", type=int, default=20, help="Number of users to create")
    parser.add_argument("--api-keys", type=int, default=2, help="API keys per user")
    parser.add_argument("--payments", type=int, default=30, help="Number of payments")
    parser.add_argument("--transactions", type=int, default=100, help="Number of credit transactions")
    parser.add_argument("--activities", type=int, default=200, help="Number of activity logs")
    parser.add_argument("--sessions", type=int, default=3, help="Chat sessions per user")
    parser.add_argument("--coupons", type=int, default=10, help="Number of coupons")

    args = parser.parse_args()

    if not args.key:
        print("Error: Supabase service key required.")
        print("Set SUPABASE_SERVICE_KEY env var or use --key argument")
        sys.exit(1)

    print(f"Connecting to Supabase at: {args.url}")

    try:
        seeder = TestDataSeeder(args.url, args.key)

        if args.clear:
            confirm = input("This will DELETE all existing data. Are you sure? (yes/no): ")
            if confirm.lower() == "yes":
                seeder.clear_test_data()
            else:
                print("Clear cancelled.")
                return

        seeder.seed_all(
            num_users=args.users,
            api_keys_per_user=args.api_keys,
            num_payments=args.payments,
            num_transactions=args.transactions,
            num_activities=args.activities,
            sessions_per_user=args.sessions,
            num_coupons=args.coupons,
        )

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
