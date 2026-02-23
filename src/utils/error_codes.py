"""
Error Code Enumerations

Comprehensive error code definitions with status code mappings and categorization.
Provides standardized error codes across the entire API.

Usage:
    from src.utils.error_codes import ErrorCode, get_status_code, get_error_category

    code = ErrorCode.MODEL_NOT_FOUND
    status = get_status_code(code)  # Returns 404
    category = get_error_category(code)  # Returns ErrorCategory.MODEL_ERRORS
"""

from enum import Enum


class ErrorCategory(str, Enum):  # noqa: UP042
    """High-level error categories for organization."""

    MODEL_ERRORS = "model_errors"
    VALIDATION_ERRORS = "validation_errors"
    AUTHENTICATION_ERRORS = "authentication_errors"
    AUTHORIZATION_ERRORS = "authorization_errors"
    PAYMENT_ERRORS = "payment_errors"
    RATE_LIMIT_ERRORS = "rate_limit_errors"
    PROVIDER_ERRORS = "provider_errors"
    SERVICE_ERRORS = "service_errors"


class ErrorCode(str, Enum):  # noqa: UP042
    """
    Standardized error codes for all API errors.

    Error codes follow the pattern: CATEGORY_SPECIFIC_ERROR
    """

    # ==================== Model Errors (404xx) ====================
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_DEPRECATED = "MODEL_DEPRECATED"
    INVALID_MODEL_FORMAT = "INVALID_MODEL_FORMAT"
    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    MODEL_REGION_RESTRICTED = "MODEL_REGION_RESTRICTED"

    # ==================== Request Validation Errors (400xx) ====================
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_PARAMETER_TYPE = "INVALID_PARAMETER_TYPE"
    PARAMETER_OUT_OF_RANGE = "PARAMETER_OUT_OF_RANGE"
    INVALID_MESSAGE_FORMAT = "INVALID_MESSAGE_FORMAT"
    EMPTY_MESSAGES_ARRAY = "EMPTY_MESSAGES_ARRAY"
    INVALID_ROLE = "INVALID_ROLE"
    MAX_TOKENS_EXCEEDED = "MAX_TOKENS_EXCEEDED"
    CONTEXT_LENGTH_EXCEEDED = "CONTEXT_LENGTH_EXCEEDED"
    INVALID_TEMPERATURE = "INVALID_TEMPERATURE"
    INVALID_STREAM_PARAMETER = "INVALID_STREAM_PARAMETER"
    INVALID_JSON = "INVALID_JSON"
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    UNSUPPORTED_PARAMETER = "UNSUPPORTED_PARAMETER"
    INVALID_CONTENT_TYPE = "INVALID_CONTENT_TYPE"
    INVALID_REQUEST_BODY = "INVALID_REQUEST_BODY"

    # ==================== Authentication Errors (401xx) ====================
    INVALID_API_KEY = "INVALID_API_KEY"
    API_KEY_EXPIRED = "API_KEY_EXPIRED"
    API_KEY_REVOKED = "API_KEY_REVOKED"
    API_KEY_MISSING = "API_KEY_MISSING"
    API_KEY_MALFORMED = "API_KEY_MALFORMED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"

    # ==================== Authorization Errors (403xx) ====================
    IP_RESTRICTED = "IP_RESTRICTED"
    DOMAIN_RESTRICTED = "DOMAIN_RESTRICTED"
    TRIAL_EXPIRED = "TRIAL_EXPIRED"
    PLAN_LIMIT_REACHED = "PLAN_LIMIT_REACHED"
    INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS"
    ACCESS_DENIED = "ACCESS_DENIED"
    FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"

    # ==================== Payment & Credit Errors (402xx) ====================
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    CREDIT_BELOW_MINIMUM = "CREDIT_BELOW_MINIMUM"
    PAYMENT_METHOD_REQUIRED = "PAYMENT_METHOD_REQUIRED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    BILLING_ERROR = "BILLING_ERROR"

    # ==================== Rate Limiting Errors (429xx) ====================
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    TOKEN_RATE_LIMIT = "TOKEN_RATE_LIMIT"
    CONCURRENT_REQUEST_LIMIT = "CONCURRENT_REQUEST_LIMIT"
    DAILY_QUOTA_EXCEEDED = "DAILY_QUOTA_EXCEEDED"
    MONTHLY_QUOTA_EXCEEDED = "MONTHLY_QUOTA_EXCEEDED"
    HOURLY_QUOTA_EXCEEDED = "HOURLY_QUOTA_EXCEEDED"

    # ==================== Provider Errors (502xx, 503xx, 504xx) ====================
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_AUTHENTICATION_ERROR = "PROVIDER_AUTHENTICATION_ERROR"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    ALL_PROVIDERS_FAILED = "ALL_PROVIDERS_FAILED"

    # ==================== Service Errors (500xx, 503xx) ====================
    INTERNAL_ERROR = "INTERNAL_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    MAINTENANCE_MODE = "MAINTENANCE_MODE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"

    # ==================== Resource Errors (404xx) ====================
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    ENDPOINT_NOT_FOUND = "ENDPOINT_NOT_FOUND"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"


