"""
Background Pricing Sync Service

Automatically syncs model pricing from providers and keeps model_pricing table updated.
Integrates with the existing model sync system.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.config.supabase_config import get_supabase_client
from src.services.model_pricing_service import bulk_upsert_pricing, clear_pricing_cache
from src.services.pricing_normalization import (
    normalize_to_per_token,
    get_provider_format,
    PricingFormat,
)

logger = logging.getLogger(__name__)


class PricingSyncService:
    """Service for syncing pricing from providers to model_pricing table"""

    def __init__(self):
        self.supabase = get_supabase_client()

    def sync_pricing_for_models(self, model_ids: list[int]) -> dict:
        """
        Sync pricing for specific models

        Args:
            model_ids: List of model IDs to sync

        Returns:
            Dict with stats: {synced: 10, failed: 0, skipped: 5}
        """
        logger.info(f"Syncing pricing for {len(model_ids)} models")

        stats = {"synced": 0, "failed": 0, "skipped": 0}
        pricing_records = []

        # Fetch models
        try:
            response = (
                self.supabase.table("models")
                .select("*")
                .in_("id", model_ids)
                .execute()
            )
            models = response.data
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return stats

        for model in models:
            try:
                pricing = self._extract_and_normalize_pricing(model)

                if pricing:
                    pricing_records.append(pricing)
                    stats["synced"] += 1
                else:
                    stats["skipped"] += 1

            except Exception as e:
                logger.error(f"Error processing model {model.get('id')}: {e}")
                stats["failed"] += 1

        # Bulk upsert
        if pricing_records:
            try:
                success, errors = bulk_upsert_pricing(pricing_records)
                logger.info(f"Bulk upsert: {success} success, {errors} errors")
            except Exception as e:
                logger.error(f"Error bulk upserting: {e}")

        return stats

    def sync_all_models(self) -> dict:
        """
        Sync pricing for ALL models in the database

        Returns:
            Dict with stats
        """
        logger.info("Starting full pricing sync for all models")

        try:
            # Get all model IDs
            response = self.supabase.table("models").select("id").execute()
            model_ids = [m["id"] for m in response.data]

            logger.info(f"Found {len(model_ids)} models to sync")

            # Sync in batches
            batch_size = 100
            total_stats = {"synced": 0, "failed": 0, "skipped": 0}

            for i in range(0, len(model_ids), batch_size):
                batch = model_ids[i : i + batch_size]
                batch_stats = self.sync_pricing_for_models(batch)

                total_stats["synced"] += batch_stats["synced"]
                total_stats["failed"] += batch_stats["failed"]
                total_stats["skipped"] += batch_stats["skipped"]

                logger.info(
                    f"Batch {i//batch_size + 1}: "
                    f"synced={batch_stats['synced']}, "
                    f"failed={batch_stats['failed']}, "
                    f"skipped={batch_stats['skipped']}"
                )

            logger.info(f"Full sync complete: {total_stats}")
            return total_stats

        except Exception as e:
            logger.error(f"Error in full sync: {e}")
            return {"synced": 0, "failed": 0, "skipped": 0, "error": str(e)}

    def sync_stale_pricing(self, hours: int = 24) -> dict:
        """
        Sync pricing for models that haven't been updated recently

        Args:
            hours: Number of hours to consider pricing stale

        Returns:
            Dict with stats
        """
        logger.info(f"Syncing stale pricing (older than {hours} hours)")

        try:
            # Get models with stale pricing
            cutoff = datetime.now() - timedelta(hours=hours)

            response = (
                self.supabase.table("model_pricing")
                .select("model_id")
                .lt("last_updated", cutoff.isoformat())
                .execute()
            )

            model_ids = [m["model_id"] for m in response.data]
            logger.info(f"Found {len(model_ids)} models with stale pricing")

            if not model_ids:
                return {"synced": 0, "failed": 0, "skipped": 0}

            return self.sync_pricing_for_models(model_ids)

        except Exception as e:
            logger.error(f"Error syncing stale pricing: {e}")
            return {"synced": 0, "failed": 0, "skipped": 0, "error": str(e)}

    def sync_provider_models(self, provider: str) -> dict:
        """
        Sync pricing for all models from a specific provider

        Args:
            provider: Provider/gateway name

        Returns:
            Dict with stats
        """
        logger.info(f"Syncing pricing for provider: {provider}")

        try:
            response = (
                self.supabase.table("models")
                .select("id")
                .eq("source_gateway", provider)
                .execute()
            )

            model_ids = [m["id"] for m in response.data]
            logger.info(f"Found {len(model_ids)} models for {provider}")

            return self.sync_pricing_for_models(model_ids)

        except Exception as e:
            logger.error(f"Error syncing provider {provider}: {e}")
            return {"synced": 0, "failed": 0, "skipped": 0, "error": str(e)}

    def _extract_and_normalize_pricing(self, model: dict) -> Optional[dict]:
        """
        Extract pricing from model and normalize to per-token format

        Args:
            model: Model dict from database

        Returns:
            Normalized pricing dict or None if no pricing
        """
        model_id = model.get("id")

        # Get source_gateway from metadata or provider relationship
        metadata = model.get("metadata", {}) or {}
        source_gateway = (
            metadata.get("source_gateway") or
            ""
        ).lower()

        # Get pricing from metadata.pricing_raw (new location after migration)
        # Pricing columns were removed from the models table and moved to metadata
        pricing_raw = metadata.get("pricing_raw", {}) or {}

        pricing_prompt = pricing_raw.get("prompt")
        pricing_completion = pricing_raw.get("completion")
        pricing_image = pricing_raw.get("image")
        pricing_request = pricing_raw.get("request")

        # Skip if no pricing
        if not pricing_prompt and not pricing_completion:
            return None

        # Get provider format
        provider_format = get_provider_format(source_gateway)

        # Normalize
        normalized = {
            "model_id": model_id,
            "pricing_source": model.get("pricing_source", "provider"),
        }

        if pricing_prompt is not None:
            norm_price = normalize_to_per_token(pricing_prompt, provider_format)
            normalized["price_per_input_token"] = float(norm_price) if norm_price else 0.0
        else:
            normalized["price_per_input_token"] = 0.0

        if pricing_completion is not None:
            norm_price = normalize_to_per_token(pricing_completion, provider_format)
            normalized["price_per_output_token"] = float(norm_price) if norm_price else 0.0
        else:
            normalized["price_per_output_token"] = 0.0

        if pricing_image is not None:
            norm_price = normalize_to_per_token(pricing_image, provider_format)
            normalized["price_per_image_token"] = float(norm_price) if norm_price else None

        if pricing_request is not None:
            normalized["price_per_request"] = float(pricing_request)

        return normalized


# Global instance
_sync_service: Optional[PricingSyncService] = None


def get_pricing_sync_service() -> PricingSyncService:
    """Get or create the pricing sync service instance"""
    global _sync_service
    if _sync_service is None:
        _sync_service = PricingSyncService()
    return _sync_service


async def run_periodic_sync(interval_hours: int = 24):
    """
    Run pricing sync periodically

    Args:
        interval_hours: How often to run (default: daily)
    """
    service = get_pricing_sync_service()

    while True:
        try:
            logger.info(f"Starting periodic pricing sync (every {interval_hours}h)")

            # Sync all models
            stats = service.sync_all_models()

            logger.info(f"Periodic sync complete: {stats}")

            # Clear cache after sync
            clear_pricing_cache()

        except Exception as e:
            logger.error(f"Error in periodic sync: {e}")

        # Wait for next sync
        await asyncio.sleep(interval_hours * 3600)


def sync_pricing_on_model_update(model_ids: list[int]):
    """
    Hook to sync pricing when models are updated

    Call this after fetching new models from providers

    Args:
        model_ids: List of model IDs that were updated
    """
    if not model_ids:
        return

    logger.info(f"Syncing pricing for {len(model_ids)} updated models")

    service = get_pricing_sync_service()
    stats = service.sync_pricing_for_models(model_ids)

    logger.info(f"Pricing sync after model update: {stats}")
    return stats
