#!/usr/bin/env python3
"""
Production Pricing Validation Script

This script validates pricing data in the production database to ensure:
1. All pricing is in correct per-token format
2. Google models match official pricing
3. No unexpected zero-pricing models
4. No suspiciously high or low pricing values
5. Consistency between models and model_pricing tables

Usage:
    python scripts/validate_pricing.py

Environment variables required:
    SUPABASE_URL - Production Supabase URL
    SUPABASE_KEY - Service role key
"""

import os
import sys
from decimal import Decimal
from typing import List, Dict, Any, Tuple

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from supabase import create_client


# Official Google Gemini pricing (from ai.google.dev/gemini-api/docs/pricing)
GOOGLE_OFFICIAL_PRICING = {
    "gemini-3-pro": {
        "input_per_1m": 2.00,
        "output_per_1m": 12.00,
    },
    "gemini-3-flash": {
        "input_per_1m": 0.50,
        "output_per_1m": 3.00,
    },
    "gemini-2.5-pro": {
        "input_per_1m": 1.25,
        "output_per_1m": 10.00,
    },
    "gemini-2.5-flash": {
        "input_per_1m": 0.30,
        "output_per_1m": 2.50,
    },
    "gemini-2.5-flash-lite": {
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
    },
    "gemini-2.0-flash": {
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
    },
    "gemini-2.0-flash-lite": {
        "input_per_1m": 0.075,
        "output_per_1m": 0.30,
    },
    "gemma": {  # All Gemma models are free
        "input_per_1m": 0.0,
        "output_per_1m": 0.0,
    },
    "text-embedding": {  # All embedding models
        "input_per_1m": 0.15,
        "output_per_1m": 0.0,
    },
}

# Pricing format thresholds
MAX_PER_TOKEN_PRICE = 0.001  # $1 per 1K tokens (anything higher is suspicious)
MIN_SIGNIFICANT_PRICE = 0.000000001  # Below this is essentially free


