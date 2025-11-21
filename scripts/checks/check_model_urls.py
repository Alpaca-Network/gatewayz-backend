#!/usr/bin/env python3
"""
Check and repair OpenRouter model URLs stored in Supabase.

The script fetches records from the openrouter_models table, verifies that each
model_url responds with a healthy HTTP status, and attempts to fix broken URLs
by rebuilding canonical slugs when possible.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import httpx
from supabase import Client

# Ensure project root is on sys.path for src imports
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config.supabase_config import get_supabase_client  # noqa: E402

logger = logging.getLogger("model_url_checker")


@dataclass
class CheckResult:
    model_id: int
    model_name: str
    author: str
    original_url: Optional[str]
    status: str
    status_code: Optional[int] = None
    final_url: Optional[str] = None
    error: Optional[str] = None


def slugify(value: Optional[str]) -> str:
    """Convert a model or author name to a URL-friendly slug."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    replaced = re.sub(r"[^\w\s-]", "", lowered)
    collapsed = re.sub(r"[\s_]+", "-", replaced).strip("-")
    return collapsed


def build_candidate_urls(author: str, model_name: str) -> List[str]:
    """Generate likely URLs for a given author/model combination."""
    author_slug = slugify(author)
    model_slug = slugify(model_name)

    candidates: List[str] = []
    paths: List[str] = []

    if author_slug and model_slug:
        paths.append(f"{author_slug}/{model_slug}")
    if model_slug:
        paths.append(model_slug)

    base_urls = [
        "https://openrouter.ai/models",
        "https://www.openrouter.ai/models",
        "https://gatewayz.ai/models",
    ]

    for base in base_urls:
        for path in paths:
            candidates.append(f"{base}/{path}")

    # Remove duplicates while preserving order
    seen: Dict[str, None] = {}
    unique_candidates = [seen.setdefault(url, None) or url for url in candidates if url not in seen]
    return unique_candidates


def fetch_models(client: Client, limit: Optional[int], offset: int, batch_size: int) -> List[Dict]:
    """Fetch openrouter_models records in batches."""
    records: List[Dict] = []
    start = offset

    while True:
        end = start + batch_size - 1
        query = (
            client.table("openrouter_models")
            .select("*")
            .order("id", desc=False)
            .range(start, end)
        )
        response = query.execute()
        data = response.data or []
        records.extend(data)

        if limit is not None and len(records) >= limit:
            return records[:limit]

        if len(data) < batch_size:
            break

        start += batch_size

    return records


def check_url(http_client: httpx.Client, url: str, timeout: float) -> Tuple[bool, Optional[int], Optional[str], Optional[str]]:
    """Return True if url responds with a healthy status code."""
    try:
        response = http_client.get(url, timeout=timeout)
        status = response.status_code
        final_url = str(response.url)
        if 200 <= status < 400:
            return True, status, final_url, None
        return False, status, final_url, response.text[:200]
    except httpx.HTTPError as exc:
        return False, None, None, str(exc)


def update_model_url(client: Client, model_id: int, new_url: str, dry_run: bool) -> bool:
    """Persist the repaired URL back to Supabase."""
    if dry_run:
        logger.info("Dry run: would update id=%s -> %s", model_id, new_url)
        return True

    try:
        result = client.table("openrouter_models").update({"model_url": new_url}).eq("id", model_id).execute()
        return bool(result.data)
    except Exception as exc:
        logger.error("Failed to update model %s: %s", model_id, exc)
        return False


def attempt_fix(client: Client, http_client: httpx.Client, model: Dict, timeout: float, dry_run: bool) -> Optional[CheckResult]:
    """Try to repair a broken model URL using slug heuristics."""
    candidates = build_candidate_urls(model.get("author", ""), model.get("model_name", ""))
    for candidate in candidates:
        ok, status, final_url, error = check_url(http_client, candidate, timeout)
        if ok:
            updated = update_model_url(client, model["id"], final_url or candidate, dry_run)
            status_label = "fixed" if updated else "fix_failed"
            return CheckResult(
                model_id=model["id"],
                model_name=model.get("model_name", ""),
                author=model.get("author", ""),
                original_url=model.get("model_url"),
                status=status_label,
                status_code=status,
                final_url=final_url or candidate,
                error=None if updated else "Supabase update failed",
            )
    return None


