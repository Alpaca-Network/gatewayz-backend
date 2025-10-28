import datetime
from typing import Optional
import logging

from src.db.api_keys import validate_api_key_permissions
from src.db.users import get_user
from src.db_security import get_audit_logs
from fastapi import APIRouter
from datetime import datetime

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
                # Handle 'Z' suffix and ensure '+' is preserved (URL decoding turns '+' into space)
                date_str = start_date.strip()
                if date_str.endswith('Z'):
                    date_str = date_str.replace('Z', '+00:00')
                # Fix URL decoded spaces back to '+' for timezone offset
                if ' ' in date_str and date_str.count(':') >= 2:
                    # Check if this looks like a timezone was affected by URL decoding
                    # e.g., "2024-01-15T10:00:00 00:00" should be "2024-01-15T10:00:00+00:00"
                    parts = date_str.rsplit(' ', 1)
                    if len(parts) == 2 and ':' in parts[1]:
                        date_str = parts[0] + '+' + parts[1]
                start_dt = datetime.fromisoformat(date_str)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid start_date format. Use ISO format.")

        if end_date:
            try:
                # Handle 'Z' suffix and ensure '+' is preserved (URL decoding turns '+' into space)
                date_str = end_date.strip()
                if date_str.endswith('Z'):
                    date_str = date_str.replace('Z', '+00:00')
                # Fix URL decoded spaces back to '+' for timezone offset
                if ' ' in date_str and date_str.count(':') >= 2:
                    # Check if this looks like a timezone was affected by URL decoding
                    # e.g., "2024-01-15T10:00:00 00:00" should be "2024-01-15T10:00:00+00:00"
                    parts = date_str.rsplit(' ', 1)
                    if len(parts) == 2 and ':' in parts[1]:
                        date_str = parts[0] + '+' + parts[1]
                end_dt = datetime.fromisoformat(date_str)
            except ValueError:
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