# Error code to HTTP status code mapping
ERROR_STATUS_CODES: dict[ErrorCode, int] = {
    # Model errors -> 404
    ErrorCode.MODEL_NOT_FOUND: 404,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.MODEL_DEPRECATED: 410,
    ErrorCode.INVALID_MODEL_FORMAT: 400,
    ErrorCode.PROVIDER_MISMATCH: 400,
    ErrorCode.MODEL_REGION_RESTRICTED: 403,

    # Validation errors -> 400
    ErrorCode.MISSING_REQUIRED_FIELD: 400,
    ErrorCode.INVALID_PARAMETER_TYPE: 400,
    ErrorCode.PARAMETER_OUT_OF_RANGE: 400,
    ErrorCode.INVALID_MESSAGE_FORMAT: 400,
    ErrorCode.EMPTY_MESSAGES_ARRAY: 400,
    ErrorCode.INVALID_ROLE: 400,
    ErrorCode.MAX_TOKENS_EXCEEDED: 400,
    ErrorCode.CONTEXT_LENGTH_EXCEEDED: 400,
    ErrorCode.INVALID_TEMPERATURE: 400,
    ErrorCode.INVALID_STREAM_PARAMETER: 400,
    ErrorCode.INVALID_JSON: 400,
    ErrorCode.MALFORMED_REQUEST: 400,
    ErrorCode.UNSUPPORTED_PARAMETER: 400,
    ErrorCode.INVALID_CONTENT_TYPE: 400,
    ErrorCode.INVALID_REQUEST_BODY: 400,

    # Authentication errors -> 401
    ErrorCode.INVALID_API_KEY: 401,
    ErrorCode.API_KEY_EXPIRED: 401,
    ErrorCode.API_KEY_REVOKED: 401,
    ErrorCode.API_KEY_MISSING: 401,
    ErrorCode.API_KEY_MALFORMED: 401,
    ErrorCode.AUTHENTICATION_REQUIRED: 401,

    # Authorization errors -> 403
    ErrorCode.IP_RESTRICTED: 403,
    ErrorCode.DOMAIN_RESTRICTED: 403,
    ErrorCode.TRIAL_EXPIRED: 403,
    ErrorCode.PLAN_LIMIT_REACHED: 403,
    ErrorCode.INSUFFICIENT_PERMISSIONS: 403,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.FEATURE_NOT_AVAILABLE: 403,

    # Payment errors -> 402
    ErrorCode.INSUFFICIENT_CREDITS: 402,
    ErrorCode.CREDIT_BELOW_MINIMUM: 402,
    ErrorCode.PAYMENT_METHOD_REQUIRED: 402,
    ErrorCode.PAYMENT_FAILED: 402,
    ErrorCode.INVOICE_OVERDUE: 402,
    ErrorCode.BILLING_ERROR: 402,

    # Rate limiting errors -> 429
    ErrorCode.RATE_LIMIT_EXCEEDED: 429,
    ErrorCode.TOKEN_RATE_LIMIT: 429,
    ErrorCode.CONCURRENT_REQUEST_LIMIT: 429,
    ErrorCode.DAILY_QUOTA_EXCEEDED: 429,
    ErrorCode.MONTHLY_QUOTA_EXCEEDED: 429,
    ErrorCode.HOURLY_QUOTA_EXCEEDED: 429,

    # Provider errors -> 502/503/504
    ErrorCode.PROVIDER_ERROR: 502,
    ErrorCode.PROVIDER_TIMEOUT: 504,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_RATE_LIMITED: 429,
    ErrorCode.PROVIDER_AUTHENTICATION_ERROR: 502,
    ErrorCode.PROVIDER_INVALID_RESPONSE: 502,
    ErrorCode.ALL_PROVIDERS_FAILED: 502,

    # Service errors -> 500/503
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.DATABASE_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.MAINTENANCE_MODE: 503,
    ErrorCode.CONFIGURATION_ERROR: 500,
    ErrorCode.UNEXPECTED_ERROR: 500,

    # Resource errors -> 404/410
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.ENDPOINT_NOT_FOUND: 404,
    ErrorCode.USER_NOT_FOUND: 404,
    ErrorCode.SESSION_NOT_FOUND: 404,
}


