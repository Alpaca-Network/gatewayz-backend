"""Pydantic schemas for trial analytics endpoints"""
from datetime import datetime

from pydantic import BaseModel, Field

# ===========================
# Trial User Schemas
# ===========================

class TrialUser(BaseModel):
    """Individual trial user data"""
    user_id: int
    email: str
    email_domain: str
    api_key_id: int
    api_key_preview: str
    is_trial: bool
    trial_start_date: datetime | None
    trial_end_date: datetime | None
    trial_status: str  # "active", "expired", "converted"
    trial_days_remaining: int | None
    trial_used_tokens: int = 0
    trial_max_tokens: int = 500000
    trial_token_utilization: float = 0.0
    trial_used_requests: int = 0
    trial_max_requests: int = 1000
    trial_request_utilization: float = 0.0
    trial_used_credits: float = 0.0
    trial_allocated_credits: float = 10.0
    trial_credit_utilization: float = 0.0
    trial_converted: bool = False
    conversion_date: datetime | None = None
    requests_at_conversion: int | None = None
    tokens_at_conversion: int | None = None
    created_at: datetime
    signup_ip: str | None = None
    last_request_at: datetime | None = None


class TrialUsersPagination(BaseModel):
    """Pagination metadata for trial users"""
    total: int
    limit: int
    offset: int
    has_more: bool


class TrialUsersResponse(BaseModel):
    """Response for GET /admin/trial/users"""
    success: bool = True
    users: list[TrialUser]
    pagination: TrialUsersPagination


# ===========================
# Domain Analysis Schemas
# ===========================

class DomainAnalysis(BaseModel):
    """Analysis data for a specific email domain"""
    domain: str
    total_users: int
    active_trials: int
    expired_trials: int
    converted_trials: int
    conversion_rate: float
    total_requests: int
    total_tokens: int
    total_credits_used: float
    avg_requests_per_user: float
    avg_tokens_per_user: float
    abuse_score: float
    flagged: bool


class DomainAnalysisResponse(BaseModel):
    """Response for GET /admin/trial/domain-analysis"""
    success: bool = True
    domains: list[DomainAnalysis]
    suspicious_domains: list[str]


# ===========================
# Conversion Funnel Schemas
# ===========================

class ConversionBreakdown(BaseModel):
    """Breakdown of when users converted"""
    converted_before_10_requests: int
    converted_between_10_50_requests: int
    converted_between_50_100_requests: int
    converted_after_100_requests: int


class ConversionFunnelData(BaseModel):
    """Conversion funnel statistics"""
    total_trials_started: int
    completed_onboarding: int
    made_first_request: int
    made_10_requests: int
    made_50_requests: int
    made_100_requests: int
    converted_to_paid: int
    conversion_breakdown: ConversionBreakdown
    avg_requests_at_conversion: float
    median_requests_at_conversion: int
    avg_tokens_at_conversion: float
    median_tokens_at_conversion: int


class ConversionFunnelResponse(BaseModel):
    """Response for GET /admin/trial/conversion-funnel"""
    success: bool = True
    funnel: ConversionFunnelData


# ===========================
# IP Analysis Schemas
# ===========================

class IPAnalysis(BaseModel):
    """Analysis data for a specific IP address"""
    ip_address: str
    total_accounts: int
    active_trials: int
    converted_accounts: int
    total_requests: int
    total_tokens: int
    flagged: bool
    reason: str | None = None


class IPAnalysisResponse(BaseModel):
    """Response for GET /admin/trial/ip-analysis"""
    success: bool = True
    ips: list[IPAnalysis]


# ===========================
# Save Conversion Metrics Schemas
# ===========================

class SaveConversionMetricsRequest(BaseModel):
    """Request body for POST /admin/trial/save-conversion-metrics"""
    user_id: int = Field(..., description="User ID who converted")
    api_key_id: int = Field(..., description="API key ID that converted")
    requests_at_conversion: int = Field(..., ge=0, description="Number of requests at conversion")
    tokens_at_conversion: int = Field(..., ge=0, description="Number of tokens used at conversion")
    credits_used_at_conversion: float = Field(..., ge=0, description="Credits used at conversion")
    trial_days_used: int = Field(..., ge=0, description="Number of days into trial when converted")
    converted_plan: str = Field(..., description="Plan name user converted to")
    conversion_trigger: str = Field(
        default="manual_upgrade",
        description="What triggered the conversion (manual_upgrade, auto_upgrade, etc)"
    )


class SaveConversionMetricsResponse(BaseModel):
    """Response for POST /admin/trial/save-conversion-metrics"""
    success: bool = True
    message: str = "Conversion metrics saved successfully"


# ===========================
# Cohort Analysis Schemas
# ===========================

class CohortData(BaseModel):
    """Data for a single cohort period"""
    cohort_label: str
    cohort_start_date: str
    cohort_end_date: str
    total_trials: int
    converted_trials: int
    conversion_rate: float
    avg_days_to_convert: float
    avg_requests_at_signup: float
    avg_tokens_at_signup: float


class BestWorstCohort(BaseModel):
    """Best or worst performing cohort"""
    label: str
    conversion_rate: float


class CohortSummary(BaseModel):
    """Summary statistics across all cohorts"""
    total_cohorts: int
    overall_conversion_rate: float
    best_cohort: BestWorstCohort
    worst_cohort: BestWorstCohort


class CohortAnalysisResponse(BaseModel):
    """Response for GET /admin/trial/cohort-analysis"""
    success: bool = True
    cohorts: list[CohortData]
    summary: CohortSummary
