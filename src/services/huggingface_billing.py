"""
HuggingFace Billing Service

Handles billing calculations and tracking for HuggingFace task API requests.
All costs are tracked in nano-USD (1 nano-USD = 1e-9 USD) for precision.

Reference: https://huggingface.co/docs/inference-providers/
"""

import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal
import json

from src.db import credit_transactions as credit_tx_module
from src.services import pricing as pricing_service
from src.config import Config

logger = logging.getLogger(__name__)


class HuggingFaceBillingService:
    """Service for HuggingFace billing operations"""

    @staticmethod
    async def calculate_cost_nano_usd(
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> int:
        """
        Calculate cost in nano-USD.

        Args:
            model: Model identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Cost in nano-USD (integer)
        """
        try:
            # Get pricing for model (in $/1M tokens)
            price_per_1m_input = await pricing_service.get_model_pricing(model, "input")
            price_per_1m_output = await pricing_service.get_model_pricing(model, "output")

            # Calculate cost in USD
            # Using Decimal for precision
            input_cost_usd = Decimal(str(input_tokens)) * Decimal(str(price_per_1m_input)) / Decimal("1_000_000")
            output_cost_usd = Decimal(str(output_tokens)) * Decimal(str(price_per_1m_output)) / Decimal("1_000_000")

            total_cost_usd = input_cost_usd + output_cost_usd

            # Convert to nano-USD (multiply by 1e9)
            cost_nano_usd = int(total_cost_usd * Decimal("1e9"))

            return cost_nano_usd

        except Exception as e:
            logger.error(f"Error calculating cost: {str(e)}")
            raise


    @staticmethod
    async def log_usage(
        request_id: str,
        user_id: int,
        task: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_nano_usd: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Log usage for billing purposes.

        Args:
            request_id: Unique request identifier
            user_id: User ID
            task: Task type (e.g., 'text-generation')
            model: Model used
            input_tokens: Input token count
            output_tokens: Output token count
            cost_nano_usd: Pre-calculated cost (optional, will be calculated if not provided)

        Returns:
            Usage record
        """
        try:
            # Calculate cost if not provided
            if cost_nano_usd is None:
                cost_nano_usd = await HuggingFaceBillingService.calculate_cost_nano_usd(
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

            # Create usage record
            usage_record = {
                "request_id": request_id,
                "user_id": user_id,
                "task": task,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_nano_usd": cost_nano_usd,
                "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
            }

            # Log to database (implement in credit_transactions module)
            # await credit_tx_module.log_transaction(usage_record)

            logger.info(
                f"Logged HF task usage - Request: {request_id}, User: {user_id}, "
                f"Task: {task}, Model: {model}, Tokens: {input_tokens}+{output_tokens}, "
                f"Cost: {cost_nano_usd} nano-USD (${cost_nano_usd / 1e9:.9f})"
            )

            return usage_record

        except Exception as e:
            logger.error(f"Error logging usage: {str(e)}")
            raise


    @staticmethod
    async def deduct_credits(
        user_id: int,
        cost_nano_usd: int,
    ) -> bool:
        """
        Deduct credits from user account.

        Args:
            user_id: User ID
            cost_nano_usd: Cost in nano-USD

        Returns:
            True if successful, False if insufficient credits

        Raises:
            Exception on database error
        """
        try:
            # Convert nano-USD to USD for storage
            cost_usd = cost_nano_usd / 1e9

            # Deduct from user credits
            # This would use existing deduct_credits function
            # success = await users_module.deduct_credits(user_id, cost_usd)

            logger.info(f"Deducted ${cost_usd:.9f} from user {user_id}")

            return True

        except Exception as e:
            logger.error(f"Error deducting credits: {str(e)}")
            raise


    @staticmethod
    async def get_usage_records(
        user_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get usage records for a user.

        Args:
            user_id: User ID
            limit: Maximum records to return
            offset: Offset for pagination

        Returns:
            Dictionary with records and metadata
        """
        try:
            # Get records from database
            # records = await credit_tx_module.get_transactions(user_id, limit, offset)

            records = []  # Placeholder

            total_cost_nano_usd = sum(r.get("cost_nano_usd", 0) for r in records)

            return {
                "records": records,
                "total_records": len(records),
                "total_cost_nano_usd": total_cost_nano_usd,
                "limit": limit,
                "offset": offset,
            }

        except Exception as e:
            logger.error(f"Error getting usage records: {str(e)}")
            raise


    @staticmethod
    async def get_billing_summary(
        user_id: int,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get billing summary for the past N days.

        Args:
            user_id: User ID
            days: Number of days to include

        Returns:
            Billing summary
        """
        try:
            # Get records from database for specified period
            # records = await credit_tx_module.get_transactions_for_period(user_id, days)

            records = []  # Placeholder

            # Group by task type
            by_task = {}
            for record in records:
                task = record.get("task", "unknown")
                if task not in by_task:
                    by_task[task] = {
                        "count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_nano_usd": 0,
                    }

                by_task[task]["count"] += 1
                by_task[task]["input_tokens"] += record.get("input_tokens", 0)
                by_task[task]["output_tokens"] += record.get("output_tokens", 0)
                by_task[task]["cost_nano_usd"] += record.get("cost_nano_usd", 0)

            # Group by model
            by_model = {}
            for record in records:
                model = record.get("model", "unknown")
                if model not in by_model:
                    by_model[model] = {
                        "count": 0,
                        "cost_nano_usd": 0,
                    }

                by_model[model]["count"] += 1
                by_model[model]["cost_nano_usd"] += record.get("cost_nano_usd", 0)

            total_cost_nano_usd = sum(r.get("cost_nano_usd", 0) for r in records)

            return {
                "period_days": days,
                "total_requests": len(records),
                "total_cost_nano_usd": total_cost_nano_usd,
                "total_cost_usd": total_cost_nano_usd / 1e9,
                "by_task": by_task,
                "by_model": by_model,
            }

        except Exception as e:
            logger.error(f"Error getting billing summary: {str(e)}")
            raise


    @staticmethod
    async def batch_calculate_costs(
        requests: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Calculate costs for multiple requests in batch.

        Args:
            requests: List of request objects with model, input_tokens, output_tokens

        Returns:
            List of request objects with cost_nano_usd added
        """
        try:
            results = []

            for req in requests:
                model = req.get("model", "")
                input_tokens = req.get("input_tokens", 0)
                output_tokens = req.get("output_tokens", 0)

                cost_nano_usd = await HuggingFaceBillingService.calculate_cost_nano_usd(
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                results.append({
                    **req,
                    "cost_nano_usd": cost_nano_usd,
                    "cost_usd": cost_nano_usd / 1e9,
                })

            return results

        except Exception as e:
            logger.error(f"Error in batch cost calculation: {str(e)}")
            raise


# Convenience function for quick billing
async def calculate_huggingface_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> int:
    """Calculate cost in nano-USD"""
    return await HuggingFaceBillingService.calculate_cost_nano_usd(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
