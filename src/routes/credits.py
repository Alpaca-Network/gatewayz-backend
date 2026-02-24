#!/usr/bin/env python3
"""
Credits Management Routes
Provides endpoints for credit operations including add, adjust, bulk-add, refund, summary, and transactions.
These endpoints match the admin dashboard API expectations.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.config.config import Config
from src.config.supabase_config import get_supabase_client
from src.db.credit_transactions import (
    TransactionType,
    get_admin_daily_grant_total,
    get_all_transactions,
    get_transaction_summary,
    log_credit_transaction,
)

# Note: Database operations are performed directly via supabase client
# to maintain consistency within transaction logging
from src.security.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# REQUEST/RESPONSE SCHEMAS
# =============================================================================


class CreditAddRequest(BaseModel):
    """Request to add credits to a user"""

    user_id: int = Field(..., description="User ID to add credits to")
    amount: float = Field(..., gt=0, description="Amount of credits to add (must be positive)")
    reason: str = Field(
        ...,
        min_length=10,
        description="Required reason for the credit grant (min 10 characters)",
    )
    description: str = Field(
        default="Admin credit addition", description="Description for the transaction"
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata")


class CreditAdjustRequest(BaseModel):
    """Request to adjust credits (add or remove)"""

    user_id: int = Field(..., description="User ID to adjust credits for")
    amount: float = Field(..., description="Amount to adjust (positive to add, negative to remove)")
    description: str = Field(
        default="Admin credit adjustment", description="Description for the transaction"
    )
    reason: str = Field(
        ...,
        min_length=10,
        description="Required reason for the adjustment (min 10 characters)",
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata")


class BulkCreditAddRequest(BaseModel):
    """Request to add credits to multiple users"""

    user_ids: list[int] = Field(
        ..., min_length=1, max_length=100, description="List of user IDs (max 100)"
    )
    amount: float = Field(..., gt=0, description="Amount of credits to add to each user")
    reason: str = Field(
        ...,
        min_length=10,
        description="Required reason for the bulk credit grant (min 10 characters)",
    )
    description: str = Field(
        default="Bulk credit addition", description="Description for the transactions"
    )
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata")


class CreditRefundRequest(BaseModel):
    """Request to refund credits to a user"""

    user_id: int = Field(..., description="User ID to refund credits to")
    amount: float = Field(..., gt=0, description="Amount of credits to refund")
    original_transaction_id: int | None = Field(
        default=None, description="Original transaction ID being refunded"
    )
    reason: str = Field(default="Refund", description="Reason for the refund")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata")


class CreditResponse(BaseModel):
    """Standard credit operation response"""

    status: str
    message: str
    user_id: int
    previous_balance: float
    new_balance: float
    amount_changed: float
    transaction_id: int | None = None
    timestamp: str


class BulkCreditResponse(BaseModel):
    """Response for bulk credit operations"""

    status: str
    message: str
    total_users: int
    successful: int
    failed: int
    amount_per_user: float
    total_credits_added: float
    results: list[dict[str, Any]]
    timestamp: str


# =============================================================================
# ADMIN GRANT SAFETY CONTROLS
# =============================================================================


def _validate_admin_credit_grant(
    amount: float,
    admin_user: dict,
    *,
    is_bulk: bool = False,
    bulk_user_count: int = 1,
) -> None:
    """
    Validate admin credit grant against safety controls:
    1. Per-transaction cap (ADMIN_MAX_CREDIT_GRANT)
    2. 24-hour rolling window limit per admin (ADMIN_DAILY_GRANT_LIMIT)

    For bulk operations, the total grant (amount * user_count) is checked
    against the daily limit.

    Raises:
        HTTPException(400) if any limit is exceeded.
    """
    max_single_grant = Config.ADMIN_MAX_CREDIT_GRANT
    daily_limit = Config.ADMIN_DAILY_GRANT_LIMIT
    admin_id = admin_user.get("id")

    # 1. Per-transaction cap
    if amount > max_single_grant:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Credit grant amount ${amount:.2f} exceeds the maximum single grant "
                f"limit of ${max_single_grant:.2f}. Contact a super-admin to increase "
                f"the ADMIN_MAX_CREDIT_GRANT limit."
            ),
        )

    # 2. Daily rolling window limit
    total_grant_amount = amount * bulk_user_count if is_bulk else amount
    daily_total = get_admin_daily_grant_total(admin_id)
    remaining = daily_limit - daily_total

    if daily_total + total_grant_amount > daily_limit:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This grant of ${total_grant_amount:.2f} would exceed your 24-hour "
                f"admin grant limit of ${daily_limit:.2f}. You have already granted "
                f"${daily_total:.2f} in the last 24 hours (${remaining:.2f} remaining). "
                f"Contact a super-admin to increase the ADMIN_DAILY_GRANT_LIMIT."
            ),
        )


# =============================================================================
# CREDIT ENDPOINTS
# =============================================================================


@router.post("/credits/add", tags=["credits", "admin"])
async def add_credits_endpoint(
    request: CreditAddRequest,
    admin_user: dict = Depends(require_admin),
) -> CreditResponse:
    """
    Add credits to a user account.

    This endpoint adds a positive amount of credits to a user's account.
    Only accessible by admin users.

    **Request:**
    - `user_id`: Target user ID
    - `amount`: Amount of credits to add (must be positive)
    - `description`: Optional description for the transaction
    - `metadata`: Optional additional metadata

    **Response:**
    - User's previous and new balance
    - Transaction details
    """
    try:
        # Enforce admin credit grant safety controls
        _validate_admin_credit_grant(request.amount, admin_user)

        client = get_supabase_client()

        # Get user
        user_result = (
            client.table("users").select("id, credits").eq("id", request.user_id).execute()
        )

        if not user_result.data or len(user_result.data) == 0:
            raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")

        user = user_result.data[0]
        balance_before = float(user.get("credits", 0) or 0)
        balance_after = balance_before + request.amount

        # Update user's credits
        update_result = (
            client.table("users")
            .update({"credits": balance_after, "updated_at": datetime.now(UTC).isoformat()})
            .eq("id", request.user_id)
            .execute()
        )

        if not update_result.data:
            raise HTTPException(status_code=500, detail="Failed to update user credits")

        # Log the transaction with reason in metadata for full audit trail
        transaction = log_credit_transaction(
            user_id=request.user_id,
            amount=request.amount,
            transaction_type=TransactionType.ADMIN_CREDIT,
            description=request.description,
            balance_before=balance_before,
            balance_after=balance_after,
            metadata={
                **(request.metadata or {}),
                "reason": request.reason,
                "admin_user_id": admin_user.get("id"),
                "admin_username": admin_user.get("username"),
            },
            created_by=f"admin:{admin_user.get('id')}",
        )

        logger.info(
            f"Admin {admin_user.get('username')} added {request.amount} credits to user "
            f"{request.user_id}. Reason: {request.reason}"
        )

        return CreditResponse(
            status="success",
            message=f"Added {request.amount} credits to user {request.user_id}",
            user_id=request.user_id,
            previous_balance=balance_before,
            new_balance=balance_after,
            amount_changed=request.amount,
            transaction_id=transaction.get("id") if transaction else None,
            timestamp=datetime.now(UTC).isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding credits: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add credits") from e


@router.post("/credits/adjust", tags=["credits", "admin"])
async def adjust_credits_endpoint(
    request: CreditAdjustRequest,
    admin_user: dict = Depends(require_admin),
) -> CreditResponse:
    """
    Adjust credits for a user account (add or remove).

    This endpoint allows adding or removing credits from a user's account.
    Use positive amounts to add credits, negative amounts to remove.
    Only accessible by admin users.

    **Request:**
    - `user_id`: Target user ID
    - `amount`: Amount to adjust (positive to add, negative to remove)
    - `description`: Optional description for the transaction
    - `reason`: Required reason for the adjustment (min 10 characters)
    - `metadata`: Optional additional metadata

    **Response:**
    - User's previous and new balance
    - Transaction details
    """
    try:
        # Enforce admin credit grant safety controls for positive adjustments (grants)
        if request.amount > 0:
            _validate_admin_credit_grant(request.amount, admin_user)

        client = get_supabase_client()

        # Get user
        user_result = (
            client.table("users").select("id, credits").eq("id", request.user_id).execute()
        )

        if not user_result.data or len(user_result.data) == 0:
            raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")

        user = user_result.data[0]
        balance_before = float(user.get("credits", 0) or 0)
        balance_after = balance_before + request.amount

        # Prevent negative balance
        if balance_after < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Adjustment would result in negative balance. Current: {balance_before}, Adjustment: {request.amount}",
            )

        # Update user's credits
        update_result = (
            client.table("users")
            .update({"credits": balance_after, "updated_at": datetime.now(UTC).isoformat()})
            .eq("id", request.user_id)
            .execute()
        )

        if not update_result.data:
            raise HTTPException(status_code=500, detail="Failed to update user credits")

        # Determine transaction type
        transaction_type = (
            TransactionType.ADMIN_CREDIT if request.amount > 0 else TransactionType.ADMIN_DEBIT
        )

        # Log the transaction
        transaction = log_credit_transaction(
            user_id=request.user_id,
            amount=request.amount,
            transaction_type=transaction_type,
            description=request.description,
            balance_before=balance_before,
            balance_after=balance_after,
            metadata={
                **(request.metadata or {}),
                "reason": request.reason,
                "admin_user_id": admin_user.get("id"),
                "admin_username": admin_user.get("username"),
            },
            created_by=f"admin:{admin_user.get('id')}",
        )

        action = "added" if request.amount > 0 else "removed"
        logger.info(
            f"Admin {admin_user.get('username')} {action} {abs(request.amount)} credits for user "
            f"{request.user_id}. Reason: {request.reason}"
        )

        return CreditResponse(
            status="success",
            message=f"Adjusted credits for user {request.user_id} by {request.amount}",
            user_id=request.user_id,
            previous_balance=balance_before,
            new_balance=balance_after,
            amount_changed=request.amount,
            transaction_id=transaction.get("id") if transaction else None,
            timestamp=datetime.now(UTC).isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adjusting credits: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to adjust credits") from e


@router.post("/credits/bulk-add", tags=["credits", "admin"])
async def bulk_add_credits_endpoint(
    request: BulkCreditAddRequest,
    admin_user: dict = Depends(require_admin),
) -> BulkCreditResponse:
    """
    Add credits to multiple users at once.

    This endpoint adds a specified amount of credits to multiple users.
    Only accessible by admin users.

    **Request:**
    - `user_ids`: List of user IDs to add credits to (max 100)
    - `amount`: Amount of credits to add to each user
    - `reason`: Required reason for the bulk credit grant (min 10 characters)
    - `description`: Optional description for the transactions
    - `metadata`: Optional additional metadata

    **Response:**
    - Summary of successful and failed operations
    - Details for each user
    """
    try:
        # Deduplicate user IDs early so we know the real count for limit checks
        unique_user_ids = list(dict.fromkeys(request.user_ids))

        # Enforce admin credit grant safety controls (per-amount cap + daily total)
        _validate_admin_credit_grant(
            request.amount,
            admin_user,
            is_bulk=True,
            bulk_user_count=len(unique_user_ids),
        )

        client = get_supabase_client()
        results = []
        successful = 0
        failed = 0

        # Batch fetch: Get all users at once to reduce N+1 queries
        users_result = (
            client.table("users")
            .select("id, credits, username")
            .in_("id", unique_user_ids)
            .execute()
        )
        users_by_id = {u["id"]: u for u in (users_result.data or [])}

        # Process each unique user
        for user_id in unique_user_ids:
            try:
                user = users_by_id.get(user_id)

                if not user:
                    results.append(
                        {
                            "user_id": user_id,
                            "status": "failed",
                            "error": "User not found",
                        }
                    )
                    failed += 1
                    continue

                balance_before = float(user.get("credits", 0) or 0)
                balance_after = balance_before + request.amount

                # Update user's credits
                update_result = (
                    client.table("users")
                    .update({"credits": balance_after, "updated_at": datetime.now(UTC).isoformat()})
                    .eq("id", user_id)
                    .execute()
                )

                if not update_result.data:
                    results.append(
                        {
                            "user_id": user_id,
                            "status": "failed",
                            "error": "Failed to update credits",
                        }
                    )
                    failed += 1
                    continue

                # Log the transaction with reason for audit trail
                transaction = log_credit_transaction(
                    user_id=user_id,
                    amount=request.amount,
                    transaction_type=TransactionType.ADMIN_CREDIT,
                    description=request.description,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    metadata={
                        **(request.metadata or {}),
                        "reason": request.reason,
                        "bulk_operation": True,
                        "admin_user_id": admin_user.get("id"),
                        "admin_username": admin_user.get("username"),
                    },
                    created_by=f"admin:{admin_user.get('id')}",
                )

                results.append(
                    {
                        "user_id": user_id,
                        "username": user.get("username"),
                        "status": "success",
                        "previous_balance": balance_before,
                        "new_balance": balance_after,
                        "transaction_id": transaction.get("id") if transaction else None,
                    }
                )
                successful += 1

            except Exception as e:
                logger.error(f"Error adding credits to user {user_id}: {e}")
                results.append(
                    {
                        "user_id": user_id,
                        "status": "failed",
                        "error": str(e),
                    }
                )
                failed += 1

        logger.info(
            f"Admin {admin_user.get('username')} bulk added {request.amount} credits to "
            f"{successful}/{len(unique_user_ids)} users. Reason: {request.reason}"
        )

        return BulkCreditResponse(
            status="success" if failed == 0 else "partial" if successful > 0 else "failed",
            message=f"Bulk credit addition completed: {successful} successful, {failed} failed",
            total_users=len(unique_user_ids),
            successful=successful,
            failed=failed,
            amount_per_user=request.amount,
            total_credits_added=request.amount * successful,
            results=results,
            timestamp=datetime.now(UTC).isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk credit addition: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to perform bulk credit addition") from e


@router.post("/credits/refund", tags=["credits", "admin"])
async def refund_credits_endpoint(
    request: CreditRefundRequest,
    admin_user: dict = Depends(require_admin),
) -> CreditResponse:
    """
    Refund credits to a user account.

    This endpoint refunds credits to a user's account, typically used for
    reversing charges or compensating users.
    Only accessible by admin users.

    **Request:**
    - `user_id`: Target user ID
    - `amount`: Amount of credits to refund (must be positive)
    - `original_transaction_id`: Optional ID of original transaction being refunded
    - `reason`: Reason for the refund
    - `metadata`: Optional additional metadata

    **Response:**
    - User's previous and new balance
    - Transaction details
    """
    try:
        client = get_supabase_client()

        # Get user
        user_result = (
            client.table("users").select("id, credits").eq("id", request.user_id).execute()
        )

        if not user_result.data or len(user_result.data) == 0:
            raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")

        user = user_result.data[0]
        balance_before = float(user.get("credits", 0) or 0)
        balance_after = balance_before + request.amount

        # Update user's credits
        update_result = (
            client.table("users")
            .update({"credits": balance_after, "updated_at": datetime.now(UTC).isoformat()})
            .eq("id", request.user_id)
            .execute()
        )

        if not update_result.data:
            raise HTTPException(status_code=500, detail="Failed to update user credits")

        # Log the transaction
        transaction = log_credit_transaction(
            user_id=request.user_id,
            amount=request.amount,
            transaction_type=TransactionType.REFUND,
            description=f"Refund: {request.reason}",
            balance_before=balance_before,
            balance_after=balance_after,
            metadata={
                **(request.metadata or {}),
                "reason": request.reason,
                "original_transaction_id": request.original_transaction_id,
                "admin_user_id": admin_user.get("id"),
                "admin_username": admin_user.get("username"),
            },
            created_by=f"admin:{admin_user.get('id')}",
        )

        logger.info(
            f"Admin {admin_user.get('username')} refunded {request.amount} credits to user {request.user_id}. Reason: {request.reason}"
        )

        return CreditResponse(
            status="success",
            message=f"Refunded {request.amount} credits to user {request.user_id}",
            user_id=request.user_id,
            previous_balance=balance_before,
            new_balance=balance_after,
            amount_changed=request.amount,
            transaction_id=transaction.get("id") if transaction else None,
            timestamp=datetime.now(UTC).isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refunding credits: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to refund credits") from e


@router.get("/credits/summary", tags=["credits", "admin"])
async def get_credits_summary_endpoint(
    user_id: int | None = Query(None, description="Filter by user ID (optional)"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Get credit summary and analytics.

    This endpoint provides a comprehensive summary of credit transactions,
    including totals, breakdowns by type, and daily statistics.

    **Query Parameters:**
    - `user_id`: Optional filter by specific user
    - `from_date`: Start date filter (YYYY-MM-DD)
    - `to_date`: End date filter (YYYY-MM-DD)

    **Response:**
    - Total credits added and used
    - Breakdown by transaction type
    - Daily breakdown
    - System-wide statistics (if no user_id specified)
    """
    try:
        client = get_supabase_client()

        if user_id:
            # Get summary for specific user
            summary = get_transaction_summary(user_id, from_date, to_date)

            # Get user info
            user_result = (
                client.table("users").select("id, username, credits").eq("id", user_id).execute()
            )
            user_info = user_result.data[0] if user_result.data else None

            return {
                "status": "success",
                "user_id": user_id,
                "user_info": user_info,
                "current_balance": float(user_info.get("credits", 0) or 0) if user_info else 0,
                "summary": summary,
                "filters": {
                    "from_date": from_date,
                    "to_date": to_date,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }
        else:
            # Get system-wide summary
            # Get all users with their balances
            users_result = client.table("users").select("id, credits").execute()
            users = users_result.data or []

            total_users = len(users)
            total_credits = sum(float(u.get("credits", 0) or 0) for u in users)
            avg_credits = total_credits / total_users if total_users > 0 else 0

            # Get transaction counts
            query = client.table("credit_transactions").select("transaction_type, amount")

            if from_date:
                if "T" not in from_date:
                    from_date = f"{from_date}T00:00:00Z"
                query = query.gte("created_at", from_date)

            if to_date:
                if "T" not in to_date:
                    to_date = f"{to_date}T23:59:59Z"
                query = query.lte("created_at", to_date)

            transactions_result = query.execute()
            transactions = transactions_result.data or []

            # Calculate totals
            total_credits_added = sum(
                float(t["amount"]) for t in transactions if float(t["amount"]) > 0
            )
            total_credits_used = sum(
                abs(float(t["amount"])) for t in transactions if float(t["amount"]) < 0
            )

            # Breakdown by type
            by_type = {}
            for txn in transactions:
                t_type = txn.get("transaction_type", "unknown")
                if t_type not in by_type:
                    by_type[t_type] = {"count": 0, "total_amount": 0.0}
                by_type[t_type]["count"] += 1
                by_type[t_type]["total_amount"] += float(txn["amount"])

            return {
                "status": "success",
                "system_summary": {
                    "total_users": total_users,
                    "total_credits_in_system": round(total_credits, 2),
                    "average_credits_per_user": round(avg_credits, 2),
                    "total_transactions": len(transactions),
                    "total_credits_added": round(total_credits_added, 2),
                    "total_credits_used": round(total_credits_used, 2),
                    "net_change": round(total_credits_added - total_credits_used, 2),
                    "by_type": by_type,
                },
                "filters": {
                    "from_date": from_date,
                    "to_date": to_date,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }

    except Exception as e:
        logger.error(f"Error getting credits summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get credits summary") from e


@router.get("/credits/transactions", tags=["credits", "admin"])
async def get_credits_transactions_endpoint(
    limit: int = Query(50, ge=1, le=1000, description="Maximum number of transactions to return"),
    offset: int = Query(0, ge=0, description="Number of transactions to skip"),
    user_id: int | None = Query(None, description="Filter by specific user ID"),
    transaction_type: str | None = Query(
        None,
        description="Filter by transaction type (trial, purchase, api_usage, admin_credit, admin_debit, refund, bonus, transfer)",
    ),
    from_date: str | None = Query(None, description="Start date filter (YYYY-MM-DD or ISO format)"),
    to_date: str | None = Query(None, description="End date filter (YYYY-MM-DD or ISO format)"),
    min_amount: float | None = Query(
        None, description="Minimum transaction amount (absolute value)"
    ),
    max_amount: float | None = Query(
        None, description="Maximum transaction amount (absolute value)"
    ),
    direction: str | None = Query(
        None,
        description="Filter by direction: 'credit' (positive amounts) or 'charge' (negative amounts)",
    ),
    sort_by: str = Query(
        "created_at", description="Sort field: 'created_at', 'amount', or 'transaction_type'"
    ),
    sort_order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Get credit transactions with advanced filtering.

    This endpoint provides access to all credit transactions with comprehensive
    filtering and pagination options.

    **Query Parameters:**
    - `limit`: Maximum transactions to return (1-1000)
    - `offset`: Number to skip for pagination
    - `user_id`: Filter by user
    - `transaction_type`: Filter by type
    - `from_date` / `to_date`: Date range
    - `min_amount` / `max_amount`: Amount range
    - `direction`: 'credit' or 'charge'
    - `sort_by`: Sort field
    - `sort_order`: 'asc' or 'desc'

    **Response:**
    - List of transactions
    - Pagination info
    - Applied filters
    """
    try:
        # Validate direction filter
        if direction and direction.lower() not in ("credit", "charge"):
            raise HTTPException(status_code=400, detail="direction must be 'credit' or 'charge'")

        # Validate sort_by
        if sort_by not in ("created_at", "amount", "transaction_type"):
            raise HTTPException(
                status_code=400,
                detail="sort_by must be 'created_at', 'amount', or 'transaction_type'",
            )

        # Validate sort_order
        if sort_order.lower() not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_order must be 'asc' or 'desc'")

        # Get transactions - fetch one extra to determine has_more
        transactions = get_all_transactions(
            limit=limit + 1,
            offset=offset,
            user_id=user_id,
            transaction_type=transaction_type,
            from_date=from_date,
            to_date=to_date,
            min_amount=min_amount,
            max_amount=max_amount,
            direction=direction,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Determine if there are more records
        has_more = len(transactions) > limit
        # Trim to requested limit
        transactions = transactions[:limit]

        # Format transactions
        formatted_transactions = [
            {
                "id": txn["id"],
                "user_id": txn["user_id"],
                "amount": float(txn["amount"]),
                "transaction_type": txn["transaction_type"],
                "description": txn.get("description", ""),
                "balance_before": float(txn["balance_before"]),
                "balance_after": float(txn["balance_after"]),
                "created_at": txn["created_at"],
                "payment_id": txn.get("payment_id"),
                "metadata": txn.get("metadata", {}),
                "created_by": txn.get("created_by"),
            }
            for txn in transactions
        ]

        return {
            "status": "success",
            "transactions": formatted_transactions,
            "pagination": {
                "total": len(formatted_transactions),
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
            },
            "filters_applied": {
                "user_id": user_id,
                "transaction_type": transaction_type,
                "from_date": from_date,
                "to_date": to_date,
                "min_amount": min_amount,
                "max_amount": max_amount,
                "direction": direction,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting credit transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get credit transactions") from e
