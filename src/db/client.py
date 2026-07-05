"""
Database client facade.

Preferred import point for database access. Routes and services should import
from here rather than from src.config.supabase_config directly.
"""

from src.config.supabase_config import (
    get_initialization_status,
    get_supabase_client,
)

__all__ = ["get_db", "get_table", "get_initialization_status", "get_supabase_client"]


def get_db():
    """Get the Supabase database client."""
    return get_supabase_client()


def get_table(table_name: str):
    """Convenience: get_db().table(table_name)."""
    return get_db().table(table_name)
