#!/usr/bin/env python3
"""
Create User with Credits Script

Creates a user with:
- Custom credit amount (default: 1000)
- Active API key with full permissions
- Active subscription

Usage:
    python scripts/create_user_with_credits.py
    python scripts/create_user_with_credits.py --email user@example.com --credits 5000
"""

import os
import sys
import secrets
import argparse
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def generate_api_key(environment: str = "live") -> str:
    """Generate a secure API key"""
    random_part = secrets.token_urlsafe(32)
    prefix = f"gw_{environment}_"
    return prefix + random_part


def create_user_with_credits(
    email: str,
    username: str,
    credits: float = 1000.00,
) -> dict:
    """
    Create a user with credits and API key

    Args:
        email: User email address
        username: Username
        credits: Initial credit balance (default: 1000)

    Returns:
        Dictionary with user details and API key
    """
    # Initialize Supabase client
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env file")

    supabase: Client = create_client(supabase_url, supabase_key)

    print("🔄 Creating user with credits...")
    print("")

    # Generate unique privy_user_id
    privy_user_id = f"user_{int(datetime.now().timestamp())}"

    try:
        # ====================================================================
        # Step 1: Create User
        # ====================================================================
        print(f"📝 Creating user: {email}")

        user_data = {
            "username": username,
            "email": email,
            "credits": credits,
            "is_active": True,
            "auth_method": "email",
            "subscription_status": "active",  # Active subscription for full access
            "privy_user_id": privy_user_id,
        }

        user_response = supabase.table("users").insert(user_data).execute()

        if not user_response.data:
            raise Exception("Failed to create user")

        user = user_response.data[0]
        user_id = user["id"]

        print(f"✅ User created with ID: {user_id}")
        print("")

        # ====================================================================
        # Step 2: Generate API Key with Full Permissions
        # ====================================================================
        print(f"🔑 Generating API key...")

        api_key = generate_api_key("live")

        api_key_data = {
            "user_id": user_id,
            "api_key": api_key,
            "key_name": "Primary API Key",
            "environment_tag": "live",
            "is_primary": True,
            "is_active": True,
            "scope_permissions": {
                "chat": ["*"],
                "models": ["*"],
                "images": ["*"],
                "admin": ["*"],
                "analytics": ["*"],
            },
        }

        api_key_response = supabase.table("api_keys_new").insert(api_key_data).execute()

        if not api_key_response.data:
            raise Exception("Failed to create API key")

        api_key_obj = api_key_response.data[0]
        api_key_id = api_key_obj["id"]

        print(f"✅ API key created with ID: {api_key_id}")
        print("")

        # ====================================================================
        # Display Results
        # ====================================================================
        print("=" * 80)
        print("🎉 USER CREATED SUCCESSFULLY!")
        print("=" * 80)
        print("")
        print("👤 USER DETAILS:")
        print(f"   User ID:          {user_id}")
        print(f"   Username:         {username}")
        print(f"   Email:            {email}")
        print(f"   Credits:          {credits:.2f}")
        print(f"   Subscription:     active")
        print(f"   Status:           active")
        print("")
        print("🔐 API KEY:")
        print(f"   API Key ID:       {api_key_id}")
        print(f"   API Key:          {api_key}")
        print(f"   Environment:      live")
        print(f"   Permissions:      Full Access (*)")
        print("")
        print("=" * 80)
        print("")
        print("📋 USAGE EXAMPLES:")
        print("")
        print("1. Chat Completion:")
        print(f'   curl -X POST http://localhost:8000/v1/chat/completions \\')
        print(f'     -H "Authorization: Bearer {api_key}" \\')
        print(f'     -H "Content-Type: application/json" \\')
        print(f"     -d '{{\"model\": \"gpt-3.5-turbo\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'")
        print("")
        print("2. List Models:")
        print(f'   curl http://localhost:8000/v1/models \\')
        print(f'     -H "Authorization: Bearer {api_key}"')
        print("")
        print("3. Check Credits:")
        print(f'   curl http://localhost:8000/v1/users/me \\')
        print(f'     -H "Authorization: Bearer {api_key}"')
        print("")
        print("=" * 80)
        print("")

        # Return all details
        return {
            "user": {
                "id": user_id,
                "username": username,
                "email": email,
                "credits": credits,
                "privy_user_id": privy_user_id,
                "subscription_status": "active",
            },
            "api_key": {
                "id": api_key_id,
                "key": api_key,
                "environment": "live",
                "permissions": {
                    "chat": ["*"],
                    "models": ["*"],
                    "images": ["*"],
                    "admin": ["*"],
                    "analytics": ["*"],
                },
            },
        }

    except Exception as e:
        print(f"❌ Error creating user: {e}")
        import traceback
        traceback.print_exc()
        raise


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Create a user with credits and API key"
    )
    parser.add_argument(
        "--email",
        type=str,
        default=f"user{int(datetime.now().timestamp())}@gatewayz.local",
        help="User email address (default: auto-generated)",
    )
    parser.add_argument(
        "--username",
        type=str,
        default=f"user{int(datetime.now().timestamp())}",
        help="Username (default: auto-generated)",
    )
    parser.add_argument(
        "--credits",
        type=float,
        default=1000.00,
        help="Initial credits (default: 1000)",
    )

    args = parser.parse_args()

    try:
        result = create_user_with_credits(
            email=args.email,
            username=args.username,
            credits=args.credits,
        )

        # Print JSON for programmatic access
        import json
        print("\n📄 JSON Output (for scripts):")
        print(json.dumps(result, indent=2))

        # Exit successfully
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ Failed to create user: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
