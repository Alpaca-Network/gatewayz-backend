"""Trial Analytics Routes - Admin endpoints for monitoring trial usage and conversions"""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config.redis_config import get_redis_config
from src.config.supabase_config import get_supabase_client
from src.schemas.trial_analytics import (
    BestWorstCohort,
    CohortAnalysisResponse,
    CohortData,
    CohortSummary,
    ConversionBreakdown,
    ConversionFunnelData,
    ConversionFunnelResponse,
    DomainAnalysis,
    DomainAnalysisResponse,
    IPAnalysisResponse,
    SaveConversionMetricsRequest,
    SaveConversionMetricsResponse,
    TrialUser,
    TrialUsersPagination,
    TrialUsersResponse,
)
from src.security.deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


def calculate_abuse_score(domain_data: dict) -> float:
    """
    Calculate abuse score for a domain based on usage patterns
    Score range: 0-10 (>7 = flagged as suspicious)

    Factors:
    - High utilization (>80%) + Low conversion (<5%) = Higher score
    - High average usage per user (>2x normal) = Higher score
    - Multiple users with similar patterns = Higher score
    """
    score = 0.0

    # Factor 1: Utilization vs Conversion mismatch
    avg_utilization = (
        domain_data.get("avg_requests_per_user", 0) / 1000 * 100  # Assuming 1000 is max
    )
    conversion_rate = domain_data.get("conversion_rate", 0)

    if avg_utilization > 80 and conversion_rate < 5:
        score += 4.0
    elif avg_utilization > 60 and conversion_rate < 10:
        score += 2.0

    # Factor 2: High average usage (>2x normal)
    normal_avg_requests = 300  # Baseline normal usage
    avg_requests = domain_data.get("avg_requests_per_user", 0)

    if avg_requests > normal_avg_requests * 2:
        score += 3.0
    elif avg_requests > normal_avg_requests * 1.5:
        score += 1.5

    # Factor 3: Large number of users with no conversions
    total_users = domain_data.get("total_users", 0)
    converted = domain_data.get("converted_trials", 0)

    if total_users > 10 and converted == 0:
        score += 3.0
    elif total_users > 5 and converted == 0:
        score += 1.0

    return min(score, 10.0)  # Cap at 10


