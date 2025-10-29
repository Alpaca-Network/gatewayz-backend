import os
import logging
from typing import Optional
from supabase import create_client, Client
from src.config.config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_supabase_client: Optional[Client] = None


class _TestingTableStub:
    def select(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def delete(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return type("Result", (), {"data": []})()


class _TestingRPCStub:
    def execute(self):
        return type("Result", (), {"data": []})()


class _SupabaseTestingStub:
    def table(self, *_args, **_kwargs):
        return _TestingTableStub()

    def rpc(self, *_args, **_kwargs):
        return _TestingRPCStub()

def get_supabase_client() -> Client:
    global _supabase_client
    
    if _supabase_client is not None:
        return _supabase_client

    if Config.IS_TESTING:
        _supabase_client = _SupabaseTestingStub()  # type: ignore[assignment]
        return _supabase_client

    try:
        Config.validate()

        _supabase_client = create_client(
            supabase_url=Config.SUPABASE_URL,
            supabase_key=Config.SUPABASE_KEY
        )

        test_connection()

        return _supabase_client

    except Exception as e:
        logger.error(f"Failed to initialize Supabase client: {e}")
        raise RuntimeError(f"Supabase client initialization failed: {e}")

def test_connection() -> bool:
    try:
        client = get_supabase_client()
        result = client.table('users').select('*').limit(1).execute()
        return True
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
