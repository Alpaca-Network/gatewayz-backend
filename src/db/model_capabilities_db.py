"""
Database layer for model capability columns and quality scores.

Provides query functions for the new columns added to the models table
(max_output_tokens, has_json_mode, is_reasoning, is_free, latency_tier)
and for the model_quality_scores table.

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


def get_all_model_capability_flags() -> list[dict[str, Any]]:
    """
    Fetch capability columns for all active models.

    Returns:
        List of dicts with keys:
            provider_model_id, model_name, max_output_tokens,
            has_json_mode, is_reasoning, is_free, latency_tier
    """
    try:
        client = get_supabase_client()
        results: list[dict[str, Any]] = []
        offset = 0
        deadline = time.monotonic() + DB_QUERY_TIMEOUT_SECONDS

        while True:
            if time.monotonic() > deadline:
                logger.warning(
                    "model capability flags fetch deadline exceeded after %d rows; "
                    "returning partial results",
                    len(results),
                )
                break

            response = (
                client.table("models")
                .select(
                    "provider_model_id, model_name, max_output_tokens, "
                    "has_json_mode, is_reasoning, is_free, latency_tier"
                )
                .eq("is_active", True)
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

        logger.debug("Loaded %d model capability rows from DB", len(results))
        return results
    except Exception as e:
        logger.warning("Failed to fetch model capability flags: %s", e)
        return []


def get_all_quality_scores() -> list[dict[str, Any]]:
    """
    Fetch all rows from model_quality_scores table.

    Returns:
        List of dicts with keys: model_id, task_type, score
    """
    try:
        client = get_supabase_client()
        results: list[dict[str, Any]] = []
        offset = 0
        deadline = time.monotonic() + DB_QUERY_TIMEOUT_SECONDS

        while True:
            if time.monotonic() > deadline:
                logger.warning(
                    "model_quality_scores fetch deadline exceeded after %d rows; "
                    "returning partial results",
                    len(results),
                )
                break

            response = (
                client.table("model_quality_scores")
                .select("model_id, task_type, score")
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

        logger.debug("Loaded %d quality score rows from DB", len(results))
        return results
    except Exception as e:
        logger.warning("Failed to fetch model_quality_scores: %s", e)
        return []
