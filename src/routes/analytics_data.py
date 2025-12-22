"""
Analytics Data API Routes
Endpoints for interacting with model_request_time_series and model_request_minute_rollup tables.
"""

import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.db.analytics_data import (
    get_minute_rollups,
    get_request_time_series,
    log_request_time_series,
    upsert_minute_rollup,
)
from src.schemas.analytics_data import (
    ModelRequestMinuteRollup,
    ModelRequestMinuteRollupCreate,
    ModelRequestTimeSeries,
    ModelRequestTimeSeriesCreate,
)
from src.security.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/analytics/data", tags=["analytics-data"])


# ============================================================================
# Time Series Endpoints
# ============================================================================

@router.post("/time-series", response_model=ModelRequestTimeSeries)
async def create_time_series_record(
    data: ModelRequestTimeSeriesCreate,
    current_user: dict | None = Depends(get_current_user),
):
    """
    Log a new raw request record to the time series table.
    """
    try:
        result = log_request_time_series(data)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create time series record")
        return result
    except Exception as e:
        logger.error(f"Error creating time series record: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/time-series", response_model=List[ModelRequestTimeSeries])
async def get_time_series_records(
    start_time: Optional[datetime] = Query(None, description="Start timestamp (ISO format)"),
    end_time: Optional[datetime] = Query(None, description="End timestamp (ISO format)"),
    provider_id: Optional[int] = Query(None, description="Filter by Provider ID"),
    model_id: Optional[int] = Query(None, description="Filter by Model ID"),
    user_id: Optional[UUID] = Query(None, description="Filter by User ID"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    current_user: dict | None = Depends(get_current_user),
):
    """
    Retrieve raw request logs with filtering.
    """
    try:
        return get_request_time_series(
            start_time=start_time,
            end_time=end_time,
            provider_id=provider_id,
            model_id=model_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"Error fetching time series records: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Rollup Endpoints
# ============================================================================

@router.post("/rollup", response_model=ModelRequestMinuteRollup)
async def create_rollup_record(
    data: ModelRequestMinuteRollupCreate,
    current_user: dict | None = Depends(get_current_user),
):
    """
    Create or update a minute rollup record.
    """
    try:
        result = upsert_minute_rollup(data)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create rollup record")
        return result
    except Exception as e:
        logger.error(f"Error creating rollup record: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/rollup", response_model=ModelRequestMinuteRollup)
async def update_rollup_record(
    data: ModelRequestMinuteRollupCreate,
    current_user: dict | None = Depends(get_current_user),
):
    """
    Update a minute rollup record (idempotent upsert).
    """
    try:
        # Since upsert handles both insert and update based on PK,
        # we can reuse the same logic.
        result = upsert_minute_rollup(data)
        if not result:
            raise HTTPException(status_code=500, detail="Failed to update rollup record")
        return result
    except Exception as e:
        logger.error(f"Error updating rollup record: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rollup", response_model=List[ModelRequestMinuteRollup])
async def get_rollup_records(
    start_time: Optional[datetime] = Query(None, description="Start bucket time"),
    end_time: Optional[datetime] = Query(None, description="End bucket time"),
    provider_id: Optional[int] = Query(None, description="Filter by Provider ID"),
    model_id: Optional[int] = Query(None, description="Filter by Model ID"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: dict | None = Depends(get_current_user),
):
    """
    Retrieve aggregated minute rollups.
    """
    try:
        return get_minute_rollups(
            start_time=start_time,
            end_time=end_time,
            provider_id=provider_id,
            model_id=model_id,
            limit=limit,
        )
    except Exception as e:
        logger.error(f"Error fetching rollup records: {e}")
        raise HTTPException(status_code=500, detail=str(e))
