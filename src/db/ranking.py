import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

# (time_period label, get_trending_models time_range) — the label values are a
# public contract with the frontend (gatewayz-frontend reads these exact
# strings: "Top today", "Top this week", "Top this month", "Trending").
_TIME_BUCKETS: list[tuple[str, str]] = [
    ("Trending", "1h"),
    ("Top today", "24h"),
    ("Top this week", "7d"),
    ("Top this month", "30d"),
]

# Below this many real-usage rows for a bucket, fall back to the scraped
# snapshot for that bucket so the page never renders empty while traffic is
# still low. Logged (not silent) so it's obvious when this can be removed.
_MIN_REAL_ROWS = 5


def get_all_latest_models(
    limit: int | None = None, offset: int | None = None
) -> list[dict[str, Any]]:
    """Get all data from latest_models table for ranking page with logo URLs"""
    try:
        client = get_supabase_client()

        # Build query with optional pagination
        query = client.table("latest_models").select("*")

        # Apply ordering by rank (ascending order - rank 1 first)
        query = query.order("rank", desc=False)

        # Apply pagination if specified
        if offset:
            query = query.range(offset, offset + (limit or 50) - 1)
        elif limit:
            query = query.limit(limit)

        result = query.execute()

        if not result.data:
            logger.info("No models found in latest_models table")
            return []

        # Enhance models with logo URLs if not present
        enhanced_models = []
        for model in result.data:
            enhanced_model = model.copy()

            # Generate logo URL if not present
            if "logo_url" not in model or not model.get("logo_url"):
                logo_url = generate_logo_url_from_author(model.get("author", ""))
                if logo_url:
                    enhanced_model["logo_url"] = logo_url

            enhanced_models.append(enhanced_model)

        logger.info(
            f"Retrieved {len(enhanced_models)} models from latest_models table with logo URLs"
        )
        return enhanced_models

    except Exception as e:
        logger.error(f"Failed to get latest models: {e}")
        raise RuntimeError(f"Failed to get latest models: {e}") from e


