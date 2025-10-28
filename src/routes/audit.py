from typing import Optional
import logging
import re
from datetime import datetime

from src.db.api_keys import validate_api_key_permissions
from src.db.users import get_user
from src.db_security import get_audit_logs
from fastapi import APIRouter

from fastapi import Depends, HTTPException

from src.security.deps import get_api_key

# Initialize logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

router = APIRouter()



@router.get("/user/api-keys/audit-logs", tags=["authentication"])
async def get_user_audit_logs(
        key_id: Optional[int] = None,
        action: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100,
        api_key: str = Depends(get_api_key)
):
    """Get audit logs for the user's API keys (Phase 4 feature)"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Validate permissions
        if not validate_api_key_permissions(api_key, "read", "api_keys"):
            raise HTTPException(status_code=403, detail="Insufficient permissions to view audit logs")

        # Parse dates if provided
        start_dt = None
        end_dt = None
        if start_date:
            try:
                # Handle various ISO formats including milliseconds
                cleaned_date = start_date.replace('Z', '+00:00')
                # Remove milliseconds if present (.000, .123456, etc.)
                if '.' in cleaned_date and '+' in cleaned_date:
                    # Split on the period, take the part before, and the timezone part after
                    base_part = cleaned_date.split('.')[0]
                    tz_part = '+' + cleaned_date.split('+')[1]
                    cleaned_date = base_part + tz_part
                elif '.' in cleaned_date:
                    # Just remove everything after the period if no timezone
                    cleaned_date = cleaned_date.split('.')[0]
                start_dt = datetime.fromisoformat(cleaned_date)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid start_date format. Use ISO format. Error: {e}")

        if end_date:
            try:
                # Handle various ISO formats including milliseconds
                cleaned_date = end_date.replace('Z', '+00:00')
                # Remove milliseconds if present (.000, .123456, etc.)
                if '.' in cleaned_date and '+' in cleaned_date:
                    # Split on the period, take the part before, and the timezone part after
                    base_part = cleaned_date.split('.')[0]
                    tz_part = '+' + cleaned_date.split('+')[1]
                    cleaned_date = base_part + tz_part
                elif '.' in cleaned_date:
                    # Just remove everything after the period if no timezone
                    cleaned_date = cleaned_date.split('.')[0]
                end_dt = datetime.fromisoformat(cleaned_date)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid end_date format. Use ISO format. Error: {e}")

        # Get audit logs
        logs = get_audit_logs(
            user_id=user["id"],
            key_id=key_id,
            action=action,
            start_date=start_dt,
            end_date=end_dt,
            limit=limit
        )

        return {
            "status": "success",
            "total_logs": len(logs),
            "logs": logs,
            "phase4_integration": True,
            "security_features_enabled": True
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting audit logs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
