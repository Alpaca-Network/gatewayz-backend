"""
Test Data Generators using Faker

Provides realistic, randomized test data for various entities in the system.
Use these generators instead of hardcoded test data for more robust tests.

Usage:
    from tests.helpers.data_generators import UserGenerator, ChatGenerator

    # Generate a single user
    user = UserGenerator.create_user()

    # Generate multiple users
    users = UserGenerator.create_batch(10)

    # Generate with specific overrides
    user = UserGenerator.create_user(email="specific@example.com")
"""

from faker import Faker
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import secrets
import uuid

fake = Faker()


# ============================================================================
# User Data Generators
# ============================================================================

class UserGenerator:
    """Generate realistic user data"""

    @staticmethod
    def create_user(
        user_id: Optional[str] = None,
        email: Optional[str] = None,
        full_name: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a realistic user object

        Args:
            user_id: Override user ID
            email: Override email
            full_name: Override full name
            **kwargs: Additional fields to override

        Returns:
            User dict with realistic data
        """
        user_data = {
            "id": user_id or str(uuid.uuid4()),
            "email": email or fake.email(),
            "full_name": full_name or fake.name(),
            "created_at": fake.date_time_between(start_date="-1y", end_date="now").isoformat(),
            "email_verified": fake.boolean(chance_of_getting_true=80),
            "phone": fake.phone_number(),
            "avatar_url": fake.image_url(),
            "metadata": {
                "signup_source": fake.random_element(["web", "mobile", "api"]),
                "referral_code": fake.bothify(text="REF-????-####"),
                "preferences": {
                    "language": fake.random_element(["en", "es", "fr", "de"]),
                    "timezone": fake.timezone(),
                    "notifications_enabled": fake.boolean()
                }
            }
        }

        # Override with any provided kwargs
        user_data.update(kwargs)
        return user_data

    @staticmethod
    def create_batch(count: int, **kwargs) -> List[Dict[str, Any]]:
        """Generate multiple users"""
        return [UserGenerator.create_user(**kwargs) for _ in range(count)]


# ============================================================================
# API Key Generators
# ============================================================================

class APIKeyGenerator:
    """Generate realistic API keys"""

    @staticmethod
    def create_api_key(
        key_id: Optional[str] = None,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        key_type: str = "live",
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a realistic API key object

        Args:
            key_id: Override key ID
            user_id: Override user ID
            name: Override key name
            key_type: "live" or "test"
            **kwargs: Additional fields

        Returns:
            API key dict
        """
        prefix = f"gw_{key_type}_"
        raw_key = prefix + secrets.token_urlsafe(32)

        key_data = {
            "id": key_id or str(uuid.uuid4()),
            "user_id": user_id or str(uuid.uuid4()),
            "name": name or fake.catch_phrase(),
            "key": raw_key,
            "key_preview": f"{raw_key[:15]}...{raw_key[-4:]}",
            "created_at": fake.date_time_between(start_date="-90d", end_date="now").isoformat(),
            "last_used_at": fake.date_time_between(start_date="-7d", end_date="now").isoformat() if fake.boolean() else None,
            "status": fake.random_element(["active", "inactive", "revoked"]),
            "allowed_ips": APIKeyGenerator._generate_ip_allowlist() if fake.boolean(chance_of_getting_true=30) else [],
            "allowed_domains": APIKeyGenerator._generate_domain_allowlist() if fake.boolean(chance_of_getting_true=20) else [],
            "rate_limit": {
                "requests_per_minute": fake.random_element([10, 60, 600, 6000]),
                "requests_per_day": fake.random_element([1000, 10000, 100000, 1000000])
            },
            "metadata": {
                "environment": fake.random_element(["production", "staging", "development"]),
                "purpose": fake.bs()
            }
        }

        key_data.update(kwargs)
        return key_data

    @staticmethod
    def _generate_ip_allowlist(count: int = None) -> List[str]:
        """Generate list of IP addresses"""
        if count is None:
            count = fake.random_int(min=1, max=5)
        return [fake.ipv4() for _ in range(count)]

    @staticmethod
    def _generate_domain_allowlist(count: int = None) -> List[str]:
        """Generate list of allowed domains"""
        if count is None:
            count = fake.random_int(min=1, max=3)
        return [f"https://{fake.domain_name()}" for _ in range(count)]

    @staticmethod
    def create_batch(count: int, **kwargs) -> List[Dict[str, Any]]:
        """Generate multiple API keys"""
        return [APIKeyGenerator.create_api_key(**kwargs) for _ in range(count)]


# ============================================================================
# Chat Message Generators
# ============================================================================

class ChatGenerator:
    """Generate realistic chat messages and conversations"""

    @staticmethod
    def create_message(
        role: str = "user",
        content: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a realistic chat message

        Args:
            role: "user", "assistant", or "system"
            content: Override message content
            **kwargs: Additional fields

        Returns:
            Chat message dict
        """
        if content is None:
            if role == "user":
                content = fake.sentence(nb_words=fake.random_int(min=5, max=20))
            elif role == "assistant":
                content = fake.paragraph(nb_sentences=fake.random_int(min=2, max=5))
            else:  # system
                content = fake.sentence(nb_words=8)

        message = {
            "role": role,
            "content": content,
            "timestamp": fake.date_time_between(start_date="-1h", end_date="now").isoformat()
        }

        message.update(kwargs)
        return message

    @staticmethod
    def create_conversation(message_count: int = None) -> List[Dict[str, Any]]:
        """
        Generate a realistic conversation with alternating user/assistant messages

        Args:
            message_count: Number of messages (random if None)

        Returns:
            List of messages forming a conversation
        """
        if message_count is None:
            message_count = fake.random_int(min=2, max=10)

        messages = []
        for i in range(message_count):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append(ChatGenerator.create_message(role=role))

        return messages

    @staticmethod
    def create_chat_completion_request(
        model: Optional[str] = None,
        messages: Optional[List[Dict]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a realistic chat completion request

        Args:
            model: Override model name
            messages: Override messages
            **kwargs: Additional parameters

        Returns:
            Chat completion request dict
        """
        request = {
            "model": model or fake.random_element([
                "gpt-4",
                "gpt-3.5-turbo",
                "claude-3-opus",
                "claude-3-sonnet",
                "claude-3-haiku"
            ]),
            "messages": messages or ChatGenerator.create_conversation(
                message_count=fake.random_int(min=2, max=6)
            ),
            "max_tokens": fake.random_element([100, 500, 1000, 2000, 4000]),
            "temperature": round(fake.random.uniform(0, 2), 2),
            "top_p": round(fake.random.uniform(0.5, 1.0), 2),
            "stream": fake.boolean(chance_of_getting_true=30)
        }

        request.update(kwargs)
        return request


# ============================================================================
# Transaction Generators
# ============================================================================

class TransactionGenerator:
    """Generate realistic transaction and billing data"""

    @staticmethod
    def create_transaction(
        transaction_id: Optional[str] = None,
        user_id: Optional[str] = None,
        amount: Optional[float] = None,
        transaction_type: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a realistic transaction

        Args:
            transaction_id: Override transaction ID
            user_id: Override user ID
            amount: Override amount
            transaction_type: "credit_purchase", "usage_charge", "refund"
            **kwargs: Additional fields

        Returns:
            Transaction dict
        """
        if transaction_type is None:
            transaction_type = fake.random_element([
                "credit_purchase",
                "usage_charge",
                "refund",
                "promotional_credit"
            ])

        if amount is None:
            if transaction_type == "credit_purchase":
                amount = fake.random_element([10.0, 25.0, 50.0, 100.0, 500.0])
            else:
                amount = round(fake.random.uniform(0.01, 50.0), 2)

        transaction = {
            "id": transaction_id or str(uuid.uuid4()),
            "user_id": user_id or str(uuid.uuid4()),
            "amount": amount,
            "currency": "USD",
            "type": transaction_type,
            "status": fake.random_element(["pending", "completed", "failed", "refunded"]),
            "created_at": fake.date_time_between(start_date="-30d", end_date="now").isoformat(),
            "description": fake.sentence(),
            "metadata": {
                "payment_method": fake.random_element(["card", "bank_transfer", "crypto"]) if transaction_type == "credit_purchase" else None,
                "stripe_charge_id": f"ch_{fake.bothify(text='????##########')}" if transaction_type == "credit_purchase" else None
            }
        }

        transaction.update(kwargs)
        return transaction

    @staticmethod
    def create_usage_record(
        user_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate a realistic usage record

        Args:
            user_id: Override user ID
            model: Override model name
            **kwargs: Additional fields

        Returns:
            Usage record dict
        """
        prompt_tokens = fake.random_int(min=10, max=5000)
        completion_tokens = fake.random_int(min=5, max=2000)
        cost_per_prompt_token = fake.random.uniform(0.000001, 0.00003)
        cost_per_completion_token = fake.random.uniform(0.000003, 0.00006)

        usage = {
            "id": str(uuid.uuid4()),
            "user_id": user_id or str(uuid.uuid4()),
            "model": model or fake.random_element([
                "gpt-4",
                "gpt-3.5-turbo",
                "claude-3-opus",
                "claude-3-sonnet"
            ]),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": round(
                (prompt_tokens * cost_per_prompt_token) +
                (completion_tokens * cost_per_completion_token),
                6
            ),
            "timestamp": fake.date_time_between(start_date="-24h", end_date="now").isoformat(),
            "endpoint": "/v1/chat/completions",
            "status_code": fake.random_element([200, 200, 200, 400, 429, 500])  # Mostly successful
        }

        usage.update(kwargs)
        return usage

    @staticmethod
    def create_batch(count: int, **kwargs) -> List[Dict[str, Any]]:
        """Generate multiple transactions"""
        return [TransactionGenerator.create_transaction(**kwargs) for _ in range(count)]


# ============================================================================
# Model Metadata Generators
# ============================================================================

class ModelGenerator:
    """Generate realistic model metadata"""

    @staticmethod
    def create_model(
        model_id: Optional[str] = None,
        provider: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate realistic model metadata

        Args:
            model_id: Override model ID
            provider: Override provider name
            **kwargs: Additional fields

        Returns:
            Model metadata dict
        """
        if provider is None:
            provider = fake.random_element([
                "openai",
                "anthropic",
                "cohere",
                "together",
                "openrouter"
            ])

        model_names = {
            "openai": ["gpt-4", "gpt-3.5-turbo", "gpt-4-turbo"],
            "anthropic": ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
            "cohere": ["command", "command-light", "command-nightly"],
            "together": ["llama-2-70b", "mixtral-8x7b", "qwen-72b"],
            "openrouter": ["auto", "openai/gpt-4", "anthropic/claude-3-opus"]
        }

        model_name = fake.random_element(model_names.get(provider, ["generic-model"]))

        model = {
            "id": model_id or f"{provider}/{model_name}",
            "provider": provider,
            "name": model_name,
            "display_name": model_name.replace("-", " ").title(),
            "context_length": fake.random_element([4096, 8192, 16384, 32768, 100000, 200000]),
            "max_output_tokens": fake.random_element([2048, 4096, 8192, 16384]),
            "pricing": {
                "prompt": round(fake.random.uniform(0.000001, 0.00003), 6),
                "completion": round(fake.random.uniform(0.000003, 0.00009), 6),
                "image": round(fake.random.uniform(0.001, 0.01), 4) if fake.boolean(chance_of_getting_true=20) else None
            },
            "capabilities": {
                "streaming": fake.boolean(chance_of_getting_true=90),
                "function_calling": fake.boolean(chance_of_getting_true=60),
                "vision": fake.boolean(chance_of_getting_true=30),
                "json_mode": fake.boolean(chance_of_getting_true=70)
            },
            "status": fake.random_element(["active", "deprecated", "beta"]),
            "created_at": fake.date_time_between(start_date="-2y", end_date="now").isoformat(),
            "updated_at": fake.date_time_between(start_date="-30d", end_date="now").isoformat()
        }

        model.update(kwargs)
        return model

    @staticmethod
    def create_batch(count: int, **kwargs) -> List[Dict[str, Any]]:
        """Generate multiple models"""
        return [ModelGenerator.create_model(**kwargs) for _ in range(count)]


# ============================================================================
# Rate Limit Data Generators
# ============================================================================

class RateLimitGenerator:
    """Generate realistic rate limit data"""

    @staticmethod
    def create_rate_limit_usage(
        user_id: Optional[str] = None,
        window_start: Optional[datetime] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate realistic rate limit usage data

        Args:
            user_id: Override user ID
            window_start: Override window start time
            **kwargs: Additional fields

        Returns:
            Rate limit usage dict
        """
        if window_start is None:
            window_start = datetime.utcnow() - timedelta(minutes=fake.random_int(min=0, max=59))

        limit = fake.random_element([60, 600, 6000])
        used = fake.random_int(min=0, max=limit)

        usage = {
            "user_id": user_id or str(uuid.uuid4()),
            "window_start": window_start.isoformat(),
            "window_duration_seconds": 60,
            "requests_limit": limit,
            "requests_used": used,
            "requests_remaining": max(0, limit - used),
            "reset_at": (window_start + timedelta(seconds=60)).isoformat()
        }

        usage.update(kwargs)
        return usage


# ============================================================================
# Convenience Functions
# ============================================================================

def create_complete_test_scenario(
    num_users: int = 3,
    num_api_keys_per_user: int = 2
) -> Dict[str, Any]:
    """
    Create a complete test scenario with users, API keys, and transactions

    Args:
        num_users: Number of users to generate
        num_api_keys_per_user: API keys per user

    Returns:
        Dict with all generated test data
    """
    users = UserGenerator.create_batch(num_users)

    scenario = {
        "users": users,
        "api_keys": [],
        "transactions": [],
        "usage_records": [],
        "models": ModelGenerator.create_batch(10)
    }

    for user in users:
        # Generate API keys for each user
        api_keys = APIKeyGenerator.create_batch(
            num_api_keys_per_user,
            user_id=user["id"]
        )
        scenario["api_keys"].extend(api_keys)

        # Generate some transactions
        transactions = TransactionGenerator.create_batch(
            fake.random_int(min=2, max=8),
            user_id=user["id"]
        )
        scenario["transactions"].extend(transactions)

        # Generate usage records
        usage_records = [
            TransactionGenerator.create_usage_record(user_id=user["id"])
            for _ in range(fake.random_int(min=5, max=20))
        ]
        scenario["usage_records"].extend(usage_records)

    return scenario