@router.get(
    "/admin/trial/users", response_model=TrialUsersResponse, tags=["admin", "trial-analytics"]
)
async def get_trial_users(
    status: Literal["active", "expired", "converted", "all"] = Query(
        "all", description="Filter by trial status"
    ),
    sort_by: Literal["requests", "tokens", "credits", "created_at"] = Query(
        "created_at", description="Sort by field"
    ),
    sort_order: Literal["asc", "desc"] = Query("desc", description="Sort order"),
    limit: int = Query(100, ge=1, le=1000, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    domain_filter: str | None = Query(None, description="Filter by email domain"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get detailed trial user list with usage and segmentation data

    **Purpose:** Monitor trial usage to detect abuse, track conversion, and segment users
    """
    try:
        client = get_supabase_client()
        current_time = datetime.now(UTC)

        # Build base query joining users and api_keys
        query = client.table("users").select(
            "id, email, created_at, "
            "api_keys_new!inner("
            "id, api_key, is_trial, trial_converted, "
            "trial_start_date, trial_end_date, trial_used_tokens, "
            "trial_used_requests, trial_used_credits, trial_credits, "
            "last_used_at, created_at"
            ")",
            count="exact",
        )

        # Filter by trial status
        if status != "all":
            if status == "active":
                # Active trials: is_trial=true, not expired, not converted
                query = query.eq("api_keys_new.is_trial", True).eq(
                    "api_keys_new.trial_converted", False
                )
            elif status == "expired":
                # Expired trials: is_trial=true, expired, not converted
                query = query.eq("api_keys_new.is_trial", True).eq(
                    "api_keys_new.trial_converted", False
                )
            elif status == "converted":
                # Converted trials
                query = query.eq("api_keys_new.trial_converted", True)

        # Filter by domain if provided
        if domain_filter:
            query = query.ilike("email", f"%@{domain_filter}%")

        # Execute count query
        count_result = query.execute()
        total_count = count_result.count if count_result.count is not None else 0

        # Note: PostgREST doesn't support sorting by nested fields in joins
        # So we can only sort by users table fields (created_at, email)
        # For sorting by usage metrics, we'll need to do client-side sorting

        # Execute data query with pagination
        data_query = client.table("users").select(
            "id, email, created_at, "
            "api_keys_new!inner("
            "id, api_key, is_trial, trial_converted, "
            "trial_start_date, trial_end_date, trial_used_tokens, "
            "trial_used_requests, trial_used_credits, trial_credits, "
            "last_used_at, created_at"
            ")"
        )

        # Apply same filters
        if status != "all":
            if status == "active":
                data_query = data_query.eq("api_keys_new.is_trial", True).eq(
                    "api_keys_new.trial_converted", False
                )
            elif status == "expired":
                data_query = data_query.eq("api_keys_new.is_trial", True).eq(
                    "api_keys_new.trial_converted", False
                )
            elif status == "converted":
                data_query = data_query.eq("api_keys_new.trial_converted", True)

        if domain_filter:
            data_query = data_query.ilike("email", f"%@{domain_filter}%")

        # Apply sorting by users table field and pagination
        # Note: Only sorting by created_at is supported due to PostgREST join limitations
        data_query = data_query.order("created_at", desc=(sort_order == "desc")).range(
            offset, offset + limit - 1
        )

        result = data_query.execute()

        # Process results into TrialUser objects
        users = []
        for row in result.data if result.data else []:
            # Each user can have multiple API keys, process each one
            for api_key_data in row.get("api_keys_new", []):
                email = row.get("email", "")
                email_domain = email.split("@")[1] if "@" in email else ""

                # Parse trial dates
                trial_start = api_key_data.get("trial_start_date")
                trial_end = api_key_data.get("trial_end_date")

                if trial_start:
                    trial_start = datetime.fromisoformat(trial_start.replace("Z", "+00:00"))
                if trial_end:
                    trial_end = datetime.fromisoformat(trial_end.replace("Z", "+00:00"))

                # Calculate trial status and days remaining
                trial_status = "unknown"
                days_remaining = None

                if api_key_data.get("trial_converted"):
                    trial_status = "converted"
                elif trial_end:
                    if trial_end > current_time:
                        trial_status = "active"
                        days_remaining = (trial_end - current_time).days
                    else:
                        trial_status = "expired"
                        days_remaining = 0

                # Calculate utilization percentages
                used_tokens = api_key_data.get("trial_used_tokens", 0)
                max_tokens = 500000  # Default from trial_config
                token_utilization = (used_tokens / max_tokens * 100) if max_tokens > 0 else 0

                used_requests = api_key_data.get("trial_used_requests", 0)
                max_requests = 1000  # Default from trial_config
                request_utilization = (
                    (used_requests / max_requests * 100) if max_requests > 0 else 0
                )

                used_credits = float(api_key_data.get("trial_used_credits", 0))
                allocated_credits = float(api_key_data.get("trial_credits", 10.0))
                credit_utilization = (
                    (used_credits / allocated_credits * 100) if allocated_credits > 0 else 0
                )

                # Create API key preview (show last 7 chars)
                api_key = api_key_data.get("api_key", "")
                api_key_preview = f"gw_****{api_key[-7:]}" if len(api_key) > 7 else "gw_****"

                # Parse last_used_at
                last_used = api_key_data.get("last_used_at")
                if last_used:
                    last_used = datetime.fromisoformat(last_used.replace("Z", "+00:00"))

                # TODO: Fetch conversion metrics from trial_conversion_metrics table
                # For now, set to None
                conversion_date = None
                requests_at_conversion = None
                tokens_at_conversion = None

                users.append(
                    TrialUser(
                        user_id=row["id"],
                        email=email,
                        email_domain=email_domain,
                        api_key_id=api_key_data["id"],
                        api_key_preview=api_key_preview,
                        is_trial=api_key_data.get("is_trial", False),
                        trial_start_date=trial_start,
                        trial_end_date=trial_end,
                        trial_status=trial_status,
                        trial_days_remaining=days_remaining,
                        trial_used_tokens=used_tokens,
                        trial_max_tokens=max_tokens,
                        trial_token_utilization=round(token_utilization, 2),
                        trial_used_requests=used_requests,
                        trial_max_requests=max_requests,
                        trial_request_utilization=round(request_utilization, 2),
                        trial_used_credits=round(used_credits, 2),
                        trial_allocated_credits=round(allocated_credits, 2),
                        trial_credit_utilization=round(credit_utilization, 2),
                        trial_converted=api_key_data.get("trial_converted", False),
                        conversion_date=conversion_date,
                        requests_at_conversion=requests_at_conversion,
                        tokens_at_conversion=tokens_at_conversion,
                        created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
                        signup_ip=None,  # TODO: Add IP tracking
                        last_request_at=last_used,
                    )
                )

        # Apply client-side sorting for usage metrics (since PostgREST can't sort by nested fields)
        if sort_by == "requests":
            users.sort(key=lambda u: u.trial_used_requests, reverse=(sort_order == "desc"))
        elif sort_by == "tokens":
            users.sort(key=lambda u: u.trial_used_tokens, reverse=(sort_order == "desc"))
        elif sort_by == "credits":
            users.sort(key=lambda u: u.trial_used_credits, reverse=(sort_order == "desc"))
        # created_at is already sorted by the database query

        return TrialUsersResponse(
            success=True,
            users=users,
            pagination=TrialUsersPagination(
                total=total_count,
                limit=limit,
                offset=offset,
                has_more=offset + limit < total_count,
            ),
        )

    except Exception as e:
        logger.error(f"Error getting trial users: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get trial users: {str(e)}") from e


@router.get(
    "/admin/trial/domain-analysis",
    response_model=DomainAnalysisResponse,
    tags=["admin", "trial-analytics"],
)
async def get_domain_analysis(
    admin_user: dict = Depends(require_admin),
):
    """
    Analyze trial users by email domain to detect potential abuse with Redis caching

    **Purpose:** Group users by domain and calculate abuse scores
    """
    CACHE_KEY = "trial:domain:analysis"
    CACHE_TTL = 300  # 5 minutes

    try:
        # Try to get from cache first
        redis_config = get_redis_config()
        cached_data = redis_config.get_cache(CACHE_KEY)

        if cached_data:
            try:
                logger.info("Returning domain analysis from cache")
                cached_result = json.loads(cached_data)
                # Return properly formatted response
                return DomainAnalysisResponse(
                    success=True,
                    domains=[DomainAnalysis(**d) for d in cached_result["domains"]],
                    suspicious_domains=cached_result["suspicious_domains"],
                )
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"Failed to decode cached domain analysis: {e}, fetching fresh data")

        client = get_supabase_client()
        current_time = datetime.now(UTC)

        # Fetch all trial users with their API key data
        # Note: Supabase has a default limit of 1000, so we need to paginate
        all_data = []
        page_size = 1000
        offset = 0

        while True:
            result = (
                client.table("users")
                .select(
                    "id, email, "
                    "api_keys_new!inner("
                    "is_trial, trial_converted, trial_end_date, "
                    "trial_used_tokens, trial_used_requests, trial_used_credits"
                    ")"
                )
                .range(offset, offset + page_size - 1)
                .execute()
            )

            if not result.data or len(result.data) == 0:
                break

            all_data.extend(result.data)

            # If we got less than page_size, we've reached the end
            if len(result.data) < page_size:
                break

            offset += page_size

        # Group by domain
        domain_stats = {}

        for row in all_data:
            email = row.get("email", "")
            domain = email.split("@")[1] if "@" in email else "unknown"

            if domain not in domain_stats:
                domain_stats[domain] = {
                    "total_users": 0,
                    "active_trials": 0,
                    "expired_trials": 0,
                    "converted_trials": 0,
                    "total_requests": 0,
                    "total_tokens": 0,
                    "total_credits": 0.0,
                }

            # Process each API key
            for api_key in row.get("api_keys_new", []):
                domain_stats[domain]["total_users"] += 1

                # Count trial status
                if api_key.get("trial_converted"):
                    domain_stats[domain]["converted_trials"] += 1
                elif api_key.get("is_trial"):
                    trial_end = api_key.get("trial_end_date")
                    if trial_end:
                        trial_end_dt = datetime.fromisoformat(trial_end.replace("Z", "+00:00"))
                        if trial_end_dt > current_time:
                            domain_stats[domain]["active_trials"] += 1
                        else:
                            domain_stats[domain]["expired_trials"] += 1

                # Aggregate usage
                domain_stats[domain]["total_requests"] += api_key.get("trial_used_requests", 0)
                domain_stats[domain]["total_tokens"] += api_key.get("trial_used_tokens", 0)
                domain_stats[domain]["total_credits"] += float(api_key.get("trial_used_credits", 0))

        # Calculate metrics and abuse scores
        domains = []
        suspicious_domains = []

        for domain, stats in domain_stats.items():
            total_users = stats["total_users"]
            converted = stats["converted_trials"]
            conversion_rate = (converted / total_users * 100) if total_users > 0 else 0

            avg_requests = stats["total_requests"] / total_users if total_users > 0 else 0
            avg_tokens = stats["total_tokens"] / total_users if total_users > 0 else 0

            # Calculate abuse score
            abuse_data = {
                "total_users": total_users,
                "converted_trials": converted,
                "conversion_rate": conversion_rate,
                "avg_requests_per_user": avg_requests,
            }
            abuse_score = calculate_abuse_score(abuse_data)
            flagged = abuse_score > 7.0

            if flagged:
                suspicious_domains.append(domain)

            domains.append(
                DomainAnalysis(
                    domain=domain,
                    total_users=total_users,
                    active_trials=stats["active_trials"],
                    expired_trials=stats["expired_trials"],
                    converted_trials=converted,
                    conversion_rate=round(conversion_rate, 2),
                    total_requests=stats["total_requests"],
                    total_tokens=stats["total_tokens"],
                    total_credits_used=round(stats["total_credits"], 2),
                    avg_requests_per_user=round(avg_requests, 2),
                    avg_tokens_per_user=round(avg_tokens, 2),
                    abuse_score=round(abuse_score, 2),
                    flagged=flagged,
                )
            )

        # Sort by abuse score descending
        domains.sort(key=lambda x: x.abuse_score, reverse=True)

        response_data = DomainAnalysisResponse(
            success=True,
            domains=domains,
            suspicious_domains=suspicious_domains,
        )

        # Cache the result
        try:
            cache_payload = {
                "domains": [d.dict() for d in domains],
                "suspicious_domains": suspicious_domains,
            }
            redis_config.set_cache(CACHE_KEY, json.dumps(cache_payload), CACHE_TTL)
            logger.info("Domain analysis cached successfully")
        except Exception as cache_error:
            logger.warning(f"Failed to cache domain analysis: {cache_error}")

        return response_data

    except Exception as e:
        logger.error(f"Error analyzing domains: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to analyze domains: {str(e)}") from e


@router.get(
    "/admin/trial/conversion-funnel",
    response_model=ConversionFunnelResponse,
    tags=["admin", "trial-analytics"],
)
async def get_conversion_funnel(
    admin_user: dict = Depends(require_admin),
):
    """
    Get conversion funnel data to understand at what point trials convert to paid

    **Purpose:** Analyze conversion patterns and optimize trial experience
    """
    try:
        client = get_supabase_client()

        # Fetch all trial API keys with conversion data
        # Paginate to get all records beyond 1000 limit
        all_trials = []
        page_size = 1000
        offset = 0

        while True:
            result = (
                client.table("api_keys_new")
                .select(
                    "id, is_trial, trial_converted, trial_used_requests, trial_used_tokens, created_at"
                )
                .eq("is_trial", True)
                .range(offset, offset + page_size - 1)
                .execute()
            )

            if not result.data or len(result.data) == 0:
                break

            all_trials.extend(result.data)

            if len(result.data) < page_size:
                break

            offset += page_size

        # Fetch conversion metrics
        all_conversion_metrics = []
        offset = 0

        while True:
            conversion_metrics_result = (
                client.table("trial_conversion_metrics")
                .select("requests_at_conversion, tokens_at_conversion")
                .range(offset, offset + page_size - 1)
                .execute()
            )

            if not conversion_metrics_result.data or len(conversion_metrics_result.data) == 0:
                break

            all_conversion_metrics.extend(conversion_metrics_result.data)

            if len(conversion_metrics_result.data) < page_size:
                break

            offset += page_size

        conversion_metrics = all_conversion_metrics

        # Initialize counters
        total_trials = len(all_trials)
        made_first_request = 0
        made_10_requests = 0
        made_50_requests = 0
        made_100_requests = 0
        converted_to_paid = 0

        converted_before_10 = 0
        converted_between_10_50 = 0
        converted_between_50_100 = 0
        converted_after_100 = 0

        for row in all_trials:
            requests = row.get("trial_used_requests", 0)

            if requests >= 1:
                made_first_request += 1
            if requests >= 10:
                made_10_requests += 1
            if requests >= 50:
                made_50_requests += 1
            if requests >= 100:
                made_100_requests += 1

            if row.get("trial_converted"):
                converted_to_paid += 1

        # Analyze conversion breakdown from conversion_metrics table
        requests_at_conversion_list = []
        tokens_at_conversion_list = []

        for metric in conversion_metrics:
            requests_at_conv = metric.get("requests_at_conversion", 0)
            tokens_at_conv = metric.get("tokens_at_conversion", 0)

            requests_at_conversion_list.append(requests_at_conv)
            tokens_at_conversion_list.append(tokens_at_conv)

            if requests_at_conv < 10:
                converted_before_10 += 1
            elif requests_at_conv < 50:
                converted_between_10_50 += 1
            elif requests_at_conv < 100:
                converted_between_50_100 += 1
            else:
                converted_after_100 += 1

        # Calculate averages and medians
        avg_requests = (
            sum(requests_at_conversion_list) / len(requests_at_conversion_list)
            if requests_at_conversion_list
            else 0
        )
        avg_tokens = (
            sum(tokens_at_conversion_list) / len(tokens_at_conversion_list)
            if tokens_at_conversion_list
            else 0
        )

        median_requests = (
            sorted(requests_at_conversion_list)[len(requests_at_conversion_list) // 2]
            if requests_at_conversion_list
            else 0
        )
        median_tokens = (
            sorted(tokens_at_conversion_list)[len(tokens_at_conversion_list) // 2]
            if tokens_at_conversion_list
            else 0
        )

        return ConversionFunnelResponse(
            success=True,
            funnel=ConversionFunnelData(
                total_trials_started=total_trials,
                completed_onboarding=total_trials,  # Assume all trials completed onboarding
                made_first_request=made_first_request,
                made_10_requests=made_10_requests,
                made_50_requests=made_50_requests,
                made_100_requests=made_100_requests,
                converted_to_paid=converted_to_paid,
                conversion_breakdown=ConversionBreakdown(
                    converted_before_10_requests=converted_before_10,
                    converted_between_10_50_requests=converted_between_10_50,
                    converted_between_50_100_requests=converted_between_50_100,
                    converted_after_100_requests=converted_after_100,
                ),
                avg_requests_at_conversion=round(avg_requests, 2),
                median_requests_at_conversion=median_requests,
                avg_tokens_at_conversion=round(avg_tokens, 2),
                median_tokens_at_conversion=median_tokens,
            ),
        )

    except Exception as e:
        logger.error(f"Error getting conversion funnel: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get conversion funnel: {str(e)}"
        ) from e


@router.get(
    "/admin/trial/ip-analysis", response_model=IPAnalysisResponse, tags=["admin", "trial-analytics"]
)
async def get_ip_analysis(
    min_accounts: int = Query(2, ge=1, description="Minimum accounts per IP to show"),
    admin_user: dict = Depends(require_admin),
):
    """
    Analyze trial signups by IP address to detect multiple accounts from same IP

    **Purpose:** Detect potential abuse from multiple accounts on same IP

    **Note:** IP tracking not yet implemented. Returns empty list for now.
    """
    try:
        # TODO: Implement IP tracking in user registration
        # For now, return empty response
        logger.warning("IP analysis requested but IP tracking not yet implemented")

        return IPAnalysisResponse(
            success=True,
            ips=[],
        )

    except Exception as e:
        logger.error(f"Error analyzing IPs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to analyze IPs: {str(e)}") from e


@router.post(
    "/admin/trial/save-conversion-metrics",
    response_model=SaveConversionMetricsResponse,
    tags=["admin", "trial-analytics"],
)
async def save_conversion_metrics(
    request: SaveConversionMetricsRequest,
    admin_user: dict = Depends(require_admin),
):
    """
    Save metrics when user converts from trial to paid

    **Purpose:** Record usage metrics at moment of conversion for analysis

    **Note:** This should be called automatically when a user upgrades from trial
    """
    try:
        client = get_supabase_client()

        # Insert conversion metrics
        result = (
            client.table("trial_conversion_metrics")
            .insert(
                {
                    "user_id": request.user_id,
                    "api_key_id": request.api_key_id,
                    "requests_at_conversion": request.requests_at_conversion,
                    "tokens_at_conversion": request.tokens_at_conversion,
                    "credits_used_at_conversion": request.credits_used_at_conversion,
                    "trial_days_used": request.trial_days_used,
                    "converted_plan": request.converted_plan,
                    "conversion_trigger": request.conversion_trigger,
                    "conversion_date": datetime.now(UTC).isoformat(),
                }
            )
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to save conversion metrics")

        logger.info(
            f"Saved conversion metrics for user {request.user_id}, api_key {request.api_key_id}"
        )

        return SaveConversionMetricsResponse(
            success=True,
            message="Conversion metrics saved successfully",
        )

    except Exception as e:
        logger.error(f"Error saving conversion metrics: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to save conversion metrics: {str(e)}"
        ) from e


@router.get(
    "/admin/trial/cohort-analysis",
    response_model=CohortAnalysisResponse,
    tags=["admin", "trial-analytics"],
)
async def get_cohort_analysis(
    period: Literal["week", "month"] = Query("week", description="Cohort period: week or month"),
    lookback: int = Query(12, ge=1, le=52, description="Number of periods to look back"),
    admin_user: dict = Depends(require_admin),
):
    """
    Provide week-over-week or month-over-month cohort conversion analysis

    **Purpose:** Track conversion rates and patterns across different signup cohorts
    """
    try:
        client = get_supabase_client()
        current_time = datetime.now(UTC)

        # Calculate cohort periods
        cohorts = []
        all_trials_count = 0
        all_converted_count = 0

        # Determine period length
        if period == "week":
            period_days = 7
            period_label = "Week"
        else:  # month
            period_days = 30
            period_label = "Month"

        # Fetch all trial API keys with their creation dates and conversion data
        # Paginate to get all records beyond 1000 limit
        all_trials_data = []
        page_size = 1000
        offset = 0

        while True:
            all_trials_result = (
                client.table("api_keys_new")
                .select(
                    "id, created_at, is_trial, trial_converted, trial_start_date, "
                    "trial_used_requests, trial_used_tokens"
                )
                .eq("is_trial", True)
                .range(offset, offset + page_size - 1)
                .execute()
            )

            if not all_trials_result.data or len(all_trials_result.data) == 0:
                break

            all_trials_data.extend(all_trials_result.data)

            if len(all_trials_result.data) < page_size:
                break

            offset += page_size

        # Fetch conversion metrics for days_to_convert calculation
        all_conversion_metrics = []
        offset = 0

        while True:
            conversion_metrics_result = (
                client.table("trial_conversion_metrics")
                .select("api_key_id, conversion_date, trial_days_used")
                .range(offset, offset + page_size - 1)
                .execute()
            )

            if not conversion_metrics_result.data or len(conversion_metrics_result.data) == 0:
                break

            all_conversion_metrics.extend(conversion_metrics_result.data)

            if len(conversion_metrics_result.data) < page_size:
                break

            offset += page_size

        conversion_metrics_map = {}
        for metric in all_conversion_metrics:
            conversion_metrics_map[metric["api_key_id"]] = metric

        # Group trials by cohort
        for cohort_index in range(lookback):
            # Calculate cohort period (going backwards from current time)
            cohort_end = current_time - timedelta(days=cohort_index * period_days)
            cohort_start = cohort_end - timedelta(days=period_days)

            # Format dates
            cohort_start_str = cohort_start.strftime("%Y-%m-%d")
            cohort_end_str = cohort_end.strftime("%Y-%m-%d")

            # Create label
            if period == "week":
                cohort_label = f"{period_label} {lookback - cohort_index} ({cohort_start.strftime('%b %d')}-{cohort_end.strftime('%b %d')})"
            else:
                cohort_label = (
                    f"{period_label} {lookback - cohort_index} ({cohort_start.strftime('%b %Y')})"
                )

            # Filter trials in this cohort
            cohort_trials = []
            for trial in all_trials_data:
                created_at_str = trial.get("created_at")
                if not created_at_str:
                    continue

                trial_created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

                # Check if trial was created in this cohort period
                if cohort_start <= trial_created < cohort_end:
                    cohort_trials.append(trial)

            # Calculate cohort metrics
            total_trials_in_cohort = len(cohort_trials)
            converted_trials_in_cohort = sum(
                1 for t in cohort_trials if t.get("trial_converted", False)
            )
            conversion_rate = (
                (converted_trials_in_cohort / total_trials_in_cohort * 100)
                if total_trials_in_cohort > 0
                else 0
            )

            # Calculate average days to convert for this cohort
            days_to_convert_list = []
            for trial in cohort_trials:
                if trial.get("trial_converted") and trial["id"] in conversion_metrics_map:
                    days_to_convert_list.append(
                        conversion_metrics_map[trial["id"]].get("trial_days_used", 0)
                    )

            avg_days_to_convert = (
                sum(days_to_convert_list) / len(days_to_convert_list) if days_to_convert_list else 0
            )

            # Calculate average usage at signup
            avg_requests = (
                sum(t.get("trial_used_requests", 0) for t in cohort_trials) / total_trials_in_cohort
                if total_trials_in_cohort > 0
                else 0
            )
            avg_tokens = (
                sum(t.get("trial_used_tokens", 0) for t in cohort_trials) / total_trials_in_cohort
                if total_trials_in_cohort > 0
                else 0
            )

            # Track totals for summary
            all_trials_count += total_trials_in_cohort
            all_converted_count += converted_trials_in_cohort

            cohorts.append(
                CohortData(
                    cohort_label=cohort_label,
                    cohort_start_date=cohort_start_str,
                    cohort_end_date=cohort_end_str,
                    total_trials=total_trials_in_cohort,
                    converted_trials=converted_trials_in_cohort,
                    conversion_rate=round(conversion_rate, 2),
                    avg_days_to_convert=round(avg_days_to_convert, 1),
                    avg_requests_at_signup=round(avg_requests, 1),
                    avg_tokens_at_signup=round(avg_tokens, 1),
                )
            )

        # Reverse to show oldest first
        cohorts.reverse()

        # Calculate summary statistics
        overall_conversion_rate = (
            (all_converted_count / all_trials_count * 100) if all_trials_count > 0 else 0
        )

        # Find best and worst cohorts (with at least 5 trials to avoid outliers)
        cohorts_with_trials = [c for c in cohorts if c.total_trials >= 5]

        if cohorts_with_trials:
            best_cohort = max(cohorts_with_trials, key=lambda c: c.conversion_rate)
            worst_cohort = min(cohorts_with_trials, key=lambda c: c.conversion_rate)
        else:
            # Fallback if no cohorts have 5+ trials
            best_cohort = (
                cohorts[0]
                if cohorts
                else CohortData(
                    cohort_label="N/A",
                    cohort_start_date="",
                    cohort_end_date="",
                    total_trials=0,
                    converted_trials=0,
                    conversion_rate=0,
                    avg_days_to_convert=0,
                    avg_requests_at_signup=0,
                    avg_tokens_at_signup=0,
                )
            )
            worst_cohort = best_cohort

        summary = CohortSummary(
            total_cohorts=len(cohorts),
            overall_conversion_rate=round(overall_conversion_rate, 2),
            best_cohort=BestWorstCohort(
                label=best_cohort.cohort_label,
                conversion_rate=best_cohort.conversion_rate,
            ),
            worst_cohort=BestWorstCohort(
                label=worst_cohort.cohort_label,
                conversion_rate=worst_cohort.conversion_rate,
            ),
        )

        return CohortAnalysisResponse(
            success=True,
            cohorts=cohorts,
            summary=summary,
        )

    except Exception as e:
        logger.error(f"Error getting cohort analysis: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get cohort analysis: {str(e)}"
        ) from e
