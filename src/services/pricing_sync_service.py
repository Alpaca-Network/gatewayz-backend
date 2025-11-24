"""
Pricing Sync Service

Automatically syncs pricing from provider APIs to manual_pricing.json.
Runs on a schedule to keep pricing data current.

Features:
- Periodic price sync from provider APIs
- Smart merging of pricing data (preserves manual overrides)
- Change detection and alerting
- Automatic backup before updates
- Validation and sanity checks
- Rollback capability
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import shutil

from src.services.pricing_provider_auditor import PricingProviderAuditor
from src.services.pricing_lookup import load_manual_pricing
from src.services.pricing_audit_service import get_pricing_audit_service

logger = logging.getLogger(__name__)

# Paths (relative to repository root)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRICING_FILE = DATA_DIR / "manual_pricing.json"
BACKUP_DIR = DATA_DIR / "pricing_backups"
SYNC_LOG_FILE = DATA_DIR / "pricing_sync.log"

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)


@dict
class PricingSyncConfig:
    """Configuration for pricing sync"""

    # Which providers to auto-sync
    AUTO_SYNC_PROVIDERS: List[str] = [
        "openrouter",
        "featherless",
        "nearai",
        "alibaba-cloud",
    ]

    # Don't sync if deviation would exceed this percentage
    MAX_DEVIATION_PCT: float = 50.0

    # Minimum price change to trigger update (in USD)
    MIN_CHANGE_THRESHOLD: float = 0.0001

    # How long to keep backups
    BACKUP_RETENTION_DAYS: int = 30

    # Skip models that have manual overrides
    PRESERVE_MANUAL_OVERRIDES: bool = True

    # Manual override markers
    MANUAL_OVERRIDE_MARKER: str = "_manual_override"


class PricingSyncService:
    """Service for syncing prices from provider APIs"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.auditor = PricingProviderAuditor()
        self.audit_service = get_pricing_audit_service()
        self.sync_history = []

    async def sync_provider_pricing(
        self, provider_name: str, dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Sync pricing for a specific provider.

        Args:
            provider_name: Provider to sync
            dry_run: If True, don't write changes

        Returns:
            Sync result with details
        """
        result = {
            "provider": provider_name,
            "dry_run": dry_run,
            "timestamp": datetime.utcnow().isoformat(),
            "status": "pending",
            "models_updated": 0,
            "models_skipped": 0,
            "price_changes": [],
            "errors": [],
        }

        try:
            # Load current manual pricing
            current_pricing = load_manual_pricing()

            # Fetch from provider API
            api_data = await self._fetch_provider_pricing(provider_name)

            if not api_data or api_data["status"] != "success":
                result["status"] = "error"
                result["errors"].append(
                    f"Failed to fetch pricing from {provider_name}: {api_data.get('error_message', 'Unknown error')}"
                )
                return result

            # Get provider's pricing section
            provider_key = provider_name.lower()
            if provider_key not in current_pricing:
                current_pricing[provider_key] = {}

            # Merge new pricing
            changes = self._merge_pricing(
                current_pricing[provider_key], api_data.get("models", {})
            )

            result["models_updated"] = len([c for c in changes if c["type"] == "updated"])
            result["models_skipped"] = len([c for c in changes if c["type"] == "skipped"])
            result["price_changes"] = changes
            result["status"] = "success"

            # Write changes if not dry-run
            if not dry_run and changes:
                await self._write_pricing_changes(current_pricing, provider_name)
                logger.info(f"Synced pricing for {provider_name}: {len(changes)} changes")

            return result

        except Exception as e:
            logger.error(f"Error syncing {provider_name} pricing: {e}")
            result["status"] = "error"
            result["errors"].append(str(e))
            return result

    async def _fetch_provider_pricing(self, provider_name: str) -> Dict[str, Any]:
        """Fetch pricing from provider API."""
        methods = {
            "openrouter": self.auditor.audit_openrouter,
            "featherless": self.auditor.audit_featherless,
            "nearai": self.auditor.audit_nearai,
            "near": self.auditor.audit_nearai,
            "alibaba-cloud": self.auditor.audit_alibaba_cloud,
            "alibaba": self.auditor.audit_alibaba_cloud,
        }

        if provider_name.lower() not in methods:
            return {
                "status": "error",
                "error_message": f"Unknown provider: {provider_name}",
            }

        try:
            result = await methods[provider_name.lower()]()
            return {
                "status": result.status,
                "models": result.models,
                "error_message": result.error_message,
            }
        except Exception as e:
            return {"status": "error", "error_message": str(e)}

    def _merge_pricing(
        self, stored_pricing: Dict[str, Any], api_pricing: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Merge API pricing with stored pricing, applying rules.

        Args:
            stored_pricing: Current stored pricing
            api_pricing: New pricing from API

        Returns:
            List of changes made
        """
        changes = []

        for model_id, api_model_pricing in api_pricing.items():
            # Normalize model ID
            normalized_id = model_id.lower()

            # Find existing (case-insensitive)
            existing_entry = None
            existing_key = None
            for stored_key, stored_data in stored_pricing.items():
                if stored_key.lower() == normalized_id:
                    existing_entry = stored_data
                    existing_key = stored_key
                    break

            # Check if manual override
            if existing_entry and existing_entry.get(PricingSyncConfig.MANUAL_OVERRIDE_MARKER):
                changes.append(
                    {
                        "model_id": model_id,
                        "type": "skipped",
                        "reason": "Manual override marker present",
                    }
                )
                continue

            # Compare prices
            if existing_entry:
                change_detected = self._has_price_change(existing_entry, api_model_pricing)

                if change_detected:
                    changes.append(
                        {
                            "model_id": model_id,
                            "type": "updated",
                            "old_pricing": existing_entry.copy(),
                            "new_pricing": api_model_pricing.copy(),
                        }
                    )
                    stored_pricing[existing_key] = api_model_pricing
                else:
                    changes.append(
                        {
                            "model_id": model_id,
                            "type": "unchanged",
                        }
                    )
            else:
                # New model
                changes.append(
                    {
                        "model_id": model_id,
                        "type": "new",
                        "pricing": api_model_pricing.copy(),
                    }
                )
                stored_pricing[model_id] = api_model_pricing

        return changes

    def _has_price_change(
        self, existing: Dict[str, Any], new: Dict[str, Any]
    ) -> bool:
        """Check if prices have changed significantly."""
        min_threshold = PricingSyncConfig.MIN_CHANGE_THRESHOLD
        max_deviation = PricingSyncConfig.MAX_DEVIATION_PCT

        for field in ["prompt", "completion", "request", "image"]:
            old_price = float(existing.get(field, 0) or 0)
            new_price = float(new.get(field, 0) or 0)

            if old_price == 0 and new_price == 0:
                continue

            if old_price == 0 or new_price == 0:
                # One is zero, other isn't - significant change
                return True

            change = abs(new_price - old_price)
            if change < min_threshold:
                continue

            change_pct = (change / old_price) * 100
            if change_pct > max_deviation:
                # Too large a change - likely an error
                logger.warning(
                    f"Price change {change_pct:.1f}% exceeds max deviation {max_deviation}%"
                )
                continue

            if change_pct >= 1.0:  # At least 1% change
                return True

        return False

    async def _write_pricing_changes(
        self, pricing_data: Dict[str, Any], provider_name: str
    ) -> None:
        """Write updated pricing to file."""
        # Create backup first
        backup_file = self._create_backup()

        try:
            # Update metadata
            if "_metadata" not in pricing_data:
                pricing_data["_metadata"] = {}

            pricing_data["_metadata"]["last_updated"] = datetime.utcnow().strftime(
                "%Y-%m-%d"
            )
            pricing_data["_metadata"]["last_sync_providers"] = (
                pricing_data["_metadata"].get("last_sync_providers", [])
            )

            if provider_name not in pricing_data["_metadata"]["last_sync_providers"]:
                pricing_data["_metadata"]["last_sync_providers"].append(provider_name)

            # Write to file
            with open(PRICING_FILE, "w") as f:
                json.dump(pricing_data, f, indent=2)

            # Log sync
            self._log_sync(provider_name, "success", f"Updated pricing for {provider_name}")

        except Exception as e:
            logger.error(f"Error writing pricing changes: {e}")
            # Restore from backup
            self._restore_backup(backup_file)
            self._log_sync(provider_name, "failed", f"Rollback performed: {str(e)}")
            raise

    def _create_backup(self) -> Path:
        """Create backup of current pricing file."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_file = BACKUP_DIR / f"pricing_backup_{timestamp}.json"

        if PRICING_FILE.exists():
            shutil.copy2(PRICING_FILE, backup_file)
            logger.info(f"Created backup: {backup_file}")

        return backup_file

    def _restore_backup(self, backup_file: Path) -> None:
        """Restore pricing from backup file."""
        if backup_file.exists():
            shutil.copy2(backup_file, PRICING_FILE)
            logger.info(f"Restored from backup: {backup_file}")

    def cleanup_old_backups(self, retention_days: int = PricingSyncConfig.BACKUP_RETENTION_DAYS):
        """Remove old backup files."""
        cutoff = datetime.utcnow() - timedelta(days=retention_days)

        for backup_file in BACKUP_DIR.glob("pricing_backup_*.json"):
            if datetime.fromtimestamp(backup_file.stat().st_mtime) < cutoff:
                backup_file.unlink()
                logger.info(f"Deleted old backup: {backup_file}")

    def _log_sync(self, provider: str, status: str, message: str) -> None:
        """Log sync operation."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "provider": provider,
            "status": status,
            "message": message,
        }

        with open(SYNC_LOG_FILE, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        logger.info(f"[{provider}] {status}: {message}")

    async def sync_all_providers(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Sync pricing from all configured providers.

        Args:
            dry_run: If True, don't write changes

        Returns:
            Combined sync results
        """
        providers = PricingSyncConfig.AUTO_SYNC_PROVIDERS
        results = {}

        logger.info(f"Starting sync for {len(providers)} providers (dry_run={dry_run})...")

        for provider in providers:
            result = await self.sync_provider_pricing(provider, dry_run=dry_run)
            results[provider] = result

        # Summary
        total_updated = sum(r.get("models_updated", 0) for r in results.values())
        total_errors = sum(len(r.get("errors", [])) for r in results.values())

        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "dry_run": dry_run,
            "providers_synced": len([r for r in results.values() if r["status"] == "success"]),
            "total_models_updated": total_updated,
            "total_errors": total_errors,
            "results": results,
        }

        if not dry_run and total_updated > 0:
            self._log_sync("all", "success", f"Updated {total_updated} models across {len(providers)} providers")

        return summary

    def get_sync_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent sync history."""
        history = []

        if SYNC_LOG_FILE.exists():
            with open(SYNC_LOG_FILE, "r") as f:
                lines = f.readlines()

            for line in lines[-limit:]:
                try:
                    history.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue

        return history


async def run_scheduled_sync() -> Dict[str, Any]:
    """Run scheduled pricing sync (for background tasks)."""
    service = PricingSyncService()
    result = await service.sync_all_providers(dry_run=False)
    service.cleanup_old_backups()
    return result


async def run_dry_run_sync() -> Dict[str, Any]:
    """Run dry-run sync to see what would change."""
    service = PricingSyncService()
    return await service.sync_all_providers(dry_run=True)


def get_pricing_sync_service() -> PricingSyncService:
    """Get pricing sync service instance."""
    return PricingSyncService()
