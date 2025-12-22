"""
Analytics Data Database Layer
Handles interactions with model_request_time_series and model_request_minute_rollup tables.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from src.config.supabase_config import get_supabase_client
from src.schemas.analytics_data import (
    ModelRequestMinuteRollup,
    ModelRequestMinuteRollupCreate,
    ModelRequestTimeSeries,
    ModelRequestTimeSeriesCreate,
)

logger = logging.getLogger(__name__)


def log_request_time_series(data: ModelRequestTimeSeriesCreate) -> Optional[ModelRequestTimeSeries]:
    """
    Insert a new record into model_request_time_series table.
    """
    try:
        supabase = get_supabase_client()
        
        # Convert Pydantic model to dict, handling UUID and datetime serialization
        payload = data.model_dump(mode="json")
        
        response = supabase.table("model_request_time_series").insert(payload).execute()
        
        if response.data:
            return ModelRequestTimeSeries(**response.data[0])
        return None
        
    except Exception as e:
        logger.error(f"Error logging request time series: {e}")
        return None


def get_request_time_series(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    provider_id: Optional[int] = None,
    model_id: Optional[int] = None,
    user_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0
) -> List[ModelRequestTimeSeries]:
    """
    Fetch raw request logs with filtering options.
    """
    try:
        supabase = get_supabase_client()
        query = supabase.table("model_request_time_series").select("*")
        
        if start_time:
            query = query.gte("timestamp", start_time.isoformat())
        if end_time:
            query = query.lte("timestamp", end_time.isoformat())
        if provider_id:
            query = query.eq("provider_id", provider_id)
        if model_id:
            query = query.eq("model_id", model_id)
        if user_id:
            query = query.eq("user_id", str(user_id))
            
        # Add sorting by timestamp desc
        query = query.order("timestamp", desc=True)
        query = query.range(offset, offset + limit - 1)
        
        response = query.execute()
        
        if response.data:
            return [ModelRequestTimeSeries(**item) for item in response.data]
        return []
        
    except Exception as e:
        logger.error(f"Error fetching request time series: {e}")
        return []


def upsert_minute_rollup(data: ModelRequestMinuteRollupCreate) -> Optional[ModelRequestMinuteRollup]:
    """
    Insert or update a record in model_request_minute_rollup table.
    Uses Supabase upsert functionality based on primary key/unique constraint (bucket, model_id, provider_id).
    """
    try:
        supabase = get_supabase_client()
        
        payload = data.model_dump(mode="json")
        
        # upsert=True is default for .upsert(), but being explicit
        response = supabase.table("model_request_minute_rollup").upsert(payload).execute()
        
        if response.data:
            return ModelRequestMinuteRollup(**response.data[0])
        return None
        
    except Exception as e:
        logger.error(f"Error upserting minute rollup: {e}")
        return None


def get_minute_rollups(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    provider_id: Optional[int] = None,
    model_id: Optional[int] = None,
    limit: int = 100
) -> List[ModelRequestMinuteRollup]:
    """
    Fetch aggregated minute rollups with filtering.
    """
    try:
        supabase = get_supabase_client()
        query = supabase.table("model_request_minute_rollup").select("*")
        
        if start_time:
            query = query.gte("bucket", start_time.isoformat())
        if end_time:
            query = query.lte("bucket", end_time.isoformat())
        if provider_id:
            query = query.eq("provider_id", provider_id)
        if model_id:
            query = query.eq("model_id", model_id)
            
        query = query.order("bucket", desc=True)
        if limit:
            query = query.limit(limit)
            
        response = query.execute()
        
        if response.data:
            return [ModelRequestMinuteRollup(**item) for item in response.data]
        return []
        
    except Exception as e:
        logger.error(f"Error fetching minute rollups: {e}")
        return []
