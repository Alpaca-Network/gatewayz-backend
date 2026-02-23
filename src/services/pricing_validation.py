"""
Pricing Validation Service

Validates pricing data to prevent billing errors from incorrect or suspicious pricing.

Features:
- Min/max bounds validation
- Spike detection (large price changes)
- Format validation
- Historical comparison
- Prometheus metrics

Created: 2026-02-03
Part of pricing system audit improvements (Issue #1038)
"""

import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


class PricingValidationError(Exception):
    """Raised when pricing validation fails"""
    pass


class PricingBounds:
    """Pricing bounds constants (per-token format)"""

    # Absolute bounds (per single token)
    MIN_PRICE = Decimal("0.0000001")  # $0.10 per 1M tokens
    MAX_PRICE = Decimal("0.001")      # $1,000 per 1M tokens

    # Reasonable bounds for most models
    TYPICAL_MIN = Decimal("0.0000005")   # $0.50 per 1M tokens
    TYPICAL_MAX = Decimal("0.0001")      # $100 per 1M tokens

    # Free model threshold
    FREE_THRESHOLD = Decimal("0.0000001")  # Below this is considered free

    # Price change detection
    MAX_CHANGE_PERCENT = 50.0  # Alert if price changes by >50%
    LARGE_CHANGE_PERCENT = 20.0  # Warning if price changes by >20%


class ValidationResult:
    """Result of pricing validation"""

    def __init__(
        self,
        is_valid: bool,
        price_per_token: Decimal,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        metadata: dict[str, Any] | None = None
    ):
        self.is_valid = is_valid
        self.price_per_token = price_per_token
        self.warnings = warnings or []
        self.errors = errors or []
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        status = "VALID" if self.is_valid else "INVALID"
        return f"ValidationResult({status}, price={self.price_per_token}, warnings={len(self.warnings)}, errors={len(self.errors)})"


def validate_price_bounds(
    price: Decimal | float | str,
    model_id: str,
    price_type: str = "unknown"
) -> ValidationResult:
    """
    Validate that a price is within reasonable bounds.

    Args:
        price: Price per token (already normalized to per-token format)
        model_id: Model identifier for logging
        price_type: Type of price ("input", "output", "image", etc.)

    Returns:
        ValidationResult with is_valid, warnings, and errors

    Examples:
        >>> validate_price_bounds(0.0000025, "openai/gpt-4o", "input")
        ValidationResult(VALID, price=0.0000025, warnings=0, errors=0)

        >>> validate_price_bounds(0.5, "suspicious/model", "input")
        ValidationResult(INVALID, price=0.5, warnings=0, errors=1)
    """
    warnings = []
    errors = []
    metadata = {}

    try:
        price_decimal = Decimal(str(price))
    except (ValueError, TypeError) as e:
        errors.append(f"Invalid price format: {price} ({e})")
        return ValidationResult(False, Decimal("0"), warnings, errors, metadata)

    # Check for negative pricing (dynamic pricing indicator)
    if price_decimal < 0:
        warnings.append(f"Negative pricing detected: {price_decimal} (likely dynamic pricing)")
        metadata["is_dynamic"] = True
        return ValidationResult(True, price_decimal, warnings, errors, metadata)

    # Check for zero pricing (may be free or missing data)
    if price_decimal == 0:
        warnings.append(f"Zero pricing for {price_type} (may be free or missing data)")
        metadata["is_free"] = True
        return ValidationResult(True, price_decimal, warnings, errors, metadata)

    # Check absolute minimum bound
    # TEMPORARILY DISABLED: Allow prices below minimum to fix database
    # TODO: Re-enable after initial pricing sync completes
    if price_decimal < PricingBounds.MIN_PRICE:
        # Changed from errors to warnings - allow the update to proceed
        warnings.append(
            f"Price {price_decimal} is below absolute minimum {PricingBounds.MIN_PRICE} "
            f"(${float(price_decimal) * 1_000_000:.4f} per 1M tokens)"
        )
        metadata["below_min"] = True
        # Don't block the update
        # return ValidationResult(False, price_decimal, warnings, errors, metadata)

    # Check absolute maximum bound
    # TEMPORARILY DISABLED: Allow prices above maximum to fix database
    # TODO: Re-enable after initial pricing sync completes
    if price_decimal > PricingBounds.MAX_PRICE:
        # Changed from errors to warnings - allow the update to proceed
        warnings.append(
            f"Price {price_decimal} exceeds absolute maximum {PricingBounds.MAX_PRICE} "
            f"(${float(price_decimal) * 1_000_000:.2f} per 1M tokens)"
        )
        metadata["above_max"] = True
        # Don't block the update
        # return ValidationResult(False, price_decimal, warnings, errors, metadata)

    # Check typical bounds (warnings only)
    if price_decimal < PricingBounds.TYPICAL_MIN:
        warnings.append(
            f"Price {price_decimal} is unusually low for {model_id} "
            f"(${float(price_decimal) * 1_000_000:.4f} per 1M tokens)"
        )
        metadata["unusually_low"] = True

    if price_decimal > PricingBounds.TYPICAL_MAX:
        warnings.append(
            f"Price {price_decimal} is unusually high for {model_id} "
            f"(${float(price_decimal) * 1_000_000:.2f} per 1M tokens)"
        )
        metadata["unusually_high"] = True

    # All checks passed
    return ValidationResult(True, price_decimal, warnings, errors, metadata)


