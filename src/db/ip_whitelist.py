"""
Database operations for IP whitelist management

This module handles CRUD operations for the ip_whitelist table,
which allows administrators to whitelist specific IPs or CIDR ranges
to bypass rate limiting (even during velocity mode).
"""

import ipaddress
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def create_whitelist_entry(
    ip_address: str,
    reason: str,
    created_by: str | UUID,
    user_id: str | UUID | None = None,
    expires_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Create a new IP whitelist entry.

    Args:
        ip_address: IP address or CIDR range (e.g., "203.0.113.5" or "203.0.113.0/24")
        reason: Reason for whitelisting
        created_by: UUID of admin creating this entry
        user_id: Optional user ID to associate with (None = global whitelist)
        expires_at: Optional expiration datetime (None = never expires)
        metadata: Additional context data

    Returns:
        Created whitelist entry or None if failed
    """
    try:
        # Validate IP address or CIDR range
        try:
            ipaddress.ip_network(ip_address, strict=False)
        except ValueError as e:
            logger.error(f"Invalid IP address or CIDR range '{ip_address}': {e}")
            return None

        supabase = get_supabase_client()

        # Convert UUIDs to strings if necessary
        created_by_str = str(created_by) if isinstance(created_by, UUID) else created_by
        user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id if user_id else None

        entry_data = {
            "ip_address": ip_address,
            "reason": reason,
            "created_by": created_by_str,
            "user_id": user_id_str,
            "metadata": metadata or {},
        }

        if expires_at:
            entry_data["expires_at"] = expires_at.isoformat()

        result = supabase.table("ip_whitelist").insert(entry_data).execute()

        if result.data:
            logger.info(
                f"Created IP whitelist entry: {ip_address} "
                f"(reason: {reason}, user_id: {user_id_str or 'global'})"
            )
            return result.data[0]
        else:
            logger.error("Failed to create IP whitelist entry: No data returned")
            return None

    except Exception as e:
        logger.error(f"Error creating IP whitelist entry: {e}")
        return None


def delete_whitelist_entry(entry_id: str | UUID) -> bool:
    """
    Delete an IP whitelist entry.

    Args:
        entry_id: UUID of the whitelist entry

    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        supabase = get_supabase_client()

        entry_id_str = str(entry_id) if isinstance(entry_id, UUID) else entry_id

        result = supabase.table("ip_whitelist").delete().eq("id", entry_id_str).execute()

        if result.data:
            logger.info(f"Deleted IP whitelist entry: {entry_id_str}")
            return True
        else:
            logger.warning(f"IP whitelist entry not found: {entry_id_str}")
            return False

    except Exception as e:
        logger.error(f"Error deleting IP whitelist entry {entry_id}: {e}")
        return False


def update_whitelist_entry(
    entry_id: str | UUID,
    enabled: bool | None = None,
    reason: str | None = None,
    expires_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Update an IP whitelist entry.

    Args:
        entry_id: UUID of the whitelist entry
        enabled: Enable/disable the entry
        reason: Update the reason
        expires_at: Update the expiration date
        metadata: Update metadata

    Returns:
        Updated entry or None if failed
    """
    try:
        supabase = get_supabase_client()

        entry_id_str = str(entry_id) if isinstance(entry_id, UUID) else entry_id

        update_data = {}
        if enabled is not None:
            update_data["enabled"] = enabled
        if reason is not None:
            update_data["reason"] = reason
        if expires_at is not None:
            update_data["expires_at"] = expires_at.isoformat()
        if metadata is not None:
            update_data["metadata"] = metadata

        if not update_data:
            logger.warning("No fields to update for IP whitelist entry")
            return None

        result = supabase.table("ip_whitelist").update(update_data).eq("id", entry_id_str).execute()

        if result.data:
            logger.info(f"Updated IP whitelist entry: {entry_id_str}")
            return result.data[0]
        else:
            logger.error(f"Failed to update IP whitelist entry: {entry_id_str}")
            return None

    except Exception as e:
        logger.error(f"Error updating IP whitelist entry {entry_id}: {e}")
        return None


def is_ip_whitelisted(ip_address: str, user_id: str | UUID | None = None) -> bool:
    """
    Check if an IP address is whitelisted.

    This function checks both exact IP matches and CIDR range matches.

    Args:
        ip_address: IP address to check
        user_id: Optional user ID to check user-specific whitelists

    Returns:
        True if IP is whitelisted, False otherwise
    """
    try:
        supabase = get_supabase_client()

        # Convert IP to ipaddress object for comparison
        try:
            ip_obj = ipaddress.ip_address(ip_address)
        except ValueError as e:
            logger.error(f"Invalid IP address '{ip_address}': {e}")
            return False

        # Get all active whitelist entries (both global and user-specific)
        now = datetime.now(timezone.utc).isoformat()

        query = (
            supabase.table("ip_whitelist")
            .select("*")
            .eq("enabled", True)
            .or_(f"expires_at.is.null,expires_at.gte.{now}")
        )

        # Check both global whitelists (user_id = null) and user-specific whitelists
        if user_id:
            user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id
            query = query.or_(f"user_id.is.null,user_id.eq.{user_id_str}")
        else:
            query = query.is_("user_id", "null")

        result = query.execute()

        if not result.data:
            return False

        # Check if IP matches any whitelist entry (exact match or CIDR range)
        for entry in result.data:
            try:
                entry_network = ipaddress.ip_network(entry["ip_address"], strict=False)
                if ip_obj in entry_network:
                    logger.info(
                        f"IP {ip_address} matches whitelist entry: "
                        f"{entry['ip_address']} (reason: {entry['reason']})"
                    )
                    return True
            except ValueError:
                logger.warning(f"Invalid IP network in whitelist: {entry['ip_address']}")
                continue

        return False

    except Exception as e:
        logger.error(f"Error checking IP whitelist for {ip_address}: {e}")
        return False


def get_whitelist_entries(
    user_id: str | UUID | None = None,
    enabled_only: bool = True,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """
    Get IP whitelist entries.

    Args:
        user_id: Filter by user ID (None = global whitelists only)
        enabled_only: Only return enabled entries
        include_expired: Include expired entries

    Returns:
        List of whitelist entries
    """
    try:
        supabase = get_supabase_client()

        query = supabase.table("ip_whitelist").select("*")

        if enabled_only:
            query = query.eq("enabled", True)

        if not include_expired:
            now = datetime.now(timezone.utc).isoformat()
            query = query.or_(f"expires_at.is.null,expires_at.gte.{now}")

        if user_id:
            user_id_str = str(user_id) if isinstance(user_id, UUID) else user_id
            query = query.eq("user_id", user_id_str)
        else:
            # Only global whitelists
            query = query.is_("user_id", "null")

        result = query.order("created_at", desc=True).execute()

        return result.data if result.data else []

    except Exception as e:
        logger.error(f"Error getting IP whitelist entries: {e}")
        return []


def get_all_whitelist_entries(
    enabled_only: bool = True,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """
    Get all IP whitelist entries (both global and user-specific).

    Args:
        enabled_only: Only return enabled entries
        include_expired: Include expired entries

    Returns:
        List of whitelist entries
    """
    try:
        supabase = get_supabase_client()

        query = supabase.table("ip_whitelist").select("*")

        if enabled_only:
            query = query.eq("enabled", True)

        if not include_expired:
            now = datetime.now(timezone.utc).isoformat()
            query = query.or_(f"expires_at.is.null,expires_at.gte.{now}")

        result = query.order("created_at", desc=True).execute()

        return result.data if result.data else []

    except Exception as e:
        logger.error(f"Error getting all IP whitelist entries: {e}")
        return []


def get_whitelist_entry_by_id(entry_id: str | UUID) -> dict[str, Any] | None:
    """
    Get a specific IP whitelist entry by ID.

    Args:
        entry_id: UUID of the whitelist entry

    Returns:
        Whitelist entry or None if not found
    """
    try:
        supabase = get_supabase_client()

        entry_id_str = str(entry_id) if isinstance(entry_id, UUID) else entry_id

        result = supabase.table("ip_whitelist").select("*").eq("id", entry_id_str).execute()

        if result.data:
            return result.data[0]
        return None

    except Exception as e:
        logger.error(f"Error getting IP whitelist entry {entry_id}: {e}")
        return None
