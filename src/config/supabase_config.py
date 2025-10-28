import os
import logging
from typing import Optional
from supabase import create_client, Client
from src.config.config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_supabase_client: Optional[Client] = None

def get_supabase_client() -> Client:
    global _supabase_client

    if _supabase_client is not None:
        return _supabase_client

    try:
        Config.validate()

        _supabase_client = create_client(
            supabase_url=Config.SUPABASE_URL,
            supabase_key=Config.SUPABASE_KEY
        )

        # Only test connection if not in test environment with dummy credentials
        if Config.SUPABASE_URL != "https://test.supabase.co":
            # Test the connection without recursive call
            _test_connection_internal(_supabase_client)

        return _supabase_client

    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        raise RuntimeError(f"Supabase client initialization failed: {e}")

def _test_connection_internal(client: Client) -> bool:
    """Internal test connection that takes a client as parameter"""
    try:
        result = client.table('users').select('*').limit(1).execute()
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        raise RuntimeError(f"Database connection failed: {e}")

def test_connection() -> bool:
    """Public test connection method"""
    try:
        client = get_supabase_client()
        return _test_connection_internal(client)
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        raise RuntimeError(f"Database connection failed: {e}")

def init_db():
    try:
        test_connection()
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

def get_client() -> Client:
    return get_supabase_client()

supabase = property(get_client) 