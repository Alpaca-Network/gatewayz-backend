"""USD spot prices for holdings rewards, from CoinGecko's free API.

**This layer fails closed, and callers depend on that.** A price is returned
only when we can prove it is both real and recent. Anything we cannot prove --
an id CoinGecko doesn't know, an entry with no ``last_updated_at``, a price
older than ``HOLDINGS_PRICE_MAX_STALENESS_SECONDS``, a non-positive or
unparseable number, a network failure -- is **omitted from the returned dict**.
It is never guessed, never defaulted to zero, and never carried forward from a
stale cache entry.

The contract for callers is therefore: *a missing price id means "cannot value
this token today, skip it"*, not "this token is worth nothing". This is money.
A wrong or stale price silently overpays every holder of that token, on every
snapshot, until someone notices.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import requests

from src.config.config import Config

logger = logging.getLogger(__name__)

COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
REQUEST_TIMEOUT_SECONDS = 10
_CACHE_KEY_PREFIX = "holdings:price:usd"


@dataclass(frozen=True)
class PricePoint:
    """A USD price and the moment the source says it was observed.

    ``as_of`` is CoinGecko's ``last_updated_at``, not our fetch time -- the
    staleness check has to measure the age of the *quote*, not the age of our
    request.
    """

    price: Decimal
    as_of: datetime

    def age_seconds(self, now: datetime | None = None) -> float:
        return ((now or datetime.now(UTC)) - self.as_of).total_seconds()

    def is_fresh(self, max_age_seconds: int, now: datetime | None = None) -> bool:
        return 0 <= self.age_seconds(now) <= max_age_seconds


# In-process fallback cache, used when Redis is unavailable: id -> PricePoint.
# Entries still get freshness-checked on read, so a stale one can never be
# served even if its TTL has not elapsed.
_LOCAL_CACHE: dict[str, PricePoint] = {}


def get_usd_prices(price_ids: list[str]) -> dict[str, PricePoint]:
    """Return USD prices for ``price_ids``, omitting any we cannot vouch for.

    Cached fresh prices are served without a network call; only the remaining
    ids are requested from CoinGecko, in a single batched call.

    Args:
        price_ids: CoinGecko coin ids (e.g. ``"ethereum"``, ``"usd-coin"``).
            Blanks and duplicates are ignored.

    Returns:
        A dict keyed by price id. **The dict may be smaller than the input,
        and may be empty.** A missing key means "no trustworthy price right
        now" -- the caller must skip that token for this run rather than
        substituting a default. See the module docstring.
    """
    wanted = _normalise_ids(price_ids)
    if not wanted:
        return {}

    max_age = Config.HOLDINGS_PRICE_MAX_STALENESS_SECONDS
    prices: dict[str, PricePoint] = {}

    missing: list[str] = []
    for price_id in wanted:
        cached = _cache_get(price_id)
        if cached is not None and cached.is_fresh(max_age):
            prices[price_id] = cached
        else:
            missing.append(price_id)

    if not missing:
        return prices

    for price_id, point in _fetch_from_coingecko(missing, max_age).items():
        _cache_set(price_id, point, ttl_seconds=max_age)
        prices[price_id] = point

    skipped = sorted(set(wanted) - set(prices))
    if skipped:
        logger.warning(
            "Holdings pricing: no trustworthy USD price for %s -- these tokens "
            "must be skipped, not valued at zero",
            ", ".join(skipped),
        )

    return prices


def _fetch_from_coingecko(price_ids: list[str], max_age: int) -> dict[str, PricePoint]:
    """One batched simple/price call. Returns only fresh, valid entries."""
    try:
        response = requests.get(
            COINGECKO_SIMPLE_PRICE_URL,
            params={
                "ids": ",".join(price_ids),
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - any failure means "no price", never a default
        logger.warning("Holdings pricing: CoinGecko request failed (%s); returning no prices", exc)
        return {}

    if not isinstance(payload, dict):
        logger.warning("Holdings pricing: unexpected CoinGecko payload type %s", type(payload))
        return {}

    now = datetime.now(UTC)
    fresh: dict[str, PricePoint] = {}
    for price_id in price_ids:
        point = _parse_entry(price_id, payload.get(price_id))
        if point is None:
            continue
        if not point.is_fresh(max_age, now):
            logger.warning(
                "Holdings pricing: %s price is %.0fs old (max %ss) -- omitted",
                price_id,
                point.age_seconds(now),
                max_age,
            )
            continue
        fresh[price_id] = point

    return fresh


def _parse_entry(price_id: str, entry: object) -> PricePoint | None:
    """Convert one CoinGecko entry to a PricePoint, or None if untrustworthy."""
    if not isinstance(entry, dict):
        return None

    raw_price = entry.get("usd")
    raw_timestamp = entry.get("last_updated_at")
    if raw_price is None or raw_timestamp is None:
        return None

    try:
        # Via str() so a float like 2500.25 doesn't arrive as 2500.2499999...
        price = Decimal(str(raw_price))
        as_of = datetime.fromtimestamp(int(raw_timestamp), tz=UTC)
    except (InvalidOperation, TypeError, ValueError, OSError, OverflowError):
        logger.warning("Holdings pricing: unparseable entry for %s: %r", price_id, entry)
        return None

    if not price.is_finite() or price <= 0:
        logger.warning("Holdings pricing: non-positive price for %s: %r", price_id, raw_price)
        return None

    return PricePoint(price=price, as_of=as_of)


def _normalise_ids(price_ids: list[str]) -> list[str]:
    """Strip, drop blanks, de-duplicate, keep first-seen order."""
    seen: dict[str, None] = {}
    for price_id in price_ids or []:
        if not isinstance(price_id, str):
            continue
        cleaned = price_id.strip()
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)


def _redis_client():
    """Return a Redis client, or None to use the in-process cache instead.

    Imported lazily and defensively: pricing must keep working on a box with
    no Redis, and a cache outage must never become a pricing outage.
    """
    try:
        from src.config.redis_config import get_redis_client, is_redis_available

        if not is_redis_available():
            return None
        return get_redis_client()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Holdings pricing: Redis unavailable (%s); using in-process cache", exc)
        return None


def _cache_key(price_id: str) -> str:
    return f"{_CACHE_KEY_PREFIX}:{price_id}"


def _cache_get(price_id: str) -> PricePoint | None:
    client = _redis_client()
    if client is None:
        return _LOCAL_CACHE.get(price_id)

    try:
        raw = client.get(_cache_key(price_id))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        return PricePoint(
            price=Decimal(str(payload["price"])),
            as_of=datetime.fromtimestamp(int(payload["as_of"]), tz=UTC),
        )
    except Exception as exc:  # noqa: BLE001 - a bad cache entry is simply a miss
        logger.debug("Holdings pricing: cache read failed for %s (%s)", price_id, exc)
        return None


def _cache_set(price_id: str, point: PricePoint, *, ttl_seconds: int) -> None:
    client = _redis_client()
    if client is None:
        _local_cache_set(price_id, point)
        return

    try:
        client.setex(
            _cache_key(price_id),
            max(int(ttl_seconds), 1),
            json.dumps({"price": str(point.price), "as_of": int(point.as_of.timestamp())}),
        )
    except Exception as exc:  # noqa: BLE001 - never fail a price read over a cache write
        logger.debug("Holdings pricing: cache write failed for %s (%s)", price_id, exc)


def _local_cache_set(price_id: str, point: PricePoint) -> None:
    _LOCAL_CACHE[price_id] = point


def _local_cache_clear() -> None:
    _LOCAL_CACHE.clear()


__all__ = [
    "COINGECKO_SIMPLE_PRICE_URL",
    "PricePoint",
    "get_usd_prices",
]