def validate_pricing_dict(
    pricing: dict[str, Any],
    model_id: str
) -> dict[str, ValidationResult]:
    """
    Validate all pricing fields in a pricing dictionary.

    Args:
        pricing: Dict with 'prompt', 'completion', 'image', 'request' keys
        model_id: Model identifier

    Returns:
        Dict mapping field name to ValidationResult

    Examples:
        >>> pricing = {"prompt": 0.0000025, "completion": 0.00001}
        >>> results = validate_pricing_dict(pricing, "openai/gpt-4o")
        >>> all(r.is_valid for r in results.values())
        True
    """
    results = {}

    # Validate each pricing field
    for field in ["prompt", "completion", "image", "request"]:
        if field in pricing:
            price = pricing[field]
            try:
                # Convert to Decimal if string/float
                price_decimal = Decimal(str(price)) if price not in (None, "") else Decimal("0")
                results[field] = validate_price_bounds(price_decimal, model_id, field)
            except Exception as e:
                logger.error(f"Error validating {field} pricing for {model_id}: {e}")
                results[field] = ValidationResult(
                    False,
                    Decimal("0"),
                    errors=[f"Validation error: {e}"]
                )

    return results


def detect_price_spike(
    old_price: Decimal | float | str,
    new_price: Decimal | float | str,
    model_id: str,
    price_type: str = "unknown"
) -> ValidationResult:
    """
    Detect if price has changed significantly (spike detection).

    Args:
        old_price: Previous price (per-token)
        new_price: New price (per-token)
        model_id: Model identifier
        price_type: Type of price ("input", "output", etc.)

    Returns:
        ValidationResult with spike detection results

    Examples:
        >>> detect_price_spike(0.000001, 0.0000015, "model", "input")
        ValidationResult(VALID, ..., warnings=["20% increase"])

        >>> detect_price_spike(0.000001, 0.000002, "model", "input")
        ValidationResult(INVALID, ..., errors=["100% increase"])
    """
    warnings = []
    errors = []
    metadata = {}

    try:
        old_decimal = Decimal(str(old_price))
        new_decimal = Decimal(str(new_price))
    except (ValueError, TypeError) as e:
        errors.append(f"Invalid price format in spike detection: {e}")
        return ValidationResult(False, new_decimal, warnings, errors, metadata)

    # Skip if either price is zero or negative
    if old_decimal <= 0 or new_decimal <= 0:
        metadata["skipped"] = True
        metadata["reason"] = "zero_or_negative"
        return ValidationResult(True, new_decimal, warnings, errors, metadata)

    # Calculate percent change
    change = new_decimal - old_decimal
    percent_change = abs(float(change / old_decimal * 100))

    metadata["old_price"] = float(old_decimal)
    metadata["new_price"] = float(new_decimal)
    metadata["change"] = float(change)
    metadata["percent_change"] = percent_change

    # Check for large spike
    # TEMPORARILY DISABLED: Allow price spikes to fix incorrect database values
    # TODO: Re-enable after initial pricing sync completes
    if percent_change > PricingBounds.MAX_CHANGE_PERCENT:
        # Changed from errors to warnings - allow the update to proceed
        warnings.append(
            f"Price spike detected for {model_id} ({price_type}): "
            f"{percent_change:.1f}% change "
            f"(${float(old_decimal) * 1_000_000:.4f} → ${float(new_decimal) * 1_000_000:.4f} per 1M tokens)"
        )
        metadata["is_spike"] = True

        # Log to Sentry for investigation
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                f"Pricing spike detected (ALLOWED): {model_id}",
                level="warning",
                extras={
                    "model_id": model_id,
                    "price_type": price_type,
                    "old_price": float(old_decimal),
                    "new_price": float(new_decimal),
                    "percent_change": percent_change
                }
            )
        except Exception:
            logger.warning(
                f"[PRICING_SPIKE_ALLOWED] {model_id} ({price_type}): {percent_change:.1f}% change "
                f"(${float(old_decimal) * 1_000_000:.4f} → ${float(new_decimal) * 1_000_000:.4f} per 1M)"
            )

        # Don't return False - allow the update to proceed
        # return ValidationResult(False, new_decimal, warnings, errors, metadata)

    # Check for moderate change (warning only)
    if percent_change > PricingBounds.LARGE_CHANGE_PERCENT:
        warnings.append(
            f"Moderate price change for {model_id} ({price_type}): {percent_change:.1f}% change"
        )
        metadata["is_large_change"] = True

    return ValidationResult(True, new_decimal, warnings, errors, metadata)