def _get_latest_models_for_bucket(time_period: str) -> list[dict[str, Any]]:
    """Scraped-snapshot rows for one time_period bucket (fallback source)."""
    try:
        client = get_supabase_client()
        result = (
            client.table("latest_models")
            .select("*")
            .eq("time_period", time_period)
            .order("rank", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to get latest_models for bucket {time_period!r}: {e}")
        return []


def _format_tokens(total_tokens: int) -> str:
    """Human-readable token count, e.g. 4650000000000 -> '4.65T tokens'."""
    value = float(total_tokens or 0)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= threshold:
            return f"{value / threshold:.2f}{suffix} tokens"
    return f"{int(value)} tokens"


def _derive_author(model_id: str) -> str:
    if model_id and "/" in model_id:
        return model_id.split("/", 1)[0]
    return "Gatewayz"


def _derive_model_url(model_id: str, provider_slug: str | None) -> str:
    """Mirrors gatewayz-frontend's src/lib/utils.ts:getModelUrl path convention."""
    if not model_id:
        return "/models"
    if ":" in model_id:
        provider, name = model_id.split(":", 1)
        return f"/models/{provider.lower()}/{name}"
    if "/" in model_id:
        return f"/models/{model_id}"
    return f"/models/{(provider_slug or 'gatewayz').lower()}/{model_id}"


def _build_catalog_index() -> dict[str, dict[str, Any]]:
    """model id (and lowercased id) -> catalog row, for enriching usage stats."""
    from src.services.models import get_cached_models

    try:
        catalog = get_cached_models("all") or []
    except Exception as e:
        logger.warning(f"Ranking: catalog fetch failed, enrichment will be partial: {e}")
        catalog = []

    index: dict[str, dict[str, Any]] = {}
    for entry in catalog:
        model_id = entry.get("id")
        if not model_id:
            continue
        index[model_id] = entry
        index.setdefault(model_id.lower(), entry)
    return index


def _usage_row_to_ranking_row(
    stats: dict[str, Any], rank: int, time_period: str, catalog_index: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    model_id = stats.get("model") or ""
    catalog_entry = catalog_index.get(model_id) or catalog_index.get(model_id.lower()) or {}

    author = _derive_author(model_id)
    provider_slug = (
        catalog_entry.get("provider_slug") or stats.get("gateway") or stats.get("provider")
    )
    model_name = catalog_entry.get("name") or model_id

    return {
        "rank": rank,
        "model_name": model_name,
        "author": author,
        "tokens": _format_tokens(stats.get("total_tokens", 0)),
        # No prior-window baseline is computed in this version — report flat
        # rather than fabricate a trend delta.
        "trend_percentage": "0%",
        "trend_direction": "flat",
        "trend_icon": "•",
        "trend_color": "gray",
        "model_url": _derive_model_url(model_id, provider_slug),
        "author_url": f"/organizations/{author}",
        "time_period": time_period,
        "id": model_id,
        "provider_slug": provider_slug,
        "context_length": catalog_entry.get("context_length"),
        "pricing": catalog_entry.get("pricing"),
        "description": catalog_entry.get("description"),
        "logo_url": generate_logo_url_from_author(author),
        "requests": stats.get("requests", 0),
        "unique_users": stats.get("unique_users", 0),
        "source": "usage",
    }


def get_ranking_models_from_usage(
    limit: int | None = None, offset: int | None = None
) -> list[dict[str, Any]]:
    """Ranking rows sourced from real Gatewayz usage (activity_log), enriched
    from the model catalog, in the same shape as the legacy scraped
    latest_models rows the frontend already consumes.

    Falls back to the scraped snapshot per-bucket when a bucket doesn't yet
    have enough real usage rows (traffic is still ramping up post-pivot).
    """
    from src.db.gateway_analytics import get_trending_models

    catalog_index = _build_catalog_index()
    rows: list[dict[str, Any]] = []

    for time_period, time_range in _TIME_BUCKETS:
        try:
            usage_stats = get_trending_models(
                gateway="all", time_range=time_range, limit=20, sort_by="requests"
            )
        except Exception as e:
            logger.warning(f"Ranking: usage query failed for bucket {time_period!r}: {e}")
            usage_stats = []

        if len(usage_stats) < _MIN_REAL_ROWS:
            logger.info(
                f"Ranking: bucket {time_period!r} has only {len(usage_stats)} real usage rows "
                f"(< {_MIN_REAL_ROWS}); falling back to scraped snapshot for this bucket"
            )
            rows.extend(_get_latest_models_for_bucket(time_period))
            continue

        rows.extend(
            _usage_row_to_ranking_row(stats, idx + 1, time_period, catalog_index)
            for idx, stats in enumerate(usage_stats)
        )

    # Backfill logo URLs for any row missing one (scraped rows fall through here too).
    for row in rows:
        if not row.get("logo_url"):
            logo_url = generate_logo_url_from_author(row.get("author", ""))
            if logo_url:
                row["logo_url"] = logo_url

    if offset:
        rows = rows[offset:]
    if limit:
        rows = rows[:limit]

    return rows


def generate_logo_url_from_author(author: str) -> str:
    """Generate logo URL from author name using Google favicon service"""
    if not author:
        return None

    # Map author names to domains
    author_domain_map = {
        "openai": "openai.com",
        "anthropic": "anthropic.com",
        "google": "google.com",
        "x-ai": "x.ai",
        "deepseek": "deepseek.com",
        "z-ai": "zhipuai.cn",
        "meta": "meta.com",
        "microsoft": "microsoft.com",
        "cohere": "cohere.com",
        "mistralai": "mistral.ai",
        "perplexity": "perplexity.ai",
        "amazon": "aws.amazon.com",
        "baidu": "baidu.com",
        "tencent": "tencent.com",
        "alibaba": "alibaba.com",
        "ai21": "ai21.com",
        "inflection": "inflection.ai",
    }

    # Get domain for author
    domain = author_domain_map.get(author.lower())
    if not domain:
        # Try to use author as domain if it looks like a domain
        if "." in author:
            domain = author
        else:
            return None

    # Generate Google favicon URL
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"


def get_all_latest_apps() -> list[dict[str, Any]]:
    """Get all data from latest_apps table for ranking page"""
    try:
        client = get_supabase_client()

        result = client.table("latest_apps").select("*").execute()

        if not result.data:
            logger.info("No apps found in latest_apps table")
            return []

        logger.info(f"Retrieved {len(result.data)} apps from latest_apps table")
        return result.data

    except Exception as e:
        logger.error(f"Failed to get latest apps: {e}")
        raise RuntimeError(f"Failed to get latest apps: {e}") from e
