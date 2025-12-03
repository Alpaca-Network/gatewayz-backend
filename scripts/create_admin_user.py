#!/usr/bin/env python3
"""
Create Admin User Script

Creates a user with:
- 1000 credits
- Admin privileges (superadmin role)
- Active API key
- Full permissions

Usage:
    python scripts/create_admin_user.py
    python scripts/create_admin_user.py --email admin@example.com --username admin --password secret123
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


def create_admin_user(
    email: str,
    username: str,
    password: str,
    credits: float = 1000.00,
) -> dict:
    """
    Create an admin user with full privileges

    Args:
        email: User email address
        username: Username
        password: Admin password (should be hashed in production!)
        credits: Initial credit balance (default: 1000)

    Returns:
        Dictionary with user details, admin info, and API key
    """
    # Initialize Supabase client
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env file")

    supabase: Client = create_client(supabase_url, supabase_key)

    print("🔄 Creating admin user...")
    print("")

    # Generate unique privy_user_id
    privy_user_id = f"admin_{int(datetime.now().timestamp())}"

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
            "subscription_status": "active",
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
        # Step 2: Create Admin Entry
        # ====================================================================
        print(f"🔐 Creating admin entry...")

        admin_data = {
            "email": email,
            "password": password,  # TODO: Hash this in production!
            "role": "superadmin",
            "status": "active",
        }

        admin_response = supabase.table("admin_users").insert(admin_data).execute()

        if not admin_response.data:
            raise Exception("Failed to create admin user")

        admin = admin_response.data[0]
        admin_id = admin["id"]

        print(f"✅ Admin created with ID: {admin_id}")
        print("")

        # ====================================================================
        # Step 3: Generate API Key
        # ====================================================================
        print(f"🔑 Generating API key...")

        api_key = generate_api_key("live")

        api_key_data = {
            "user_id": user_id,
            "api_key": api_key,
            "key_name": "Primary Admin Key",
            "environment_tag": "live",
            "is_primary": True,
            "is_active": True,
            "scope_permissions": {
                "chat": ["*"],
                "models": ["*"],
                "images": ["*"],
                "admin": ["*"],
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
        print("🎉 ADMIN USER CREATED SUCCESSFULLY!")
        print("=" * 80)
        print("")
        print("👤 USER DETAILS:")
        print(f"   User ID:       {user_id}")
        print(f"   Username:      {username}")
        print(f"   Email:         {email}")
        print(f"   Credits:       {credits:.2f}")
        print(f"   Status:        active")
        print("")
        print("🔑 ADMIN CREDENTIALS:")
        print(f"   Admin ID:      {admin_id}")
        print(f"   Role:          superadmin")
        print(f"   Password:      {password}")
        print("")
        print("🔐 API KEY:")
        print(f"   API Key ID:    {api_key_id}")
        print(f"   API Key:       {api_key}")
        print(f"   Environment:   live")
        print(f"   Permissions:   Full Access (*)")
        print("")
        print("=" * 80)
        print("")
        print("📋 USAGE:")
        print("")
        print("API Request Example:")
        print(f'  curl -X POST http://localhost:8000/v1/chat/completions \\')
        print(f'    -H "Authorization: Bearer {api_key}" \\')
        print(f'    -H "Content-Type: application/json" \\')
        print(f"    -d '{{\"model\": \"gpt-4\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello!\"}}]}}'")
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
            },
            "admin": {
                "id": admin_id,
                "role": "superadmin",
                "password": password,
            },
            "api_key": {
                "id": api_key_id,
                "key": api_key,
                "environment": "live",
            },
        }

    except Exception as e:
        print(f"❌ Error creating admin user: {e}")
        raise


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Create an admin user with 1000 credits and full privileges"
    )
    parser.add_argument(
        "--email",
        type=str,
        default="admin@gatewayz.local",
        help="Admin email address (default: admin@gatewayz.local)",
    )
    parser.add_argument(
        "--username",
        type=str,
        default="admin",
        help="Username (default: admin)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default="admin123",
        help="Admin password (default: admin123)",
    )
    parser.add_argument(
        "--credits",
        type=float,
        default=1000.00,
        help="Initial credits (default: 1000)",
    )

    args = parser.parse_args()

    try:
        result = create_admin_user(
            email=args.email,
            username=args.username,
            password=args.password,
            credits=args.credits,
        )

        # Exit successfully
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ Failed to create admin user: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