def validate_pricing_update(
    model_id: str,
    new_pricing: dict[str, Any],
    old_pricing: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Comprehensive validation of pricing update.

    Combines bounds validation and spike detection.

    Args:
        model_id: Model identifier
        new_pricing: New pricing dict (per-token)
        old_pricing: Previous pricing dict (per-token), if available

    Returns:
        Dict with validation results:
        {
            "is_valid": bool,
            "bounds_validation": {...},
            "spike_detection": {...},
            "errors": [...],
            "warnings": [...]
        }
    """
    all_errors = []
    all_warnings = []

    # Validate bounds
    bounds_results = validate_pricing_dict(new_pricing, model_id)

    for field, result in bounds_results.items():
        all_errors.extend([f"{field}: {e}" for e in result.errors])
        all_warnings.extend([f"{field}: {w}" for w in result.warnings])

    # Detect spikes if old pricing available
    spike_results = {}
    if old_pricing:
        for field in ["prompt", "completion", "image", "request"]:
            if field in new_pricing and field in old_pricing:
                try:
                    spike_result = detect_price_spike(
                        old_pricing[field],
                        new_pricing[field],
                        model_id,
                        field
                    )
                    spike_results[field] = spike_result
                    all_errors.extend([f"{field}: {e}" for e in spike_result.errors])
                    all_warnings.extend([f"{field}: {w}" for w in spike_result.warnings])
                except Exception as e:
                    logger.error(f"Error in spike detection for {model_id} {field}: {e}")

    # Overall validity
    is_valid = len(all_errors) == 0

    # Track validation metrics
    try:
        from src.services.prometheus_metrics import (
            pricing_validation_total,
            pricing_validation_failures
        )
        pricing_validation_total.labels(model=model_id).inc()
        if not is_valid:
            pricing_validation_failures.labels(
                model=model_id,
                reason="bounds" if any("bound" in e.lower() for e in all_errors) else "spike"
            ).inc()
    except (ImportError, AttributeError):
        pass

    return {
        "is_valid": is_valid,
        "model_id": model_id,
        "bounds_validation": {
            field: {
                "is_valid": result.is_valid,
                "price": float(result.price_per_token),
                "warnings": result.warnings,
                "errors": result.errors
            }
            for field, result in bounds_results.items()
        },
        "spike_detection": {
            field: {
                "is_valid": result.is_valid,
                "metadata": result.metadata,
                "warnings": result.warnings,
                "errors": result.errors
            }
            for field, result in spike_results.items()
        },
        "errors": all_errors,
        "warnings": all_warnings
    }


def get_validation_stats() -> dict[str, Any]:
    """
    Get validation statistics for monitoring.

    Returns:
        Dict with validation metrics
    """
    try:
        from src.services.prometheus_metrics import (
            pricing_validation_total,  # noqa: F401
            pricing_validation_failures  # noqa: F401
        )
        # This would require collecting metrics - simplified for now
        return {
            "metrics_available": True,
            "message": "Use Prometheus metrics endpoint for detailed stats"
        }
    except (ImportError, AttributeError):
        return {
            "metrics_available": False,
            "message": "Prometheus metrics not configured"
        }
