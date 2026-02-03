"""
Pricing Sync Service - Phase 2 Database Implementation

Automatically syncs pricing from provider APIs to model_pricing database table.
Replaces JSON-based storage with database-first approach.

Features:
- Periodic price sync from provider APIs
- Database-first storage (model_pricing table)
- Change detection and history tracking (model_pricing_history)
- Sync operation logging (pricing_sync_log)
- Cache invalidation for immediate effect
- JSON backup (emergency fallback)
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

from src.config.config import Config
from src.config.supabase_config import get_supabase_client
from src.services.pricing_provider_auditor import PricingProviderAuditor
from src.services.pricing_validation import validate_pricing_update

logger = logging.getLogger(__name__)


class PricingFormat:
    """Pricing format constants."""
    PER_TOKEN = "per_token"
    PER_1K_TOKENS = "per_1k_tokens"
    PER_1M_TOKENS = "per_1m_tokens"


# Provider format mapping
PROVIDER_FORMATS = {
    "openrouter": PricingFormat.PER_TOKEN,  # FIXED: OpenRouter returns per-token pricing
    "featherless": PricingFormat.PER_1M_TOKENS,
    "deepinfra": PricingFormat.PER_1M_TOKENS,
    "together": PricingFormat.PER_1M_TOKENS,  # ✅ Together uses per-1M (input/output keys)
    "fireworks": PricingFormat.PER_1M_TOKENS,
    "groq": PricingFormat.PER_1M_TOKENS,
    "google": PricingFormat.PER_1K_TOKENS,  # ⚠️ Different!
    "google-vertex": PricingFormat.PER_1K_TOKENS,
    "vertex": PricingFormat.PER_1K_TOKENS,
    "vertex-ai": PricingFormat.PER_1K_TOKENS,
    "cerebras": PricingFormat.PER_1M_TOKENS,
    "novita": PricingFormat.PER_1M_TOKENS,
    "nearai": PricingFormat.PER_1M_TOKENS,
    "near": PricingFormat.PER_1M_TOKENS,
    "alibaba-cloud": PricingFormat.PER_1M_TOKENS,
    "alibaba": PricingFormat.PER_1M_TOKENS,
    "cloudflare-workers-ai": PricingFormat.PER_1M_TOKENS,
    "nosana": PricingFormat.PER_1M_TOKENS,
}


class PricingSyncConfig:
    """Configuration for pricing sync"""

    # Which providers to auto-sync (Issue #1038: Expand from 4 to 15 providers)
    # NOTE: Uses Config.PRICING_SYNC_PROVIDERS at runtime (configurable via env var)
    # This is the default fallback list
    AUTO_SYNC_PROVIDERS: list[str] = [
        # Phase 1 (Original 4 providers)
        "openrouter",      # ✅ Has API (per-token format)
        "featherless",     # ✅ Has API (per-1M format)
        "nearai",          # ✅ Has API (per-1M format)
        "alibaba-cloud",   # ✅ Has API (per-1M format)

        # Phase 2 (Issue #1038 - 4 new providers)
        "together",        # ✅ ADDED: Has API (per-1M, input/output keys)
        "fireworks",       # ✅ ADDED: Has API (cents per token)
        "groq",            # ✅ ADDED: Has API (cents per token)
        "deepinfra",       # ✅ ADDED: Has API (cents per token)

        # Phase 3a (Issue #1038 - 3 new providers)
        "cerebras",        # ✅ ADDED: SDK-based, pricing in models.list
        "novita",          # ✅ ADDED: OpenAI-compatible API with pricing
        "nebius",          # ✅ ADDED: OpenAI-compatible API with pricing

        # Future additions (to be implemented)
        # "google",        # 🔄 Vertex AI pricing API research needed
        # "xai",           # 🔄 API research needed (no public models.list)
        # "cloudflare",    # 🔄 Workers AI pricing research needed
        # Add more as provider APIs are discovered and implemented
    ]

    # Don't sync if deviation would exceed this percentage
    MAX_DEVIATION_PCT: float = 200.0  # Increased for price fluctuations

    # Minimum price change to trigger update (in per-token)
    MIN_CHANGE_THRESHOLD: Decimal = Decimal("0.0000001")  # $0.10/1M tokens

    # Pricing variance tolerance (1% = 0.01)
    PRICE_CHANGE_THRESHOLD_PCT: float = 1.0


def get_provider_format(provider_slug: str) -> str:
    """Get pricing format for provider."""
    return PROVIDER_FORMATS.get(provider_slug.lower(), PricingFormat.PER_1M_TOKENS)


def normalize_to_per_token(value: str | float | Decimal, format: str) -> Decimal:
    """
    Normalize pricing value to per-token format.

    Args:
        value: Pricing value
        format: One of PricingFormat values

    Returns:
        Decimal value in per-token format

    Examples:
        >>> normalize_to_per_token("2.50", PricingFormat.PER_1M_TOKENS)
        Decimal('0.0000025')
        >>> normalize_to_per_token(0.00125, PricingFormat.PER_1K_TOKENS)
        Decimal('0.00000125')
    """
    if value is None or value == "":
        return Decimal("0")

    # Handle negative values (OpenRouter uses -1 for dynamic pricing)
    decimal_value = Decimal(str(value))
    if decimal_value < 0:
        return Decimal("-1")

    if format == PricingFormat.PER_TOKEN:
        return decimal_value
    elif format == PricingFormat.PER_1K_TOKENS:
        return decimal_value / Decimal("1000")
    elif format == PricingFormat.PER_1M_TOKENS:
        return decimal_value / Decimal("1000000")
    else:
        raise ValueError(f"Unknown pricing format: {format}")


class PricingSyncService:
    """Service for syncing prices from provider APIs to database"""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.auditor = PricingProviderAuditor()
        self.client = get_supabase_client()

    async def sync_provider_pricing(
        self, provider_slug: str, dry_run: bool = False, triggered_by: str = "manual"
    ) -> Dict[str, Any]:
        """
        Sync pricing from provider API to database.

        Args:
            provider_slug: Provider identifier (e.g., "openrouter")
            dry_run: If True, don't write to database
            triggered_by: Source of sync trigger ('manual', 'scheduler', 'api')

        Returns:
            Sync stats dict
        """
        from src.services.prometheus_metrics import (
            track_pricing_sync,
            record_pricing_sync_models_updated,
            record_pricing_sync_models_skipped,
            record_pricing_sync_models_fetched,
            record_pricing_sync_price_changes,
            record_pricing_sync_error,
        )

        logger.info(f"Starting pricing sync for provider: {provider_slug} (dry_run={dry_run})")

        sync_started_at = datetime.now(timezone.utc)
        stats = {
            "provider": provider_slug,
            "dry_run": dry_run,
            "triggered_by": triggered_by,
            "started_at": sync_started_at.isoformat(),
            "models_fetched": 0,
            "models_updated": 0,
            "models_skipped": 0,
            "models_unchanged": 0,
            "errors": 0,
            "error_details": [],
            "price_changes": [],
        }

        # Log start of sync (in_progress)
        sync_log_id = None
        if not dry_run:
            try:
                sync_log = self.client.table("pricing_sync_log").insert({
                    "provider_slug": provider_slug,
                    "sync_started_at": sync_started_at.isoformat(),
                    "status": "in_progress",
                    "triggered_by": triggered_by
                }).execute()

                if sync_log.data:
                    sync_log_id = sync_log.data[0]["id"]
            except Exception as e:
                logger.warning(f"Could not log sync start: {e}")

        # Track sync operation with Prometheus metrics
        with track_pricing_sync(provider_slug, triggered_by):
            try:
                # Fetch pricing from provider API
                api_data = await self._fetch_provider_pricing(provider_slug)

                if not api_data or not api_data.get("models"):
                    raise Exception(f"No models returned from {provider_slug} API")

                stats["models_fetched"] = len(api_data["models"])
                logger.info(f"Fetched {stats['models_fetched']} models from {provider_slug}")

                # Record models fetched metric
                record_pricing_sync_models_fetched(provider_slug, stats["models_fetched"])

                # Get provider format
                provider_format = get_provider_format(provider_slug)

                # Track skipped models by reason
                skip_reasons = {}

                # Process each model
                for model_id, pricing in api_data["models"].items():
                    try:
                        result = await self._process_model_pricing(
                            model_id,
                            pricing,
                            provider_format,
                            provider_slug,
                            dry_run
                        )

                        if result["status"] == "updated":
                            stats["models_updated"] += 1
                            stats["price_changes"].append(result)
                        elif result["status"] == "skipped":
                            stats["models_skipped"] += 1
                            stats["error_details"].append(result)
                            # Track skip reason
                            reason = result.get("reason", "unknown")
                            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                        elif result["status"] == "unchanged":
                            stats["models_unchanged"] += 1

                    except Exception as e:
                        logger.error(f"Error processing {model_id}: {e}")
                        stats["errors"] += 1
                        stats["error_details"].append({
                            "model_id": model_id,
                            "error": str(e)
                        })
                        # Record error metric
                        error_type = type(e).__name__
                        record_pricing_sync_error(provider_slug, error_type)

                # Record metrics after processing
                record_pricing_sync_models_updated(provider_slug, stats["models_updated"])
                record_pricing_sync_price_changes(provider_slug, len(stats["price_changes"]))

                # Record skipped models by reason
                for reason, count in skip_reasons.items():
                    record_pricing_sync_models_skipped(provider_slug, reason, count)

                # Clear pricing cache (force reload from database)
                if not dry_run and stats["models_updated"] > 0:
                    self._clear_pricing_cache()

                # Log completion
                sync_completed_at = datetime.now(timezone.utc)
                stats["completed_at"] = sync_completed_at.isoformat()
                stats["duration_ms"] = int((sync_completed_at - sync_started_at).total_seconds() * 1000)
                stats["status"] = "success"

                # Update sync log
                if not dry_run and sync_log_id:
                    try:
                        self.client.table("pricing_sync_log").update({
                            "sync_completed_at": sync_completed_at.isoformat(),
                            "status": "success",
                            "models_fetched": stats["models_fetched"],
                            "models_updated": stats["models_updated"],
                            "models_skipped": stats["models_skipped"],
                            "errors": stats["errors"]
                        }).eq("id", sync_log_id).execute()
                    except Exception as e:
                        logger.warning(f"Could not update sync log: {e}")

                logger.info(
                    f"Pricing sync completed for {provider_slug}: "
                    f"{stats['models_updated']} updated, "
                    f"{stats['models_unchanged']} unchanged, "
                    f"{stats['models_skipped']} skipped, "
                    f"{stats['errors']} errors"
                )

                return stats

            except Exception as e:
                logger.error(f"Pricing sync failed for {provider_slug}: {e}")

                # Classify error type for metrics
                error_type = type(e).__name__
                if "API" in str(e) or "fetch" in str(e).lower():
                    error_type = "api_error"
                elif "database" in str(e).lower() or "supabase" in str(e).lower():
                    error_type = "database_error"
                elif "timeout" in str(e).lower():
                    error_type = "timeout_error"

                record_pricing_sync_error(provider_slug, error_type)

                sync_completed_at = datetime.now(timezone.utc)
                stats["completed_at"] = sync_completed_at.isoformat()
                stats["duration_ms"] = int((sync_completed_at - sync_started_at).total_seconds() * 1000)
                stats["status"] = "failed"
                stats["error_message"] = str(e)

                # Update sync log with failure
                if not dry_run and sync_log_id:
                    try:
                        self.client.table("pricing_sync_log").update({
                            "sync_completed_at": sync_completed_at.isoformat(),
                            "status": "failed",
                            "error_message": str(e),
                            "errors": stats["errors"]
                        }).eq("id", sync_log_id).execute()
                    except Exception as log_error:
                        logger.warning(f"Could not update sync log: {log_error}")

                return stats

    async def _process_model_pricing(
        self,
        model_id: str,
        pricing: Dict[str, Any],
        provider_format: str,
        provider_slug: str,
        dry_run: bool
    ) -> Dict[str, Any]:
        """
        Process pricing for a single model.

        Returns:
            Result dict with status: "updated", "skipped", or "unchanged"
        """
        # Find model in database
        model_result = self.client.table("models").select("id").eq(
            "model_id", model_id
        ).eq("is_active", True).limit(1).execute()

        if not model_result.data:
            return {
                "status": "skipped",
                "model_id": model_id,
                "reason": "Model not found in database"
            }

        db_model_id = model_result.data[0]["id"]

        # Normalize pricing to per-token
        input_price = normalize_to_per_token(
            pricing.get("prompt", 0),
            provider_format
        )
        output_price = normalize_to_per_token(
            pricing.get("completion", 0),
            provider_format
        )

        # Skip dynamic pricing (OpenRouter returns -1)
        if input_price < 0 or output_price < 0:
            return {
                "status": "skipped",
                "model_id": model_id,
                "reason": "Dynamic pricing (not fixed)"
            }

        # Skip if both prices are zero
        if input_price == 0 and output_price == 0:
            return {
                "status": "skipped",
                "model_id": model_id,
                "reason": "Zero pricing"
            }

        # Get current pricing from database
        current_result = self.client.table("model_pricing").select(
            "price_per_input_token, price_per_output_token"
        ).eq("model_id", db_model_id).limit(1).execute()

        # Check if pricing changed
        pricing_changed = False
        old_input = None
        old_output = None

        if current_result.data:
            old_input = Decimal(str(current_result.data[0]["price_per_input_token"]))
            old_output = Decimal(str(current_result.data[0]["price_per_output_token"]))

            # Check if change exceeds threshold
            input_changed = abs(old_input - input_price) >= PricingSyncConfig.MIN_CHANGE_THRESHOLD
            output_changed = abs(old_output - output_price) >= PricingSyncConfig.MIN_CHANGE_THRESHOLD

            pricing_changed = input_changed or output_changed

        if not pricing_changed and current_result.data:
            return {
                "status": "unchanged",
                "model_id": model_id
            }

        # Validate pricing before updating (Issue #1038)
        new_pricing_dict = {
            "prompt": float(input_price),
            "completion": float(output_price),
            "image": 0,
            "request": 0
        }
        old_pricing_dict = None
        if old_input is not None and old_output is not None:
            old_pricing_dict = {
                "prompt": float(old_input),
                "completion": float(old_output),
                "image": 0,
                "request": 0
            }

        validation_result = validate_pricing_update(
            model_id,
            new_pricing_dict,
            old_pricing_dict
        )

        # Log validation warnings
        if validation_result["warnings"]:
            logger.warning(
                f"Pricing validation warnings for {model_id}: "
                f"{', '.join(validation_result['warnings'])}"
            )

        # Reject if validation failed
        if not validation_result["is_valid"]:
            logger.error(
                f"Pricing validation failed for {model_id}: "
                f"{', '.join(validation_result['errors'])}"
            )
            return {
                "status": "skipped",
                "model_id": model_id,
                "reason": f"Validation failed: {validation_result['errors'][0]}",
                "validation_errors": validation_result["errors"],
                "validation_warnings": validation_result["warnings"]
            }

        # Prepare pricing data
        pricing_data = {
            "model_id": db_model_id,
            "price_per_input_token": float(input_price),
            "price_per_output_token": float(output_price),
            "pricing_source": f"provider_api:{provider_slug}",
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

        if dry_run:
            return {
                "status": "updated",
                "model_id": model_id,
                "old_input": float(old_input) if old_input else None,
                "old_output": float(old_output) if old_output else None,
                "new_input": float(input_price),
                "new_output": float(output_price),
                "dry_run": True
            }

        # Upsert to model_pricing table
        self.client.table("model_pricing").upsert(pricing_data, on_conflict="model_id").execute()

        # Log to pricing history if changed
        if current_result.data and (old_input is not None and old_output is not None):
            self.client.table("model_pricing_history").insert({
                "model_id": db_model_id,
                "price_per_input_token": float(input_price),
                "price_per_output_token": float(output_price),
                "previous_input_price": float(old_input),
                "previous_output_price": float(old_output),
                "changed_at": datetime.now(timezone.utc).isoformat(),
                "changed_by": f"api_sync:{provider_slug}"
            }).execute()

            logger.info(
                f"Pricing updated: {model_id} - "
                f"Input: ${old_input} → ${input_price}, "
                f"Output: ${old_output} → ${output_price}"
            )

        return {
            "status": "updated",
            "model_id": model_id,
            "old_input": float(old_input) if old_input else None,
            "old_output": float(old_output) if old_output else None,
            "new_input": float(input_price),
            "new_output": float(output_price)
        }

    async def _fetch_provider_pricing(self, provider_slug: str) -> Dict[str, Any]:
        """
        Fetch pricing from provider API.

        Reuses existing implementation from pricing_provider_auditor.
        """
        methods = {
            "openrouter": self.auditor.audit_openrouter,
            "featherless": self.auditor.audit_featherless,
            "nearai": self.auditor.audit_nearai,
            "near": self.auditor.audit_nearai,
            "alibaba-cloud": self.auditor.audit_alibaba_cloud,
            "alibaba": self.auditor.audit_alibaba_cloud,
            "together": self.auditor.audit_together,
            "fireworks": self.auditor.audit_fireworks,
            "groq": self.auditor.audit_groq,
            "deepinfra": self.auditor.audit_deepinfra,
            "cerebras": self.auditor.audit_cerebras,
            "novita": self.auditor.audit_novita,
            "nebius": self.auditor.audit_nebius,
        }

        if provider_slug.lower() not in methods:
            raise ValueError(f"Unsupported provider: {provider_slug}")

        result = await methods[provider_slug.lower()]()

        if result.status != "success":
            raise Exception(f"Provider API fetch failed: {result.error_message}")

        return {"models": result.models}

    def _clear_pricing_cache(self) -> None:
        """
        Clear pricing cache to force reload from database.

        This ensures that pricing changes take effect immediately.
        """
        try:
            from src.services.pricing import clear_pricing_cache
            clear_pricing_cache()
            logger.info("Pricing cache cleared successfully")
        except Exception as e:
            logger.warning(f"Could not clear pricing cache: {e}")

    async def sync_all_providers(
        self, dry_run: bool = False, triggered_by: str = "manual"
    ) -> Dict[str, Any]:
        """
        Sync pricing from all configured providers.

        Args:
            dry_run: If True, don't write changes
            triggered_by: Source of sync trigger

        Returns:
            Combined sync results
        """
        # Phase 2.5: Use configured providers from env var (falls back to AUTO_SYNC_PROVIDERS)
        try:
            from src.config.config import Config
            providers = Config.PRICING_SYNC_PROVIDERS
        except Exception:
            providers = PricingSyncConfig.AUTO_SYNC_PROVIDERS

        results = {}

        logger.info(f"Starting sync for {len(providers)} providers (dry_run={dry_run})...")

        for provider in providers:
            result = await self.sync_provider_pricing(
                provider, dry_run=dry_run, triggered_by=triggered_by
            )
            results[provider] = result

        # Summary
        total_updated = sum(r.get("models_updated", 0) for r in results.values())
        total_errors = sum(r.get("errors", 0) for r in results.values())
        total_skipped = sum(r.get("models_skipped", 0) for r in results.values())

        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dry_run": dry_run,
            "triggered_by": triggered_by,
            "providers_synced": len([r for r in results.values() if r["status"] == "success"]),
            "providers_failed": len([r for r in results.values() if r["status"] == "failed"]),
            "total_models_updated": total_updated,
            "total_models_skipped": total_skipped,
            "total_errors": total_errors,
            "results": results,
        }

        logger.info(
            f"Sync completed: {summary['providers_synced']} providers, "
            f"{total_updated} models updated, {total_errors} errors"
        )

        return summary

    def get_sync_history(self, provider_slug: str | None = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent sync history from database.

        Args:
            provider_slug: Filter by provider (optional)
            limit: Max number of records to return

        Returns:
            List of sync log entries
        """
        try:
            query = self.client.table("pricing_sync_log").select("*").order(
                "sync_started_at", desc=True
            ).limit(limit)

            if provider_slug:
                query = query.eq("provider_slug", provider_slug)

            result = query.execute()

            return result.data if result.data else []

        except Exception as e:
            logger.error(f"Error fetching sync history: {e}")
            return []


# Convenience functions

async def run_scheduled_sync(triggered_by: str = "scheduler") -> Dict[str, Any]:
    """Run scheduled pricing sync (for background tasks)."""
    service = PricingSyncService()
    return await service.sync_all_providers(dry_run=False, triggered_by=triggered_by)


async def run_dry_run_sync() -> Dict[str, Any]:
    """Run dry-run sync to see what would change."""
    service = PricingSyncService()
    return await service.sync_all_providers(dry_run=True, triggered_by="manual")


def get_pricing_sync_service() -> PricingSyncService:
    """Get pricing sync service instance."""
    return PricingSyncService()