# Error code to category mapping
ERROR_CATEGORIES: dict[ErrorCode, ErrorCategory] = {
    # Model errors
    ErrorCode.MODEL_NOT_FOUND: ErrorCategory.MODEL_ERRORS,
    ErrorCode.MODEL_UNAVAILABLE: ErrorCategory.MODEL_ERRORS,
    ErrorCode.MODEL_DEPRECATED: ErrorCategory.MODEL_ERRORS,
    ErrorCode.INVALID_MODEL_FORMAT: ErrorCategory.MODEL_ERRORS,
    ErrorCode.PROVIDER_MISMATCH: ErrorCategory.MODEL_ERRORS,
    ErrorCode.MODEL_REGION_RESTRICTED: ErrorCategory.MODEL_ERRORS,

    # Validation errors
    ErrorCode.MISSING_REQUIRED_FIELD: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_PARAMETER_TYPE: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.PARAMETER_OUT_OF_RANGE: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_MESSAGE_FORMAT: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.EMPTY_MESSAGES_ARRAY: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_ROLE: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.MAX_TOKENS_EXCEEDED: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.CONTEXT_LENGTH_EXCEEDED: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_TEMPERATURE: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_STREAM_PARAMETER: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_JSON: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.MALFORMED_REQUEST: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.UNSUPPORTED_PARAMETER: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_CONTENT_TYPE: ErrorCategory.VALIDATION_ERRORS,
    ErrorCode.INVALID_REQUEST_BODY: ErrorCategory.VALIDATION_ERRORS,

    # Authentication errors
    ErrorCode.INVALID_API_KEY: ErrorCategory.AUTHENTICATION_ERRORS,
    ErrorCode.API_KEY_EXPIRED: ErrorCategory.AUTHENTICATION_ERRORS,
    ErrorCode.API_KEY_REVOKED: ErrorCategory.AUTHENTICATION_ERRORS,
    ErrorCode.API_KEY_MISSING: ErrorCategory.AUTHENTICATION_ERRORS,
    ErrorCode.API_KEY_MALFORMED: ErrorCategory.AUTHENTICATION_ERRORS,
    ErrorCode.AUTHENTICATION_REQUIRED: ErrorCategory.AUTHENTICATION_ERRORS,

    # Authorization errors
    ErrorCode.IP_RESTRICTED: ErrorCategory.AUTHORIZATION_ERRORS,
    ErrorCode.DOMAIN_RESTRICTED: ErrorCategory.AUTHORIZATION_ERRORS,
    ErrorCode.TRIAL_EXPIRED: ErrorCategory.AUTHORIZATION_ERRORS,
    ErrorCode.PLAN_LIMIT_REACHED: ErrorCategory.AUTHORIZATION_ERRORS,
    ErrorCode.INSUFFICIENT_PERMISSIONS: ErrorCategory.AUTHORIZATION_ERRORS,
    ErrorCode.ACCESS_DENIED: ErrorCategory.AUTHORIZATION_ERRORS,
    ErrorCode.FEATURE_NOT_AVAILABLE: ErrorCategory.AUTHORIZATION_ERRORS,

    # Payment errors
    ErrorCode.INSUFFICIENT_CREDITS: ErrorCategory.PAYMENT_ERRORS,
    ErrorCode.CREDIT_BELOW_MINIMUM: ErrorCategory.PAYMENT_ERRORS,
    ErrorCode.PAYMENT_METHOD_REQUIRED: ErrorCategory.PAYMENT_ERRORS,
    ErrorCode.PAYMENT_FAILED: ErrorCategory.PAYMENT_ERRORS,
    ErrorCode.INVOICE_OVERDUE: ErrorCategory.PAYMENT_ERRORS,
    ErrorCode.BILLING_ERROR: ErrorCategory.PAYMENT_ERRORS,

    # Rate limit errors
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorCategory.RATE_LIMIT_ERRORS,
    ErrorCode.TOKEN_RATE_LIMIT: ErrorCategory.RATE_LIMIT_ERRORS,
    ErrorCode.CONCURRENT_REQUEST_LIMIT: ErrorCategory.RATE_LIMIT_ERRORS,
    ErrorCode.DAILY_QUOTA_EXCEEDED: ErrorCategory.RATE_LIMIT_ERRORS,
    ErrorCode.MONTHLY_QUOTA_EXCEEDED: ErrorCategory.RATE_LIMIT_ERRORS,
    ErrorCode.HOURLY_QUOTA_EXCEEDED: ErrorCategory.RATE_LIMIT_ERRORS,

    # Provider errors
    ErrorCode.PROVIDER_ERROR: ErrorCategory.PROVIDER_ERRORS,
    ErrorCode.PROVIDER_TIMEOUT: ErrorCategory.PROVIDER_ERRORS,
    ErrorCode.PROVIDER_UNAVAILABLE: ErrorCategory.PROVIDER_ERRORS,
    ErrorCode.PROVIDER_RATE_LIMITED: ErrorCategory.PROVIDER_ERRORS,
    ErrorCode.PROVIDER_AUTHENTICATION_ERROR: ErrorCategory.PROVIDER_ERRORS,
    ErrorCode.PROVIDER_INVALID_RESPONSE: ErrorCategory.PROVIDER_ERRORS,
    ErrorCode.ALL_PROVIDERS_FAILED: ErrorCategory.PROVIDER_ERRORS,

    # Service errors
    ErrorCode.INTERNAL_ERROR: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.DATABASE_ERROR: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.SERVICE_UNAVAILABLE: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.MAINTENANCE_MODE: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.CONFIGURATION_ERROR: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.UNEXPECTED_ERROR: ErrorCategory.SERVICE_ERRORS,

    # Resource errors (could be model or service category)
    ErrorCode.RESOURCE_NOT_FOUND: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.ENDPOINT_NOT_FOUND: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.USER_NOT_FOUND: ErrorCategory.SERVICE_ERRORS,
    ErrorCode.SESSION_NOT_FOUND: ErrorCategory.SERVICE_ERRORS,
}


