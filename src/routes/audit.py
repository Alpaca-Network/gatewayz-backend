import logging
from typing import Optional

from dateutil.parser import isoparse
from fastapi import APIRouter
from fastapi import Depends, HTTPException

from src.db.api_keys import validate_api_key_permissions
from src.db.users import get_user
from src.db_security import get_audit_logs
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

        # Parse dates if provided. Allow both literal 'Z' and forms where '+' was converted to space.
        # Using dateutil's isoparse makes the accepted formats consistent with ISO-8601 variations.
        start_dt = None
        end_dt = None
        if start_date:
            try:
                normalized_start = start_date.replace(' ', '+', 1) if ' ' in start_date else start_date
                start_dt = isoparse(normalized_start)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid start_date format. Use ISO format.")

        if end_date:
            try:
                normalized_end = end_date.replace(' ', '+', 1) if ' ' in end_date else end_date
                end_dt = isoparse(normalized_end)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid end_date format. Use ISO format.")

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
