"""
IP Classification Service

This service provides IP address classification capabilities including:
- Datacenter/cloud provider detection via CIDR ranges
- ASN-based classification (optional)
- Redis caching for performance
- Geolocation hints (future enhancement)
"""

import asyncio
import logging

from src.config.datacenter_ips import (
    is_datacenter_asn,
    is_datacenter_ip,
)

logger = logging.getLogger(__name__)

# Cache TTL for IP classification results (24 hours)
IP_CLASSIFICATION_CACHE_TTL = 86400


class IPClassificationResult:
    """Result of IP classification"""

    def __init__(
        self,
        ip: str,
        is_datacenter: bool = False,
        provider_name: str | None = None,
        asn: int | None = None,
        classification_method: str = "cidr",
    ):
        self.ip = ip
        self.is_datacenter = is_datacenter
        self.provider_name = provider_name
        self.asn = asn
        self.classification_method = classification_method

    def __repr__(self):
        return (
            f"IPClassificationResult(ip={self.ip}, "
            f"is_datacenter={self.is_datacenter}, "
            f"provider={self.provider_name}, "
            f"asn={self.asn}, "
            f"method={self.classification_method})"
        )

    def to_dict(self):
        """Convert to dictionary for caching"""
        return {
            "ip": self.ip,
            "is_datacenter": self.is_datacenter,
            "provider_name": self.provider_name,
            "asn": self.asn,
            "classification_method": self.classification_method,
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Create from dictionary"""
        return cls(
            ip=data["ip"],
            is_datacenter=data["is_datacenter"],
            provider_name=data.get("provider_name"),
            asn=data.get("asn"),
            classification_method=data.get("classification_method", "cidr"),
        )


class IPClassificationService:
    """Service for classifying IP addresses"""

    def __init__(self, redis_client=None):
        """
        Initialize IP classification service.

        Args:
            redis_client: Optional Redis client for caching
        """
        self.redis = redis_client
        self._local_cache = {}  # Fallback in-memory cache

    async def classify_ip(self, ip: str, check_asn: bool = False) -> IPClassificationResult:
        """
        Classify an IP address.

        Args:
            ip: IP address to classify
            check_asn: Whether to perform ASN lookup (slower, requires external API)

        Returns:
            IPClassificationResult with classification details

        Note:
            - CIDR-based detection is used by default (fast, no external calls)
            - ASN lookup is optional and requires external API calls (slower)
            - Results are cached in Redis for 24 hours
        """
        # Check cache first
        cached_result = await self._get_cached_result(ip)
        if cached_result:
            logger.debug(f"IP classification cache hit for {ip}")
            return cached_result

        # Perform classification
        result = await self._classify_ip_uncached(ip, check_asn)

        # Cache the result
        await self._cache_result(result)

        return result

    async def _classify_ip_uncached(
        self, ip: str, check_asn: bool = False
    ) -> IPClassificationResult:
        """
        Classify an IP address without using cache.

        Args:
            ip: IP address to classify
            check_asn: Whether to perform ASN lookup

        Returns:
            IPClassificationResult
        """
        # Step 1: Check CIDR ranges (fast, local)
        is_dc_cidr = is_datacenter_ip(ip)

        if is_dc_cidr:
            logger.debug(f"IP {ip} classified as datacenter via CIDR match")
            return IPClassificationResult(
                ip=ip,
                is_datacenter=True,
                classification_method="cidr",
            )

        # Step 2: ASN lookup (optional, slow)
        if check_asn:
            asn_result = await self._lookup_asn(ip)
            if asn_result:
                asn, provider_name = asn_result
                is_dc_asn = is_datacenter_asn(asn)

                if is_dc_asn:
                    logger.debug(
                        f"IP {ip} classified as datacenter via ASN {asn} ({provider_name})"
                    )
                    return IPClassificationResult(
                        ip=ip,
                        is_datacenter=True,
                        provider_name=provider_name,
                        asn=asn,
                        classification_method="asn",
                    )

        # Not a datacenter IP
        logger.debug(f"IP {ip} classified as non-datacenter")
        return IPClassificationResult(
            ip=ip,
            is_datacenter=False,
            classification_method="cidr" if not check_asn else "asn",
        )

    async def _lookup_asn(self, ip: str) -> tuple[int, str] | None:
        """
        Look up the ASN for an IP address.

        Args:
            ip: IP address to lookup

        Returns:
            Tuple of (asn, provider_name) or None if lookup fails

        Note:
            This is a placeholder for ASN lookup. In production, you could use:
            - ipwhois library (requires external API calls)
            - MaxMind GeoIP2 ASN database (requires license/download)
            - Team Cymru IP to ASN service (DNS-based, free)

            For now, we skip ASN lookup to avoid external dependencies.
        """
        # TODO: Implement ASN lookup if needed
        # Options:
        # 1. Use ipwhois library (requires pip install ipwhois)
        # 2. Use MaxMind GeoIP2 ASN database
        # 3. Use Team Cymru DNS-based lookup (dig +short {ip}.origin.asn.cymru.com TXT)

        logger.debug(f"ASN lookup not implemented for {ip}, skipping")
        return None

    async def _get_cached_result(self, ip: str) -> IPClassificationResult | None:
        """Get cached classification result from Redis or local cache"""
        cache_key = f"ip_classification:{ip}"

        # Try Redis first
        if self.redis:
            try:
                import json

                cached = await asyncio.to_thread(self.redis.get, cache_key)
                if cached:
                    data = json.loads(cached)
                    return IPClassificationResult.from_dict(data)
            except Exception as e:
                logger.debug(f"Redis cache get failed for {ip}: {e}")

        # Fallback to local cache
        if cache_key in self._local_cache:
            return self._local_cache[cache_key]

        return None

    async def _cache_result(self, result: IPClassificationResult):
        """Cache classification result in Redis and local cache"""
        cache_key = f"ip_classification:{result.ip}"

        # Cache in Redis
        if self.redis:
            try:
                import json

                data = json.dumps(result.to_dict())
                await asyncio.to_thread(
                    self.redis.setex,
                    cache_key,
                    IP_CLASSIFICATION_CACHE_TTL,
                    data,
                )
            except Exception as e:
                logger.debug(f"Redis cache set failed for {result.ip}: {e}")

        # Also cache locally (bounded cache)
        self._local_cache[cache_key] = result
        # Keep local cache bounded to 1000 entries
        if len(self._local_cache) > 1000:
            # Remove oldest 200 entries (simple approach - no LRU)
            keys_to_remove = list(self._local_cache.keys())[:200]
            for key in keys_to_remove:
                del self._local_cache[key]

    async def is_datacenter_ip_fast(self, ip: str) -> bool:
        """
        Fast check if IP is a datacenter IP (CIDR-only, no ASN lookup).

        This is the recommended method for hot paths like middleware.

        Args:
            ip: IP address to check

        Returns:
            True if IP is in a known datacenter CIDR range
        """
        # Check local cache first (no Redis lookup for maximum speed)
        cache_key = f"ip_classification:{ip}"
        if cache_key in self._local_cache:
            return self._local_cache[cache_key].is_datacenter

        # Check CIDR ranges (local, fast)
        is_dc = is_datacenter_ip(ip)

        # Cache result locally
        result = IPClassificationResult(
            ip=ip,
            is_datacenter=is_dc,
            classification_method="cidr_fast",
        )
        self._local_cache[cache_key] = result

        # Keep local cache bounded
        if len(self._local_cache) > 1000:
            keys_to_remove = list(self._local_cache.keys())[:200]
            for key in keys_to_remove:
                del self._local_cache[key]

        return is_dc

    def is_datacenter_ip_sync(self, ip: str) -> bool:
        """
        Synchronous version of datacenter IP check (CIDR-only).

        Use this when you can't use async/await.

        Args:
            ip: IP address to check

        Returns:
            True if IP is in a known datacenter CIDR range
        """
        # Check local cache first
        cache_key = f"ip_classification:{ip}"
        if cache_key in self._local_cache:
            return self._local_cache[cache_key].is_datacenter

        # Check CIDR ranges
        is_dc = is_datacenter_ip(ip)

        # Cache result
        result = IPClassificationResult(
            ip=ip,
            is_datacenter=is_dc,
            classification_method="cidr_sync",
        )
        self._local_cache[cache_key] = result

        # Keep cache bounded
        if len(self._local_cache) > 1000:
            keys_to_remove = list(self._local_cache.keys())[:200]
            for key in keys_to_remove:
                del self._local_cache[key]

        return is_dc

    async def clear_cache(self, ip: str | None = None):
        """
        Clear IP classification cache.

        Args:
            ip: Specific IP to clear, or None to clear all
        """
        if ip:
            cache_key = f"ip_classification:{ip}"
            if self.redis:
                try:
                    await asyncio.to_thread(self.redis.delete, cache_key)
                except Exception as e:
                    logger.debug(f"Redis cache delete failed for {ip}: {e}")
            if cache_key in self._local_cache:
                del self._local_cache[cache_key]
        else:
            # Clear all
            if self.redis:
                try:
                    # Use pattern matching to find all ip_classification keys
                    pattern = "ip_classification:*"
                    keys = await asyncio.to_thread(self.redis.keys, pattern)
                    if keys:
                        await asyncio.to_thread(self.redis.delete, *keys)
                except Exception as e:
                    logger.debug(f"Redis cache clear failed: {e}")
            self._local_cache.clear()


# Global instance
_ip_classification_service = None


def get_ip_classification_service(redis_client=None) -> IPClassificationService:
    """
    Get or create the global IP classification service instance.

    Args:
        redis_client: Optional Redis client for caching

    Returns:
        IPClassificationService instance
    """
    global _ip_classification_service
    if _ip_classification_service is None:
        _ip_classification_service = IPClassificationService(redis_client)
    return _ip_classification_service


# Convenience functions
async def classify_ip(ip: str, check_asn: bool = False) -> IPClassificationResult:
    """
    Classify an IP address.

    Args:
        ip: IP address to classify
        check_asn: Whether to perform ASN lookup (slower)

    Returns:
        IPClassificationResult
    """
    service = get_ip_classification_service()
    return await service.classify_ip(ip, check_asn)


async def is_datacenter_ip_fast(ip: str) -> bool:
    """
    Fast check if IP is a datacenter IP (recommended for hot paths).

    Args:
        ip: IP address to check

    Returns:
        True if IP is a datacenter IP
    """
    service = get_ip_classification_service()
    return await service.is_datacenter_ip_fast(ip)


def is_datacenter_ip_sync(ip: str) -> bool:
    """
    Synchronous check if IP is a datacenter IP.

    Args:
        ip: IP address to check

    Returns:
        True if IP is a datacenter IP
    """
    service = get_ip_classification_service()
    return service.is_datacenter_ip_sync(ip)