class PricingValidator:
    """Validates pricing data in production database."""

    def __init__(self, supabase_url: str, supabase_key: str):
        """Initialize validator with Supabase connection."""
        self.supabase = create_client(supabase_url, supabase_key)
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []

    def validate_all(self) -> bool:
        """
        Run all validation checks.

        Returns:
            True if all validations pass, False if any errors found
        """
        print("🔍 Starting pricing validation...\n")

        # Run all validation checks
        self.check_pricing_format()
        self.check_google_pricing()
        self.check_zero_pricing()
        self.check_suspicious_pricing()
        self.check_missing_pricing()

        # Print results
        self.print_results()

        return len(self.errors) == 0

    def check_pricing_format(self):
        """Check that all pricing is in per-token format (< $0.001)."""
        print("📏 Checking pricing format...")

        # Query for models with suspiciously high pricing
        high_pricing = self.supabase.table("model_pricing").select(
            "model_id, price_per_input_token, price_per_output_token"
        ).or_(
            f"price_per_input_token.gt.{MAX_PER_TOKEN_PRICE},"
            f"price_per_output_token.gt.{MAX_PER_TOKEN_PRICE}"
        ).execute()

        if high_pricing.data:
            for model in high_pricing.data:
                model_id = model["model_id"]
                input_price = model.get("price_per_input_token", 0)
                output_price = model.get("price_per_output_token", 0)

                if input_price and input_price > MAX_PER_TOKEN_PRICE:
                    self.errors.append(
                        f"Model {model_id} has input price ${input_price} per token "
                        f"(=${input_price * 1_000_000:.2f} per 1M) - likely wrong format!"
                    )

                if output_price and output_price > MAX_PER_TOKEN_PRICE:
                    self.errors.append(
                        f"Model {model_id} has output price ${output_price} per token "
                        f"(=${output_price * 1_000_000:.2f} per 1M) - likely wrong format!"
                    )

        if not high_pricing.data:
            self.info.append("✅ All pricing in correct per-token format")

    def check_google_pricing(self):
        """Verify Google models match official pricing."""
        print("🔍 Checking Google models against official pricing...")

        # Get all Google models from database
        google_models = self.supabase.table("models").select(
            "id, provider_slug, source_gateway"
        ).or_(
            "provider_slug.ilike.%google%,source_gateway.ilike.%google%"
        ).execute()

        for model in google_models.data:
            model_id = model["id"]

            # Get pricing from model_pricing table
            pricing = self.supabase.table("model_pricing").select(
                "price_per_input_token, price_per_output_token"
            ).eq("model_id", model_id).execute()

            if not pricing.data:
                self.warnings.append(f"Google model {model_id} missing pricing")
                continue

            db_input = Decimal(str(pricing.data[0].get("price_per_input_token", 0)))
            db_output = Decimal(str(pricing.data[0].get("price_per_output_token", 0)))

            # Check against official pricing
            for pattern, official in GOOGLE_OFFICIAL_PRICING.items():
                if pattern in model_id.lower():
                    # Convert official pricing from per-1M to per-token
                    expected_input = Decimal(str(official["input_per_1m"])) / Decimal("1000000")
                    expected_output = Decimal(str(official["output_per_1m"])) / Decimal("1000000")

                    # Allow 1% tolerance for rounding
                    tolerance = Decimal("0.01")

                    if db_input > 0 and abs(db_input - expected_input) / expected_input > tolerance:
                        self.errors.append(
                            f"Model {model_id} input pricing mismatch: "
                            f"DB=${float(db_input * 1_000_000):.4f}/1M, "
                            f"Official=${official['input_per_1m']:.4f}/1M"
                        )

                    if db_output > 0 and abs(db_output - expected_output) / expected_output > tolerance:
                        self.errors.append(
                            f"Model {model_id} output pricing mismatch: "
                            f"DB=${float(db_output * 1_000_000):.4f}/1M, "
                            f"Official=${official['output_per_1m']:.4f}/1M"
                        )

                    break

        if not any("Google model" in e for e in self.errors):
            self.info.append("✅ All Google models match official pricing")

    def check_zero_pricing(self):
        """Check for models with zero pricing that shouldn't be free."""
        print("🔍 Checking for unexpected zero pricing...")

        # Get models with zero pricing
        zero_pricing = self.supabase.table("model_pricing").select(
            "model_id"
        ).eq("price_per_input_token", 0).eq("price_per_output_token", 0).execute()

        # Get corresponding model details
        for pricing in zero_pricing.data:
            model_id = pricing["model_id"]

            # Check if model should be free
            is_free_model = (
                ":free" in model_id.lower() or
                "gemma" in model_id.lower() or
                "free" in model_id.lower()
            )

            if not is_free_model:
                self.warnings.append(
                    f"Model {model_id} has zero pricing but doesn't appear to be free"
                )

        total_zero = len(zero_pricing.data)
        self.info.append(f"📊 Found {total_zero} models with zero pricing")

    def check_suspicious_pricing(self):
        """Flag unusually high or low pricing values."""
        print("🔍 Checking for suspicious pricing values...")

        # Query all pricing
        all_pricing = self.supabase.table("model_pricing").select(
            "model_id, price_per_input_token, price_per_output_token"
        ).execute()

        for model in all_pricing.data:
            model_id = model["model_id"]
            input_price = model.get("price_per_input_token", 0)
            output_price = model.get("price_per_output_token", 0)

            # Check for unusually high pricing (>$100/1M tokens)
            if input_price > 0.0001:  # $100/1M
                self.warnings.append(
                    f"Model {model_id} has very high input price: "
                    f"${input_price * 1_000_000:.2f}/1M tokens"
                )

            if output_price > 0.0001:  # $100/1M
                self.warnings.append(
                    f"Model {model_id} has very high output price: "
                    f"${output_price * 1_000_000:.2f}/1M tokens"
                )

            # Check for output cheaper than input (unusual but not always wrong)
            if input_price > 0 and output_price > 0 and output_price < input_price:
                self.info.append(
                    f"Model {model_id} has output price < input price (unusual)"
                )

    def check_missing_pricing(self):
        """Check for models without pricing entries."""
        print("🔍 Checking for models missing pricing...")

        # Get all models
        all_models = self.supabase.table("models").select("id").execute()
        total_models = len(all_models.data)

        # Get models with pricing
        priced_models = self.supabase.table("model_pricing").select("model_id").execute()
        total_priced = len(priced_models.data)

        missing_count = total_models - total_priced

        if missing_count > 0:
            self.warnings.append(
                f"{missing_count} models ({missing_count/total_models*100:.1f}%) "
                f"missing pricing entries"
            )
        else:
            self.info.append("✅ All models have pricing entries")

    def print_results(self):
        """Print validation results."""
        print("\n" + "="*70)
        print("📊 VALIDATION RESULTS")
        print("="*70)

        if self.errors:
            print(f"\n❌ ERRORS ({len(self.errors)}):")
            for error in self.errors:
                print(f"   • {error}")

        if self.warnings:
            print(f"\n⚠️  WARNINGS ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"   • {warning}")

        if self.info:
            print(f"\n✅ INFO ({len(self.info)}):")
            for info in self.info:
                print(f"   • {info}")

        print("\n" + "="*70)

        if self.errors:
            print("❌ VALIDATION FAILED - Please fix errors above")
            return False
        elif self.warnings:
            print("⚠️  VALIDATION PASSED WITH WARNINGS")
            return True
        else:
            print("✅ VALIDATION PASSED - All checks successful!")
            return True


def main():
    """Main entry point."""
    # Get credentials from environment
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        print("❌ Error: SUPABASE_URL and SUPABASE_KEY environment variables required")
        print("\nUsage:")
        print("  export SUPABASE_URL='https://your-project.supabase.co'")
        print("  export SUPABASE_KEY='your-service-role-key'")
        print("  python scripts/validate_pricing.py")
        sys.exit(1)

    # Run validation
    validator = PricingValidator(supabase_url, supabase_key)
    success = validator.validate_all()

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