def get_status_code(error_code: ErrorCode) -> int:
    """
    Get the HTTP status code for an error code.

    Args:
        error_code: The error code

    Returns:
        HTTP status code (400-599)
    """
    return ERROR_STATUS_CODES.get(error_code, 500)


def get_error_category(error_code: ErrorCode) -> ErrorCategory:
    """
    Get the category for an error code.

    Args:
        error_code: The error code

    Returns:
        ErrorCategory enum value
    """
    return ERROR_CATEGORIES.get(error_code, ErrorCategory.SERVICE_ERRORS)


def get_error_type(error_code: ErrorCode) -> str:
    """
    Convert error code to error type string (snake_case).

    Args:
        error_code: The error code (e.g., ErrorCode.MODEL_NOT_FOUND)

    Returns:
        Error type string (e.g., "model_not_found")
    """
    return error_code.value.lower()


def is_client_error(error_code: ErrorCode) -> bool:
    """
    Check if error is a client error (4xx status code).

    Args:
        error_code: The error code

    Returns:
        True if client error, False otherwise
    """
    status = get_status_code(error_code)
    return 400 <= status < 500


def is_server_error(error_code: ErrorCode) -> bool:
    """
    Check if error is a server error (5xx status code).

    Args:
        error_code: The error code

    Returns:
        True if server error, False otherwise
    """
    status = get_status_code(error_code)
    return 500 <= status < 600


def is_retryable_error(error_code: ErrorCode) -> bool:
    """
    Check if error is potentially retryable.

    Args:
        error_code: The error code

    Returns:
        True if error might be resolved by retrying
    """
    retryable_codes = {
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.PROVIDER_RATE_LIMITED,
        ErrorCode.SERVICE_UNAVAILABLE,
        ErrorCode.RATE_LIMIT_EXCEEDED,
        ErrorCode.TOKEN_RATE_LIMIT,
        ErrorCode.CONCURRENT_REQUEST_LIMIT,
    }
    return error_code in retryable_codes