def evaluate_model(client: Client, http_client: httpx.Client, model: Dict, timeout: float, dry_run: bool) -> CheckResult:
    """Check a single model record and attempt repairs when needed."""
    model_id = model.get("id")
    model_name = model.get("model_name", "")
    author = model.get("author", "")
    model_url = model.get("model_url")

    if not model_url:
        logger.warning("Model %s (%s) missing model_url; attempting repair", model_id, model_name)
        fix_result = attempt_fix(client, http_client, model, timeout, dry_run)
        if fix_result:
            return fix_result
        return CheckResult(
            model_id=model_id,
            model_name=model_name,
            author=author,
            original_url=None,
            status="missing_unfixed",
            error="No model_url present and fix failed",
        )

    ok, status_code, final_url, error = check_url(http_client, model_url, timeout)
    if ok:
        needs_update = final_url and final_url.rstrip("/") != model_url.rstrip("/")
        if needs_update:
            updated = update_model_url(client, model_id, final_url, dry_run)
            if updated:
                return CheckResult(
                    model_id=model_id,
                    model_name=model_name,
                    author=author,
                    original_url=model_url,
                    status="normalized",
                    status_code=status_code,
                    final_url=final_url,
                )
            logger.error("Failed to persist normalized URL for model %s", model_id)
            return CheckResult(
                model_id=model_id,
                model_name=model_name,
                author=author,
                original_url=model_url,
                status="normalize_failed",
                status_code=status_code,
                final_url=final_url,
                error="Supabase update failed",
            )

        return CheckResult(
            model_id=model_id,
            model_name=model_name,
            author=author,
            original_url=model_url,
            status="healthy",
            status_code=status_code,
            final_url=final_url or model_url,
        )

    logger.warning(
        "Model %s (%s) URL failed (%s). Attempting repair.",
        model_id,
        model_name,
        error or status_code,
    )
    fix_result = attempt_fix(client, http_client, model, timeout, dry_run)
    if fix_result:
        return fix_result

    return CheckResult(
        model_id=model_id,
        model_name=model_name,
        author=author,
        original_url=model_url,
        status="failed",
        status_code=status_code,
        final_url=None,
        error=error or "Unknown failure",
    )


def summarize(results: Iterable[CheckResult]) -> Dict[str, int]:
    """Build summary counts keyed by status."""
    summary: Dict[str, int] = {}
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
    return summary


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and repair OpenRouter model URLs."
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of records to check.")
    parser.add_argument("--offset", type=int, default=0, help="Start checking from this zero-based index.")
    parser.add_argument("--batch-size", type=int, default=500, help="Number of records to fetch per Supabase request.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout per request in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Do not persist fixes; report only.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)

    try:
        supabase_client = get_supabase_client()
    except Exception as exc:
        logger.error("Failed to initialise Supabase client: %s", exc)
        sys.exit(2)

    logger.info("Fetching model records (limit=%s offset=%s)...", args.limit or "all", args.offset)
    models = fetch_models(supabase_client, args.limit, args.offset, args.batch_size)
    if not models:
        logger.warning("No model records retrieved.")
        sys.exit(0)

    results: List[CheckResult] = []
    start_time = time.time()

    with httpx.Client(follow_redirects=True, timeout=args.timeout) as http_client:
        for index, model in enumerate(models, 1):
            result = evaluate_model(supabase_client, http_client, model, args.timeout, args.dry_run)
            results.append(result)

            if args.verbose or result.status != "healthy":
                logger.info(
                    "[%s/%s] id=%s status=%s url=%s",
                    index,
                    len(models),
                    result.model_id,
                    result.status.upper(),
                    result.final_url or result.original_url or "N/A",
                )
                if result.error:
                    logger.debug("  → %s", result.error)

    duration = time.time() - start_time
    summary = summarize(results)

    logger.info("Completed in %.2fs", duration)
    logger.info("Summary: %s", summary)

    failures = summary.get("failed", 0) + summary.get("missing_unfixed", 0) + summary.get("fix_failed", 0)
    sys.exit(1 if failures and not args.dry_run else 0)


if __name__ == "__main__":
    main()
