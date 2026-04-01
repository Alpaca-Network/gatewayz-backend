"""
Database layer for model mapping tables.

Provides query functions for model_aliases, model_provider_mappings,
and model_routing_rules tables.

Error contract:
- All functions return [] on error (never None)
- Errors are logged via logger.warning()
- Uses pagination to handle large result sets beyond Supabase's 1000-row limit
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

SUPABASE_PAGE_SIZE = 1000
DB_QUERY_TIMEOUT_SECONDS = 60


def get_all_model_aliases() -> list[dict[str, Any]]:
    """
    Fetch all rows from model_aliases table.

    Returns:
        List of dicts with keys: alias, canonical_id
    """
    try:
        client = get_supabase_client()
        results: list[dict[str, Any]] = []
        offset = 0
        deadline = time.monotonic() + DB_QUERY_TIMEOUT_SECONDS

        while True:
            if time.monotonic() > deadline:
                logger.warning(
                    "model_aliases fetch deadline exceeded after %d rows; returning partial results",
                    len(results),
                )
                break

            response = (
                client.table("model_aliases")
                .select("alias, canonical_id")
                .range(offset, offset + SUPABASE_PAGE_SIZE - 1)
                .execute()
            )
            batch = response.data or []
            if not batch:
                break
            results.extend(batch)
            if len(batch) < SUPABASE_PAGE_SIZE:
                break
            offset += SUPABASE_PAGE_SIZE

        logger.debug("Loaded %d model aliases from DB", len(results))
        return results
    except Exception as e:
        logger.warning("Failed to fetch model_aliases: %s", e)
        return []


def get_all_model_provider_mappings() -> list[dict[str, Any]]:
    """
    Fetch all rows from model_provider_mappings table.

    Returns:
        List of dicts with keys: model_id, provider, provider_model_id
    """
    try:
        client = get_supabase_client()
        results: list[dict[str, Any]] = []
        offset = 0
        deadline = time.monotonic() + DB_QUERY_TIMEOUT_SECONDS

        while True:
            if time.monotonic() > deadline:
                logger.warning(
                    "model_provider_mappings fetch deadline exceeded after %d rows; returning partial results",
                    len(results),
                )
                break

            response = (
                client.table("model_provider_mappings")
                .select("model_id, provider, provider_model_id")
                .range(offset, offset + SUPABASE_PAGE_SIZE - 1)
                .execute()
            )
            batch = response.data or []
            if not batch:
                break
            results.extend(batch)
            if len(batch) < SUPABASE_PAGE_SIZE:
                break
            offset += SUPABASE_PAGE_SIZE

        logger.debug("Loaded %d model provider mappings from DB", len(results))
        return results
    except Exception as e:
        logger.warning("Failed to fetch model_provider_mappings: %s", e)
        return []


def get_all_model_routing_rules() -> list[dict[str, Any]]:
    """
    Fetch all rows from model_routing_rules table, ordered by priority DESC.

    Returns:
        List of dicts with keys: model_pattern, force_provider, priority
    """
    try:
        client = get_supabase_client()
        results: list[dict[str, Any]] = []
        offset = 0
        deadline = time.monotonic() + DB_QUERY_TIMEOUT_SECONDS

        while True:
            if time.monotonic() > deadline:
                logger.warning(
                    "model_routing_rules fetch deadline exceeded after %d rows; returning partial results",
                    len(results),
                )
                break

            response = (
                client.table("model_routing_rules")
                .select("model_pattern, force_provider, priority")
                .order("priority", desc=True)
                .range(offset, offset + SUPABASE_PAGE_SIZE - 1)
                .execute()
            )
            batch = response.data or []
            if not batch:
                break
            results.extend(batch)
            if len(batch) < SUPABASE_PAGE_SIZE:
                break
            offset += SUPABASE_PAGE_SIZE

        logger.debug("Loaded %d model routing rules from DB", len(results))
        return results
    except Exception as e:
        logger.warning("Failed to fetch model_routing_rules: %s", e)
        return []
