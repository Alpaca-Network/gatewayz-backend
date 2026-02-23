"""
Development API Key Management
Provides utilities for managing development API keys to ensure proper tracking
even in development environments.
"""

import logging

logger = logging.getLogger(__name__)

# Constants
DEV_API_KEY_PREFIX = "dev_local_"
DEV_USER_EMAIL = "dev@localhost"
DEV_USER_USERNAME = "dev_user"


def get_or_create_dev_api_key() -> str | None:
    """
    Get or create a development API key in the database.

    This ensures that even in development mode, we have a valid API key
    that can be tracked in the database for analytics and testing.

    Returns:
        Development API key string if successful, None otherwise
    """
    try:
        from src.config.supabase_config import get_supabase_client
        from src.security.security import encrypt_api_key

        client = get_supabase_client()

        # Check if development user exists
        user_result = client.table("users").select("*").eq("email", DEV_USER_EMAIL).execute()

        if user_result.data and len(user_result.data) > 0:
            user = user_result.data[0]
            user_id = user["id"]
        else:
            # Create development user
            user_data = {
                "email": DEV_USER_EMAIL,
                "username": DEV_USER_USERNAME,
                "credits": 1000000.0,  # Large amount for development
                "is_active": True,
                "environment_tag": "development",
            }
            user_result = client.table("users").insert(user_data).execute()

            if not user_result.data or len(user_result.data) == 0:
                logger.error("Failed to create development user")
                return None

            user = user_result.data[0]
            user_id = user["id"]
            logger.info(f"Created development user: {user_id}")

        # Check if development API key exists
        api_key_result = (
            client.table("api_keys_new")
            .select("*")
            .eq("user_id", user_id)
            .eq("name", "Development Key")
            .eq("is_active", True)
            .execute()
        )

        if api_key_result.data and len(api_key_result.data) > 0:
            # Return existing key (we need to decrypt it)
            # For development, we'll use a predictable key format
            return f"{DEV_API_KEY_PREFIX}{user_id}_local_development"

        # Create new development API key
        plain_key = f"{DEV_API_KEY_PREFIX}{user_id}_local_development"

        # Try to encrypt the key (if encryption is available)
        try:
            encrypted_key = encrypt_api_key(plain_key)
        except Exception as e:
            logger.warning(f"Could not encrypt dev API key: {e}, using plain key")
            encrypted_key = plain_key

        api_key_data = {
            "user_id": user_id,
            "name": "Development Key",
            "key": encrypted_key,
            "is_active": True,
            "environment": "development",
        }

        create_result = client.table("api_keys_new").insert(api_key_data).execute()

        if create_result.data and len(create_result.data) > 0:
            logger.info(f"Created development API key for user {user_id}")
            return plain_key
        else:
            logger.error("Failed to create development API key")
            return None

    except Exception as e:
        logger.error(f"Error getting/creating development API key: {e}", exc_info=True)
        return None


def is_dev_api_key(api_key: str) -> bool:
    """
    Check if the given API key is a development key.

    Args:
        api_key: API key string to check

    Returns:
        True if it's a development key, False otherwise
    """
    return api_key and api_key.startswith(DEV_API_KEY_PREFIX)
