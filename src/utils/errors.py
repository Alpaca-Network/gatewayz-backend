"""Consolidated error handling for the Gatewayz gateway.

This module is the single home for the error cluster, merged verbatim (MVP
Task 15) from the former ``error_codes.py``, ``error_messages.py``,
``exceptions.py`` and ``error_factory.py``. Logic was moved, not rewritten —
the provider-error classification, user-facing message table and status-code
mapping (encoding regressions #2144-#2147, #2160) are preserved exactly.

Public surface (re-exported verbatim for importers):
  - ``APIExceptions`` (FastAPI HTTPException builders)
  - ``DetailedErrorFactory`` (provider-error -> client-status mapping)
  - ``PROVIDER_CAPACITY_MESSAGE``, ``is_provider_budget_error``,
    ``sanitize_provider_error_for_user`` (user-facing sanitization)
  - ``ErrorCode``/``ErrorCategory`` enums and their helper functions
"""

# ============================================================================
# Merged from error_codes.py
# ============================================================================

"""
Error Code Enumerations

Comprehensive error code definitions with status code mappings and categorization.
Provides standardized error codes across the entire API.

Usage:
    from src.utils.errors import ErrorCode, get_status_code, get_error_category

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


# ============================================================================
# Merged from error_messages.py
# ============================================================================

"""
Error Message Templates

Pre-defined, user-friendly error messages and suggestions for each error type.
Provides consistent, helpful error messages across the API.

Usage:
    from src.utils.errors import get_error_message, get_suggestions

    message = get_error_message(ErrorCode.MODEL_NOT_FOUND, model_id="gpt-5")
    suggestions = get_suggestions(ErrorCode.MODEL_NOT_FOUND)
"""

import re

# User-facing, friendly message for provider account/key budget exhaustion (e.g. an
# OpenRouter key hitting its weekly spend limit). Never expose the upstream key/URL.
PROVIDER_CAPACITY_MESSAGE = (
    "This model is temporarily unavailable due to a capacity limit on our side. "
    "Please try a different model or try again shortly."
)

# Patterns that indicate an upstream provider ran out of budget/credits rather than a
# problem with the user's own request. Matched case-insensitively against the raw error.
_PROVIDER_BUDGET_PATTERNS = (
    "requires more credits",
    "can only afford",
    "adjust the key",
    "weekly limit",
    "insufficient_quota",
    "payment required",
)

_URL_RE = re.compile(r"https?://\S+")
# Long hex tokens (32+ chars) are almost always secrets/key hashes (e.g. an OpenRouter
# key id leaked in an error URL). Strip them from anything shown to end users.
_HEX_SECRET_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def is_provider_budget_error(raw_error: str | None) -> bool:
    """True if the raw provider error indicates the provider account/key is out of budget."""
    if not raw_error:
        return False
    lowered = str(raw_error).lower()
    if "error code: 402" in lowered or '"code": 402' in lowered or "'code': 402" in lowered:
        return True
    return any(pattern in lowered for pattern in _PROVIDER_BUDGET_PATTERNS)


def sanitize_provider_error_for_user(raw_error: str | None, max_length: int = 200) -> str:
    """Strip URLs and secret-like tokens from a provider error before showing it to a user.

    Upstream providers (notably OpenRouter) embed dashboard URLs containing the API key id
    directly in their error text. Passing that through leaks a credential-ish identifier to
    end users, so we remove URLs and long hex tokens and truncate the result.
    """
    if not raw_error:
        return ""
    cleaned = _URL_RE.sub("[link removed]", str(raw_error))
    cleaned = _HEX_SECRET_RE.sub("[redacted]", cleaned)
    cleaned = cleaned.replace("\n", " ").replace("\r", " ").strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip() + "…"
    return cleaned


# Error message templates with placeholders
ERROR_MESSAGES: dict[ErrorCode, str] = {
    # Model errors
    ErrorCode.MODEL_NOT_FOUND: "Model '{model_id}' not found",
    ErrorCode.MODEL_UNAVAILABLE: "Model '{model_id}' is temporarily unavailable",
    ErrorCode.MODEL_DEPRECATED: "Model '{model_id}' has been deprecated",
    ErrorCode.INVALID_MODEL_FORMAT: "Invalid model ID format: '{model_id}'",
    ErrorCode.PROVIDER_MISMATCH: "Model '{model_id}' is not available on provider '{provider}'",
    ErrorCode.MODEL_REGION_RESTRICTED: "Model '{model_id}' is not available in your region",
    # Validation errors
    ErrorCode.MISSING_REQUIRED_FIELD: "Missing required field: '{field_name}'",
    ErrorCode.INVALID_PARAMETER_TYPE: "Invalid type for parameter '{parameter_name}': expected {expected_type}, got {actual_type}",
    ErrorCode.PARAMETER_OUT_OF_RANGE: "Parameter '{parameter_name}' value {value} is out of valid range [{min_value}, {max_value}]",
    ErrorCode.INVALID_MESSAGE_FORMAT: "Invalid message format: {reason}",
    ErrorCode.EMPTY_MESSAGES_ARRAY: "Messages array cannot be empty",
    ErrorCode.INVALID_ROLE: "Invalid message role: '{role}'. Must be one of: {allowed_roles}",
    ErrorCode.MAX_TOKENS_EXCEEDED: "Requested max_tokens ({requested}) exceeds model limit ({limit})",
    ErrorCode.CONTEXT_LENGTH_EXCEEDED: "Input length ({input_tokens} tokens) exceeds model's maximum context length ({max_context})",
    ErrorCode.INVALID_TEMPERATURE: "Temperature must be between {min_value} and {max_value}, got {value}",
    ErrorCode.INVALID_STREAM_PARAMETER: "Invalid value for 'stream' parameter: expected boolean",
    ErrorCode.INVALID_JSON: "Invalid JSON in request body",
    ErrorCode.MALFORMED_REQUEST: "Malformed request: {reason}",
    ErrorCode.UNSUPPORTED_PARAMETER: "Parameter '{parameter_name}' is not supported for this endpoint",
    ErrorCode.INVALID_CONTENT_TYPE: "Invalid Content-Type: expected application/json",
    ErrorCode.INVALID_REQUEST_BODY: "Invalid request body: {reason}",
    # Authentication errors
    ErrorCode.INVALID_API_KEY: "Invalid API key",
    ErrorCode.API_KEY_EXPIRED: "API key has expired",
    ErrorCode.API_KEY_REVOKED: "API key has been revoked",
    ErrorCode.API_KEY_MISSING: "API key is required. Please provide an API key in the Authorization header",
    ErrorCode.API_KEY_MALFORMED: "API key format is invalid",
    ErrorCode.AUTHENTICATION_REQUIRED: "Authentication is required for this endpoint",
    # Authorization errors
    ErrorCode.IP_RESTRICTED: "Access denied: IP address {ip_address} is not in the allowed list",
    ErrorCode.DOMAIN_RESTRICTED: "Access denied: Domain is not in the allowed list",
    ErrorCode.TRIAL_EXPIRED: "Free trial has expired",
    ErrorCode.PLAN_LIMIT_REACHED: "Plan limit exceeded: {reason}",
    ErrorCode.INSUFFICIENT_PERMISSIONS: "Insufficient permissions to access this resource",
    ErrorCode.ACCESS_DENIED: "Access denied",
    ErrorCode.FEATURE_NOT_AVAILABLE: "This feature is not available on your current plan",
    # Payment & credit errors
    ErrorCode.INSUFFICIENT_CREDITS: "Insufficient credits. Please add credits to continue.",
    ErrorCode.CREDIT_BELOW_MINIMUM: "Credit balance is below the minimum required amount. Please add credits to continue.",
    ErrorCode.PAYMENT_METHOD_REQUIRED: "Payment method required. Please add a payment method to your account",
    ErrorCode.PAYMENT_FAILED: "Payment processing failed: {reason}",
    ErrorCode.INVOICE_OVERDUE: "Your account has overdue invoices. Please settle your balance to continue",
    ErrorCode.BILLING_ERROR: "Billing error: {reason}",
    # Rate limiting errors
    ErrorCode.RATE_LIMIT_EXCEEDED: "Rate limit exceeded: {limit_type}",
    ErrorCode.TOKEN_RATE_LIMIT: "Token rate limit exceeded. Please slow down your requests",
    ErrorCode.CONCURRENT_REQUEST_LIMIT: "Too many concurrent requests. Maximum: {max_concurrent}",
    ErrorCode.DAILY_QUOTA_EXCEEDED: "Daily quota exceeded. Limit: {limit}, Used: {used}",
    ErrorCode.MONTHLY_QUOTA_EXCEEDED: "Monthly quota exceeded. Limit: {limit}, Used: {used}",
    ErrorCode.HOURLY_QUOTA_EXCEEDED: "Hourly quota exceeded. Limit: {limit}, Used: {used}",
    # Provider errors
    ErrorCode.PROVIDER_ERROR: "Provider '{provider}' returned an error for model '{model_id}': {error_message}",
    ErrorCode.PROVIDER_TIMEOUT: "Request to provider '{provider}' timed out",
    ErrorCode.PROVIDER_UNAVAILABLE: "Provider '{provider}' is temporarily unavailable",
    ErrorCode.PROVIDER_RATE_LIMITED: "Provider '{provider}' rate limit exceeded",
    ErrorCode.PROVIDER_AUTHENTICATION_ERROR: "Provider '{provider}' authentication failed",
    ErrorCode.PROVIDER_INVALID_RESPONSE: "Provider '{provider}' returned an invalid response",
    ErrorCode.ALL_PROVIDERS_FAILED: "All providers failed for model '{model_id}'. Please try again later",
    # Service errors
    ErrorCode.INTERNAL_ERROR: "Internal server error",
    ErrorCode.DATABASE_ERROR: "Database error occurred",
    ErrorCode.SERVICE_UNAVAILABLE: "Service is temporarily unavailable",
    ErrorCode.MAINTENANCE_MODE: "Service is currently in maintenance mode",
    ErrorCode.CONFIGURATION_ERROR: "Service configuration error",
    ErrorCode.UNEXPECTED_ERROR: "An unexpected error occurred",
    # Resource errors
    ErrorCode.RESOURCE_NOT_FOUND: "Resource not found: {resource_type} '{resource_id}'",
    ErrorCode.ENDPOINT_NOT_FOUND: "Endpoint not found: {method} {path}",
    ErrorCode.USER_NOT_FOUND: "User not found",
    ErrorCode.SESSION_NOT_FOUND: "Session not found: {session_id}",
}


# Detailed explanations for each error type
ERROR_DETAILS: dict[ErrorCode, str] = {
    # Model errors
    ErrorCode.MODEL_NOT_FOUND: "The requested model is not available in our catalog. Please check the model name and try again with a valid model ID.",
    ErrorCode.MODEL_UNAVAILABLE: "This model is currently unavailable due to provider maintenance or issues. Please try again later or use an alternative model.",
    ErrorCode.MODEL_DEPRECATED: "This model has been deprecated and is no longer available. Please use a newer version of this model or switch to an alternative.",
    ErrorCode.INVALID_MODEL_FORMAT: "The model ID format is invalid. Model IDs should follow the pattern 'provider/model-name' or use a canonical model name.",
    ErrorCode.PROVIDER_MISMATCH: "The specified model is not available through the requested provider. The model may be exclusive to a different provider.",
    ErrorCode.MODEL_REGION_RESTRICTED: "This model is not available in your geographic region due to provider restrictions.",
    # Validation errors
    ErrorCode.MISSING_REQUIRED_FIELD: "A required field is missing from your request. Please include all required parameters and try again.",
    ErrorCode.INVALID_PARAMETER_TYPE: "One of your request parameters has an incorrect type. Please check the API documentation for the correct parameter types.",
    ErrorCode.PARAMETER_OUT_OF_RANGE: "A parameter value is outside the valid range. Please adjust the value to be within the allowed limits.",
    ErrorCode.INVALID_MESSAGE_FORMAT: "The messages array format is invalid. Please ensure messages follow the correct structure with 'role' and 'content' fields.",
    ErrorCode.EMPTY_MESSAGES_ARRAY: "At least one message is required. Please provide a non-empty messages array.",
    ErrorCode.INVALID_ROLE: "Message role must be one of the allowed values (e.g., 'user', 'assistant', 'system').",
    ErrorCode.MAX_TOKENS_EXCEEDED: "The requested max_tokens value exceeds the model's maximum output length. Please reduce the max_tokens parameter.",
    ErrorCode.CONTEXT_LENGTH_EXCEEDED: "Your input is too long for this model's context window. Please reduce the input length or use a model with a larger context window.",
    ErrorCode.INVALID_TEMPERATURE: "Temperature must be a number between 0 and 2. Values closer to 0 make output more deterministic, while values closer to 2 make it more creative.",
    ErrorCode.INVALID_STREAM_PARAMETER: "The 'stream' parameter must be a boolean value (true or false).",
    ErrorCode.INVALID_JSON: "The request body contains invalid JSON. Please ensure your request is properly formatted JSON.",
    ErrorCode.MALFORMED_REQUEST: "The request is malformed and cannot be processed. Please check your request format and try again.",
    ErrorCode.UNSUPPORTED_PARAMETER: "This parameter is not supported for the current endpoint or model. Please refer to the API documentation for supported parameters.",
    ErrorCode.INVALID_CONTENT_TYPE: "Requests must use Content-Type: application/json. Please set the correct content type header.",
    ErrorCode.INVALID_REQUEST_BODY: "The request body is invalid. Please check the request format and ensure all required fields are present.",
    # Authentication errors
    ErrorCode.INVALID_API_KEY: "The provided API key is invalid or not found. Please check your API key and try again.",
    ErrorCode.API_KEY_EXPIRED: "Your API key has expired. Please generate a new API key from your dashboard.",
    ErrorCode.API_KEY_REVOKED: "This API key has been revoked and can no longer be used. Please create a new API key.",
    ErrorCode.API_KEY_MISSING: "No API key was provided. Please include your API key in the Authorization header as 'Bearer YOUR_API_KEY'.",
    ErrorCode.API_KEY_MALFORMED: "The API key format is invalid. API keys should start with 'gw_live_' or 'gw_test_'.",
    ErrorCode.AUTHENTICATION_REQUIRED: "This endpoint requires authentication. Please provide a valid API key.",
    # Authorization errors
    ErrorCode.IP_RESTRICTED: "Your IP address is not authorized to use this API key. Please add your IP to the allowed list in your dashboard.",
    ErrorCode.DOMAIN_RESTRICTED: "Your domain is not authorized to use this API key. Please add your domain to the allowed list.",
    ErrorCode.TRIAL_EXPIRED: "Your free trial has ended. Please upgrade to a paid plan to continue using the API.",
    ErrorCode.PLAN_LIMIT_REACHED: "You have reached your plan's usage limit. Please upgrade your plan or wait for the limit to reset.",
    ErrorCode.INSUFFICIENT_PERMISSIONS: "Your account does not have permission to perform this action. Please contact support if you believe this is an error.",
    ErrorCode.ACCESS_DENIED: "Access to this resource is denied. Please check your permissions.",
    ErrorCode.FEATURE_NOT_AVAILABLE: "This feature is not included in your current plan. Please upgrade to access this feature.",
    # Payment & credit errors
    ErrorCode.INSUFFICIENT_CREDITS: "You do not have enough credits to complete this request. Please add credits to your account to continue.",
    ErrorCode.CREDIT_BELOW_MINIMUM: "Your credit balance has fallen below the minimum required amount. Please add credits to continue making requests.",
    ErrorCode.PAYMENT_METHOD_REQUIRED: "A payment method is required to continue. Please add a valid payment method to your account.",
    ErrorCode.PAYMENT_FAILED: "We were unable to process your payment. Please check your payment method and try again.",
    ErrorCode.INVOICE_OVERDUE: "Your account has outstanding invoices that must be paid before you can continue using the service.",
    ErrorCode.BILLING_ERROR: "A billing error occurred while processing your request. Please contact support for assistance.",
    # Rate limiting errors
    ErrorCode.RATE_LIMIT_EXCEEDED: "You have exceeded the rate limit. Please slow down your requests and try again later.",
    ErrorCode.TOKEN_RATE_LIMIT: "You are sending tokens too quickly. Please reduce your token throughput.",
    ErrorCode.CONCURRENT_REQUEST_LIMIT: "You have too many requests in flight. Please wait for some requests to complete before starting new ones.",
    ErrorCode.DAILY_QUOTA_EXCEEDED: "You have exceeded your daily usage quota. The quota will reset at midnight UTC.",
    ErrorCode.MONTHLY_QUOTA_EXCEEDED: "You have exceeded your monthly usage quota. Please wait for the monthly reset or upgrade your plan.",
    ErrorCode.HOURLY_QUOTA_EXCEEDED: "You have exceeded your hourly usage quota. The quota will reset at the top of the hour.",
    # Provider errors
    ErrorCode.PROVIDER_ERROR: "The upstream AI provider encountered an error while processing your request. This is usually temporary.",
    ErrorCode.PROVIDER_TIMEOUT: "The request to the AI provider timed out. Please try again.",
    ErrorCode.PROVIDER_UNAVAILABLE: "The AI provider is temporarily unavailable. Please try again in a few moments or try a different model.",
    ErrorCode.PROVIDER_RATE_LIMITED: "The AI provider is rate limiting requests. Please try again later or use a different model.",
    ErrorCode.PROVIDER_AUTHENTICATION_ERROR: "There was an authentication error with the AI provider. Please contact support.",
    ErrorCode.PROVIDER_INVALID_RESPONSE: "The AI provider returned an invalid response. Please try again or contact support if the issue persists.",
    ErrorCode.ALL_PROVIDERS_FAILED: "All available providers failed to process your request. This may be due to widespread provider issues. Please try again later.",
    # Service errors
    ErrorCode.INTERNAL_ERROR: "An internal server error occurred. Our team has been notified and is working on a fix.",
    ErrorCode.DATABASE_ERROR: "A database error occurred while processing your request. Please try again later.",
    ErrorCode.SERVICE_UNAVAILABLE: "The service is temporarily unavailable due to maintenance or high load. Please try again shortly.",
    ErrorCode.MAINTENANCE_MODE: "The service is currently undergoing scheduled maintenance. Please check our status page for updates.",
    ErrorCode.CONFIGURATION_ERROR: "A service configuration error occurred. Our team has been notified.",
    ErrorCode.UNEXPECTED_ERROR: "An unexpected error occurred. Please try again or contact support if the problem persists.",
    # Resource errors
    ErrorCode.RESOURCE_NOT_FOUND: "The requested resource was not found. Please check the resource ID and try again.",
    ErrorCode.ENDPOINT_NOT_FOUND: "The requested endpoint does not exist. Please check the URL and try again.",
    ErrorCode.USER_NOT_FOUND: "The specified user was not found.",
    ErrorCode.SESSION_NOT_FOUND: "The chat session was not found. It may have expired or been deleted.",
}


# Suggestions for each error type
ERROR_SUGGESTIONS: dict[ErrorCode, list[str]] = {
    # Model errors
    ErrorCode.MODEL_NOT_FOUND: [
        "Check the list of available models at /v1/models",
        "Verify the model ID is spelled correctly",
        "Visit https://docs.gatewayz.ai/models for the complete model catalog",
    ],
    ErrorCode.MODEL_UNAVAILABLE: [
        "Try again in a few minutes",
        "Use an alternative model from the same provider",
        "Check https://status.gatewayz.ai for provider status updates",
    ],
    ErrorCode.MODEL_DEPRECATED: [
        "Check the model catalog for recommended alternatives",
        "Update your code to use a newer model version",
        "Visit https://docs.gatewayz.ai/models/deprecated for migration guides",
    ],
    ErrorCode.INVALID_MODEL_FORMAT: [
        "Use the format 'provider/model-name' (e.g., 'openrouter/gpt-4')",
        "Or use a canonical name (e.g., 'gpt-4', 'claude-3-opus')",
        "Check /v1/models for valid model IDs",
    ],
    ErrorCode.PROVIDER_MISMATCH: [
        "Check which providers support this model at /v1/models",
        "Remove the provider prefix to allow automatic provider selection",
        "Try a similar model from the requested provider",
    ],
    ErrorCode.MODEL_REGION_RESTRICTED: [
        "Try using a VPN or proxy in a supported region",
        "Use an alternative model that's available in your region",
        "Contact support for region-specific model availability",
    ],
    # Validation errors
    ErrorCode.MISSING_REQUIRED_FIELD: [
        "Check the API documentation for required fields",
        "Ensure all required parameters are included in your request",
        "Visit https://docs.gatewayz.ai/api for endpoint specifications",
    ],
    ErrorCode.INVALID_PARAMETER_TYPE: [
        "Check the expected type for this parameter in the API docs",
        "Ensure numeric values are not quoted as strings",
        "Visit https://docs.gatewayz.ai/api for parameter type specifications",
    ],
    ErrorCode.PARAMETER_OUT_OF_RANGE: [
        "Check the valid range for this parameter in the API documentation",
        "Adjust the parameter value to be within the allowed range",
        "Visit https://docs.gatewayz.ai/api for parameter limits",
    ],
    ErrorCode.INVALID_MESSAGE_FORMAT: [
        "Ensure each message has 'role' and 'content' fields",
        "Check the API docs for the correct message format",
        "Visit https://docs.gatewayz.ai/api/messages for examples",
    ],
    ErrorCode.EMPTY_MESSAGES_ARRAY: [
        "Include at least one message in the messages array",
        "Ensure your messages array is not empty",
    ],
    ErrorCode.INVALID_ROLE: [
        "Use one of: 'user', 'assistant', or 'system'",
        "Check the API docs for supported message roles",
    ],
    ErrorCode.MAX_TOKENS_EXCEEDED: [
        "Reduce the max_tokens parameter",
        "Check the model's maximum output length in the model catalog",
        "Use a model with a higher max_tokens limit",
    ],
    ErrorCode.CONTEXT_LENGTH_EXCEEDED: [
        "Reduce the length of your input messages",
        "Use a model with a larger context window (e.g., GPT-4 Turbo with 128k)",
        "Split your request into smaller chunks",
    ],
    ErrorCode.INVALID_TEMPERATURE: [
        "Set temperature to a value between 0 and 2",
        "Use 0 for deterministic output, 1 for balanced, 2 for creative",
    ],
    ErrorCode.INVALID_STREAM_PARAMETER: [
        "Set stream to true or false (boolean value)",
        'Remove quotes if you\'re sending "true" or "false" as strings',
    ],
    ErrorCode.INVALID_JSON: [
        "Validate your JSON using a JSON validator",
        "Check for missing quotes, commas, or brackets",
        "Ensure all strings are properly escaped",
    ],
    ErrorCode.MALFORMED_REQUEST: [
        "Check your request format against the API documentation",
        "Ensure all required fields are present and properly formatted",
    ],
    ErrorCode.UNSUPPORTED_PARAMETER: [
        "Check the API docs for supported parameters for this endpoint",
        "Remove unsupported parameters from your request",
    ],
    ErrorCode.INVALID_CONTENT_TYPE: [
        "Set the Content-Type header to 'application/json'",
        "Ensure you're sending JSON in the request body",
    ],
    ErrorCode.INVALID_REQUEST_BODY: [
        "Validate your request body against the API schema",
        "Check the API docs for the correct request format",
    ],
    # Authentication errors
    ErrorCode.INVALID_API_KEY: [
        "Verify your API key in your dashboard at https://gatewayz.ai/dashboard",
        "Ensure you're using the correct API key for the environment (test vs live)",
        "Generate a new API key if needed",
    ],
    ErrorCode.API_KEY_EXPIRED: [
        "Generate a new API key from your dashboard",
        "Update your application with the new key",
    ],
    ErrorCode.API_KEY_REVOKED: [
        "Create a new API key from your dashboard",
        "Update your application configuration with the new key",
    ],
    ErrorCode.API_KEY_MISSING: [
        "Add 'Authorization: Bearer YOUR_API_KEY' to your request headers",
        "Check that your API key is being sent correctly",
        "Visit https://docs.gatewayz.ai/authentication for examples",
    ],
    ErrorCode.API_KEY_MALFORMED: [
        "Ensure your API key starts with 'gw_live_' or 'gw_test_'",
        "Copy the full API key from your dashboard without modifications",
        "Verify there are no extra spaces or characters in the key",
    ],
    ErrorCode.AUTHENTICATION_REQUIRED: [
        "Provide a valid API key in the Authorization header",
        "Sign up at https://gatewayz.ai to get an API key",
    ],
    # Authorization errors
    ErrorCode.IP_RESTRICTED: [
        "Add your IP address to the allowed list in your dashboard",
        "Disable IP restrictions if you're using dynamic IPs",
        "Contact support if you need help configuring IP restrictions",
    ],
    ErrorCode.DOMAIN_RESTRICTED: [
        "Add your domain to the allowed list in your dashboard",
        "Ensure you're making requests from an allowed domain",
    ],
    ErrorCode.TRIAL_EXPIRED: [
        "Upgrade to a paid plan at https://gatewayz.ai/pricing",
        "Add credits to your account to continue using the API",
    ],
    ErrorCode.PLAN_LIMIT_REACHED: [
        "Upgrade your plan at https://gatewayz.ai/pricing for higher limits",
        "Wait for your usage quota to reset",
        "Contact support to discuss custom limits",
    ],
    ErrorCode.INSUFFICIENT_PERMISSIONS: [
        "Check your account permissions in the dashboard",
        "Contact your team admin to request access",
        "Reach out to support if you need assistance",
    ],
    ErrorCode.ACCESS_DENIED: [
        "Verify you have permission to access this resource",
        "Contact support if you believe this is an error",
    ],
    ErrorCode.FEATURE_NOT_AVAILABLE: [
        "Upgrade your plan to access this feature",
        "Visit https://gatewayz.ai/pricing to see plan features",
    ],
    # Payment & credit errors
    ErrorCode.INSUFFICIENT_CREDITS: [
        "Add credits at https://gatewayz.ai/billing",
        "Enable auto-recharge to prevent interruptions",
        "Consider upgrading to a subscription plan for better rates",
    ],
    ErrorCode.CREDIT_BELOW_MINIMUM: [
        "Add more credits to your account",
        "Set up auto-recharge to maintain a minimum balance",
    ],
    ErrorCode.PAYMENT_METHOD_REQUIRED: [
        "Add a payment method at https://gatewayz.ai/billing",
        "Verify your payment method is valid and active",
    ],
    ErrorCode.PAYMENT_FAILED: [
        "Check your payment method details",
        "Ensure your card has sufficient funds",
        "Try a different payment method",
        "Contact your bank if the issue persists",
    ],
    ErrorCode.INVOICE_OVERDUE: [
        "Pay outstanding invoices at https://gatewayz.ai/billing",
        "Contact support if you need payment assistance",
    ],
    ErrorCode.BILLING_ERROR: [
        "Try your request again",
        "Contact support at https://gatewayz.ai/support for assistance",
    ],
    # Rate limiting errors
    ErrorCode.RATE_LIMIT_EXCEEDED: [
        "Wait before making additional requests",
        "Check the Retry-After header for when to retry",
        "Implement exponential backoff in your application",
        "Upgrade your plan for higher rate limits",
    ],
    ErrorCode.TOKEN_RATE_LIMIT: [
        "Reduce the number of tokens in your requests",
        "Spread your requests over a longer time period",
        "Upgrade your plan for higher token limits",
    ],
    ErrorCode.CONCURRENT_REQUEST_LIMIT: [
        "Wait for existing requests to complete before starting new ones",
        "Implement request queuing in your application",
        "Upgrade your plan for higher concurrency limits",
    ],
    ErrorCode.DAILY_QUOTA_EXCEEDED: [
        "Wait until midnight UTC for your quota to reset",
        "Upgrade your plan for a higher daily quota",
    ],
    ErrorCode.MONTHLY_QUOTA_EXCEEDED: [
        "Wait for the monthly quota reset",
        "Upgrade your plan for a higher monthly quota",
    ],
    ErrorCode.HOURLY_QUOTA_EXCEEDED: [
        "Wait until the top of the hour for your quota to reset",
        "Upgrade your plan for higher hourly limits",
    ],
    # Provider errors
    ErrorCode.PROVIDER_ERROR: [
        "Try your request again",
        "Try a different model from an alternative provider",
        "Check https://status.gatewayz.ai for provider status",
    ],
    ErrorCode.PROVIDER_TIMEOUT: [
        "Retry your request",
        "Try a different model or provider",
        "Check if the provider is experiencing issues",
    ],
    ErrorCode.PROVIDER_UNAVAILABLE: [
        "Wait a few minutes and try again",
        "Use a model from a different provider",
        "Check https://status.gatewayz.ai for updates",
    ],
    ErrorCode.PROVIDER_RATE_LIMITED: [
        "Wait a few moments and retry",
        "Use a model from a different provider",
        "Gatewayz automatically handles provider failover",
    ],
    ErrorCode.PROVIDER_AUTHENTICATION_ERROR: [
        "Try again - this is usually a temporary issue",
        "Contact support if the error persists",
    ],
    ErrorCode.PROVIDER_INVALID_RESPONSE: [
        "Retry your request",
        "Contact support if the issue continues",
    ],
    ErrorCode.ALL_PROVIDERS_FAILED: [
        "Wait a few minutes and try again",
        "Check https://status.gatewayz.ai for provider status",
        "Contact support if the issue persists",
    ],
    # Service errors
    ErrorCode.INTERNAL_ERROR: [
        "Try your request again",
        "Contact support if the error persists",
        "Check https://status.gatewayz.ai for service status",
    ],
    ErrorCode.DATABASE_ERROR: [
        "Retry your request",
        "Contact support if the issue continues",
    ],
    ErrorCode.SERVICE_UNAVAILABLE: [
        "Wait a few minutes and try again",
        "Check https://status.gatewayz.ai for status updates",
    ],
    ErrorCode.MAINTENANCE_MODE: [
        "Check https://status.gatewayz.ai for maintenance schedule",
        "Try again after the maintenance window",
    ],
    ErrorCode.CONFIGURATION_ERROR: [
        "Contact support for assistance",
        "Try again later",
    ],
    ErrorCode.UNEXPECTED_ERROR: [
        "Try your request again",
        "Contact support if the problem persists",
    ],
    # Resource errors
    ErrorCode.RESOURCE_NOT_FOUND: [
        "Verify the resource ID is correct",
        "Check if the resource still exists",
    ],
    ErrorCode.ENDPOINT_NOT_FOUND: [
        "Check the API documentation for the correct endpoint",
        "Verify the HTTP method (GET, POST, etc.) is correct",
        "Visit https://docs.gatewayz.ai/api for endpoint reference",
    ],
    ErrorCode.USER_NOT_FOUND: [
        "Verify the user ID or email is correct",
        "Check if the user account still exists",
    ],
    ErrorCode.SESSION_NOT_FOUND: [
        "The session may have expired",
        "Create a new chat session",
    ],
}


# Documentation URLs for each error type
ERROR_DOCS_URLS: dict[ErrorCode, str] = {
    ErrorCode.MODEL_NOT_FOUND: "https://docs.gatewayz.ai/errors/model-not-found",
    ErrorCode.INSUFFICIENT_CREDITS: "https://docs.gatewayz.ai/errors/insufficient-credits",
    ErrorCode.RATE_LIMIT_EXCEEDED: "https://docs.gatewayz.ai/errors/rate-limits",
    ErrorCode.INVALID_API_KEY: "https://docs.gatewayz.ai/authentication",
    ErrorCode.CONTEXT_LENGTH_EXCEEDED: "https://docs.gatewayz.ai/errors/context-length",
    # Add more as needed
}


def get_error_message(error_code: ErrorCode, **kwargs) -> str:
    """
    Get the error message template for an error code with optional formatting.

    Args:
        error_code: The error code
        **kwargs: Values to format into the message template

    Returns:
        Formatted error message
    """
    template = ERROR_MESSAGES.get(error_code, "An error occurred")
    try:
        return template.format(**kwargs)
    except KeyError:
        # If formatting fails, return template as-is
        return template


def get_error_detail(error_code: ErrorCode) -> str:
    """
    Get the detailed explanation for an error code.

    Args:
        error_code: The error code

    Returns:
        Detailed error explanation
    """
    return ERROR_DETAILS.get(error_code, "Please try again or contact support.")


def get_suggestions(error_code: ErrorCode) -> list[str]:
    """
    Get actionable suggestions for resolving an error.

    Args:
        error_code: The error code

    Returns:
        List of suggestions
    """
    return ERROR_SUGGESTIONS.get(
        error_code, ["Try again later", "Contact support if the issue persists"]
    )


def get_docs_url(error_code: ErrorCode) -> str | None:
    """
    Get the documentation URL for an error code.

    Args:
        error_code: The error code

    Returns:
        Documentation URL or None
    """
    return ERROR_DOCS_URLS.get(error_code)


# ============================================================================
# Merged from exceptions.py
# ============================================================================

"""
HTTP Exception Factories

Centralized exception creation with consistent error messages and status codes.
Eliminates 308+ duplicate HTTPException patterns across the codebase.

Supports both simple and detailed error modes:
- Simple mode: Traditional HTTPException with string detail
- Detailed mode: Rich error responses with context, suggestions, and documentation

Usage:
    from src.utils.errors import APIExceptions

    # Simple mode (backward compatible):
    raise APIExceptions.unauthorized()

    # Detailed mode:
    raise APIExceptions.model_not_found_detailed(
        model_id="gpt-5",
        suggested_models=["gpt-4", "gpt-4-turbo"],
        request_id="req_123"
    )
"""

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class APIExceptions:
    """Factory class for creating standardized HTTP exceptions."""

    @staticmethod
    def unauthorized(detail: str = "Invalid API key or unauthorized access") -> HTTPException:
        """
        401 Unauthorized - Authentication failed.

        Args:
            detail: Custom error message

        Returns:
            HTTPException with status 401
        """
        return HTTPException(status_code=401, detail=detail)

    @staticmethod
    def invalid_api_key() -> HTTPException:
        """401 Unauthorized - Specific for invalid API key."""
        return HTTPException(status_code=401, detail="Invalid API key")

    @staticmethod
    def payment_required(
        detail: str = "Insufficient credits. Please add credits to continue.",
        credits: float | None = None,
    ) -> HTTPException:
        """
        402 Payment Required - User has insufficient credits.

        SECURITY: Never expose exact credit balance or required amounts in the
        HTTP response. Use server-side logging for debugging instead.

        Args:
            detail: Custom error message (should NOT contain dollar amounts)
            credits: Ignored for security - kept for backward compatibility

        Returns:
            HTTPException with status 402
        """
        # SECURITY: Do not include credit balance in the response detail
        return HTTPException(
            status_code=402,
            detail="Insufficient credits. Please add credits to continue.",
        )

    @staticmethod
    def forbidden(detail: str = "Access forbidden") -> HTTPException:
        """
        403 Forbidden - User doesn't have permission.

        Args:
            detail: Custom error message

        Returns:
            HTTPException with status 403
        """
        return HTTPException(status_code=403, detail=detail)

    @staticmethod
    def not_found(resource: str = "Resource", resource_id: Any | None = None) -> HTTPException:
        """
        404 Not Found - Resource doesn't exist.

        Args:
            resource: Type of resource (e.g., "User", "Model", "Session")
            resource_id: Optional ID of the resource

        Returns:
            HTTPException with status 404
        """
        detail = f"{resource} not found"
        if resource_id is not None:
            detail += f": {resource_id}"
        return HTTPException(status_code=404, detail=detail)

    @staticmethod
    def rate_limited(
        retry_after: int | None = None,
        detail: str = "Rate limit exceeded",
        reason: str | None = None,
        rate_limit_headers: dict[str, str] | None = None,
    ) -> HTTPException:
        """
        429 Too Many Requests - Rate limit exceeded.

        Args:
            retry_after: Seconds until retry is allowed
            detail: Custom error message
            reason: Optional reason for rate limit (e.g., "token_limit", "request_limit")
            rate_limit_headers: Optional dict of rate limit headers from
                get_rate_limit_headers() or get_anonymous_rate_limit_headers()

        Returns:
            HTTPException with status 429, Retry-After, and rate limit headers
        """
        if reason:
            detail = f"{detail}: {reason}"

        headers: dict[str, str] = {}
        if rate_limit_headers:
            headers.update(rate_limit_headers)
        if retry_after:
            headers["Retry-After"] = str(retry_after)
        return HTTPException(status_code=429, detail=detail, headers=headers or None)

    @staticmethod
    def plan_limit_exceeded(reason: str = "unknown") -> HTTPException:
        """
        429 Too Many Requests - Plan limit exceeded.

        Args:
            reason: Reason for limit (e.g., "monthly_quota", "request_limit")

        Returns:
            HTTPException with status 429
        """
        return HTTPException(status_code=429, detail=f"Plan limit exceeded: {reason}")

    @staticmethod
    def bad_request(
        detail: str = "Bad request", errors: dict[str, Any] | None = None
    ) -> HTTPException:
        """
        400 Bad Request - Invalid request data.

        Args:
            detail: Error message
            errors: Optional validation errors dict

        Returns:
            HTTPException with status 400
        """
        if errors:
            return HTTPException(status_code=400, detail={"message": detail, "errors": errors})
        return HTTPException(status_code=400, detail=detail)

    @staticmethod
    def internal_error(
        operation: str = "operation", error: Exception | None = None, include_details: bool = False
    ) -> HTTPException:
        """
        500 Internal Server Error - Server-side error.

        Args:
            operation: Name of the operation that failed
            error: Optional exception that caused the error
            include_details: Whether to include error details (dev mode only)

        Returns:
            HTTPException with status 500
        """
        detail = f"Internal error during {operation}"

        if error and include_details:
            detail += f": {str(error)}"

        # Log the full error for debugging
        if error:
            logger.error(f"Internal error in {operation}: {error}", exc_info=True)

        return HTTPException(status_code=500, detail=detail)

    @staticmethod
    def database_error(
        operation: str = "database operation", error: Exception | None = None
    ) -> HTTPException:
        """
        500 Internal Server Error - Database operation failed.

        Args:
            operation: Name of the database operation
            error: Optional exception

        Returns:
            HTTPException with status 500
        """
        logger.error(f"Database error during {operation}: {error}", exc_info=True)
        return HTTPException(status_code=500, detail=f"Database error during {operation}")

    @staticmethod
    def provider_error(
        provider: str, model: str, error: Exception | None = None, status_code: int = 502
    ) -> HTTPException:
        """
        502 Bad Gateway - Upstream provider error.

        Args:
            provider: Provider name (e.g., "openrouter", "anthropic")
            model: Model name
            error: Optional exception from provider
            status_code: HTTP status code (default 502)

        Returns:
            HTTPException with appropriate status code
        """
        detail = f"Provider {provider} failed for model {model}"

        if error:
            logger.error(f"Provider error ({provider}/{model}): {error}", exc_info=True)

        return HTTPException(status_code=status_code, detail=detail)

    @staticmethod
    def service_unavailable(
        service: str = "service", retry_after: int | None = None
    ) -> HTTPException:
        """
        503 Service Unavailable - Service temporarily unavailable.

        Args:
            service: Name of the unavailable service
            retry_after: Optional seconds to wait before retry

        Returns:
            HTTPException with status 503
        """
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return HTTPException(
            status_code=503, detail=f"{service} is temporarily unavailable", headers=headers
        )

    @staticmethod
    def validation_error(field: str, message: str) -> HTTPException:
        """
        422 Unprocessable Entity - Validation failed.

        Args:
            field: Field that failed validation
            message: Validation error message

        Returns:
            HTTPException with status 422
        """
        return HTTPException(status_code=422, detail={"field": field, "message": message})

    # ==================== Detailed Error Methods ====================
    # These methods use the DetailedErrorFactory for rich error responses

    @staticmethod
    def model_not_found_detailed(
        model_id: str,
        provider: str | None = None,
        suggested_models: list[str] | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        404 Model Not Found - Detailed version with suggestions.

        Args:
            model_id: The requested model ID
            provider: Optional provider name
            suggested_models: Optional list of similar models
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_handlers import create_error_response_dict
        from src.utils.errors import DetailedErrorFactory

        error = DetailedErrorFactory.model_not_found(
            model_id=model_id,
            provider=provider,
            suggested_models=suggested_models,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def insufficient_credits_detailed(
        current_credits: float,
        required_credits: float,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        402 Insufficient Credits - Detailed version with amounts.

        Args:
            current_credits: User's current credit balance
            required_credits: Credits required for request
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_handlers import create_error_response_dict
        from src.utils.errors import DetailedErrorFactory

        error = DetailedErrorFactory.insufficient_credits(
            current_credits=current_credits,
            required_credits=required_credits,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def insufficient_credits_for_reservation(
        current_credits: float,
        max_cost: float,
        model_id: str,
        max_tokens: int,
        input_tokens: int | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        402 Insufficient Credits - Pre-flight check version with max cost details.

        This error is raised BEFORE making a provider request when the user doesn't
        have enough credits to cover the maximum possible cost (based on max_tokens).

        Args:
            current_credits: User's current credit balance
            max_cost: Maximum possible cost for the request
            model_id: Model being requested
            max_tokens: Maximum output tokens parameter
            input_tokens: Optional estimated input tokens
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response including:
            - Maximum possible cost
            - Current balance
            - Shortfall amount
            - Actionable suggestions (reduce max_tokens, add credits)
            - Calculated recommended max_tokens

        Example:
            >>> raise APIExceptions.insufficient_credits_for_reservation(
            ...     current_credits=0.05,
            ...     max_cost=0.20,
            ...     model_id="gpt-4o",
            ...     max_tokens=4096,
            ...     input_tokens=100
            ... )
        """
        from src.utils.error_handlers import create_error_response_dict
        from src.utils.errors import DetailedErrorFactory

        error = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=current_credits,
            max_cost=max_cost,
            model_id=model_id,
            max_tokens=max_tokens,
            input_tokens=input_tokens,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def invalid_api_key_detailed(
        reason: str | None = None,
        key_prefix: str | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        401 Invalid API Key - Detailed version.

        Args:
            reason: Optional reason for invalidity
            key_prefix: First few characters of the key
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_handlers import create_error_response_dict
        from src.utils.errors import DetailedErrorFactory

        error = DetailedErrorFactory.invalid_api_key(
            reason=reason,
            key_prefix=key_prefix,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def rate_limit_exceeded_detailed(
        limit_type: str,
        retry_after: int | None = None,
        limit_value: int | None = None,
        current_usage: int | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        429 Rate Limit Exceeded - Detailed version with retry info.

        Args:
            limit_type: Type of rate limit
            retry_after: Seconds until retry is allowed
            limit_value: Rate limit threshold
            current_usage: Current usage count
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_handlers import create_error_response_dict
        from src.utils.errors import DetailedErrorFactory

        error = DetailedErrorFactory.rate_limit_exceeded(
            limit_type=limit_type,
            retry_after=retry_after,
            limit_value=limit_value,
            current_usage=current_usage,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def provider_error_detailed(
        provider: str,
        model: str,
        provider_message: str | None = None,
        status_code: int = 502,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        502 Provider Error - Detailed version.

        Args:
            provider: Provider name
            model: Model ID
            provider_message: Original provider error message
            status_code: HTTP status code
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_handlers import create_error_response_dict
        from src.utils.errors import DetailedErrorFactory

        error = DetailedErrorFactory.provider_error(
            provider=provider,
            model=model,
            provider_message=provider_message,
            status_code=status_code,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )

    @staticmethod
    def invalid_parameter_detailed(
        parameter_name: str,
        parameter_value: Any,
        expected_type: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        allowed_values: list[Any] | None = None,
        request_id: str | None = None,
    ) -> HTTPException:
        """
        400 Invalid Parameter - Detailed version.

        Args:
            parameter_name: Name of invalid parameter
            parameter_value: The invalid value
            expected_type: Expected type (for type mismatches)
            min_value: Minimum allowed value
            max_value: Maximum allowed value
            allowed_values: List of allowed values
            request_id: Optional request ID

        Returns:
            HTTPException with detailed error response
        """
        from src.utils.error_handlers import create_error_response_dict
        from src.utils.errors import DetailedErrorFactory

        error = DetailedErrorFactory.invalid_parameter(
            parameter_name=parameter_name,
            parameter_value=parameter_value,
            expected_type=expected_type,
            min_value=min_value,
            max_value=max_value,
            allowed_values=allowed_values,
            request_id=request_id,
        )
        response_dict, headers = create_error_response_dict(error)

        return HTTPException(
            status_code=error.error.status,
            detail=response_dict,
            headers=headers,
        )


# Convenience aliases for common exceptions
unauthorized = APIExceptions.unauthorized
invalid_api_key = APIExceptions.invalid_api_key
payment_required = APIExceptions.payment_required
forbidden = APIExceptions.forbidden
not_found = APIExceptions.not_found
rate_limited = APIExceptions.rate_limited
plan_limit_exceeded = APIExceptions.plan_limit_exceeded
bad_request = APIExceptions.bad_request
internal_error = APIExceptions.internal_error
database_error = APIExceptions.database_error
provider_error = APIExceptions.provider_error
service_unavailable = APIExceptions.service_unavailable
validation_error = APIExceptions.validation_error


# ============================================================================
# Merged from error_factory.py
# ============================================================================

"""
Error Factory

Factory functions for creating detailed, user-friendly error responses.
Provides standardized error creation across the entire API.

Usage:
    from src.utils.errors import DetailedErrorFactory

    # Model not found error
    error = DetailedErrorFactory.model_not_found(
        model_id="gpt-5",
        suggested_models=["gpt-4", "gpt-4-turbo"],
        request_id="req_123"
    )

    # Raise as HTTPException
    raise HTTPException(
        status_code=error.error.status,
        detail=error.dict(exclude_none=True)
    )
"""

import uuid
from datetime import datetime
from typing import Any

from src.schemas.errors import ErrorContext, ErrorDetail, ErrorResponse


class DetailedErrorFactory:
    """Factory class for creating detailed error responses."""

    # ==================== Model Errors ====================

    @staticmethod
    def model_not_found(
        model_id: str,
        provider: str | None = None,
        suggested_models: list[str] | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a model not found error with suggestions.

        Args:
            model_id: The requested model ID
            provider: Optional provider name
            suggested_models: Optional list of similar models
            request_id: Optional request ID

        Returns:
            ErrorResponse with detailed model not found error
        """
        code = ErrorCode.MODEL_NOT_FOUND
        message = get_error_message(code, model_id=model_id)
        detail = get_error_detail(code)
        suggestions = get_suggestions(code)

        # Add specific model suggestions if provided
        if suggested_models:
            suggestions.insert(
                1, f"Try using one of these similar models: {', '.join(suggested_models[:3])}"
            )

        context = ErrorContext(
            requested_model=model_id,
            suggested_models=suggested_models,
            provider=provider,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def model_unavailable(
        model_id: str,
        provider: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a model unavailable error."""
        code = ErrorCode.MODEL_UNAVAILABLE
        message = get_error_message(code, model_id=model_id)
        detail = get_error_detail(code)
        if reason:
            detail = f"{detail} Reason: {reason}"

        context = ErrorContext(
            requested_model=model_id,
            provider=provider,
            provider_status=reason,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Validation Errors ====================

    @staticmethod
    def missing_required_field(
        field_name: str,
        endpoint: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a missing required field error."""
        code = ErrorCode.MISSING_REQUIRED_FIELD
        message = get_error_message(code, field_name=field_name)

        context = ErrorContext(
            parameter_name=field_name,
            endpoint=endpoint,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def invalid_parameter(
        parameter_name: str,
        parameter_value: Any,
        expected_type: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        allowed_values: list[Any] | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an invalid parameter error.

        Args:
            parameter_name: Name of the invalid parameter
            parameter_value: The invalid value provided
            expected_type: Expected type (for type mismatches)
            min_value: Minimum allowed value (for range errors)
            max_value: Maximum allowed value (for range errors)
            allowed_values: List of allowed values
            request_id: Optional request ID

        Returns:
            ErrorResponse with parameter validation error
        """
        # Determine which error code to use
        if min_value is not None or max_value is not None:
            code = ErrorCode.PARAMETER_OUT_OF_RANGE
            message = get_error_message(
                code,
                parameter_name=parameter_name,
                value=parameter_value,
                min_value=min_value or 0,
                max_value=max_value or 0,
            )
        elif expected_type:
            code = ErrorCode.INVALID_PARAMETER_TYPE
            actual_type = type(parameter_value).__name__
            message = get_error_message(
                code,
                parameter_name=parameter_name,
                expected_type=expected_type,
                actual_type=actual_type,
            )
        else:
            # Generic parameter error
            code = ErrorCode.INVALID_PARAMETER_TYPE
            message = f"Invalid value for parameter '{parameter_name}'"

        context = ErrorContext(
            parameter_name=parameter_name,
            parameter_value=parameter_value,
            expected_type=expected_type,
            min_value=min_value,
            max_value=max_value,
            allowed_values=allowed_values,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def context_length_exceeded(
        input_tokens: int,
        max_context_length: int,
        model_id: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a context length exceeded error."""
        code = ErrorCode.CONTEXT_LENGTH_EXCEEDED
        message = get_error_message(
            code,
            input_tokens=input_tokens,
            max_context=max_context_length,
        )

        context = ErrorContext(
            input_tokens=input_tokens,
            max_context_length=max_context_length,
            requested_model=model_id,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def empty_messages_array(
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an empty messages array error."""
        code = ErrorCode.EMPTY_MESSAGES_ARRAY
        message = get_error_message(code)

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Authentication Errors ====================

    @staticmethod
    def invalid_api_key(
        reason: str | None = None,
        key_prefix: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an invalid API key error.

        Args:
            reason: Optional reason for invalidity
            key_prefix: First few characters of the key (for debugging)
            request_id: Optional request ID

        Returns:
            ErrorResponse with invalid API key error
        """
        code = ErrorCode.INVALID_API_KEY
        message = get_error_message(code)
        detail = get_error_detail(code)
        if reason:
            detail = f"{detail} {reason}"

        context = ErrorContext(
            key_prefix=key_prefix,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def api_key_missing(
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an API key missing error."""
        code = ErrorCode.API_KEY_MISSING

        error = ErrorDetail(
            type=get_error_type(code),
            message=get_error_message(code),
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Authorization Errors ====================

    @staticmethod
    def trial_expired(
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a trial expired error."""
        code = ErrorCode.TRIAL_EXPIRED

        context = ErrorContext(
            trial_status="expired",
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=get_error_message(code),
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def plan_limit_reached(
        reason: str,
        plan_name: str | None = None,
        limit: int | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a plan limit reached error."""
        code = ErrorCode.PLAN_LIMIT_REACHED
        message = get_error_message(code, reason=reason)

        context = ErrorContext(
            plan_name=plan_name,
            plan_limit=limit,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def ip_restricted(
        ip_address: str,
        allowed_ips: list[str] | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an IP restricted error."""
        code = ErrorCode.IP_RESTRICTED
        message = get_error_message(code, ip_address=ip_address)

        context = ErrorContext(
            ip_address=ip_address,
            allowed_ips=allowed_ips,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Payment & Credit Errors ====================

    @staticmethod
    def insufficient_credits(
        current_credits: float,
        required_credits: float,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an insufficient credits error.

        SECURITY: Does not expose exact credit amounts in the response.
        Amounts are kept for internal logging only.

        Args:
            current_credits: User's current credit balance (logged server-side only)
            required_credits: Credits required for the request (logged server-side only)
            request_id: Optional request ID

        Returns:
            ErrorResponse with sanitized insufficient credits error
        """
        code = ErrorCode.INSUFFICIENT_CREDITS
        message = get_error_message(code)
        detail = "Please add credits to your account to complete this request."

        # SECURITY: Do not include exact credit amounts in the response context
        context = ErrorContext()

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
            support_url="https://gatewayz.ai/support",
        )

        return ErrorResponse(error=error)

    @staticmethod
    def insufficient_credits_for_reservation(
        current_credits: float,
        max_cost: float,
        model_id: str,
        max_tokens: int,
        input_tokens: int | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an insufficient credits error for credit reservation (pre-flight check).

        This error is raised BEFORE making a provider request when the user doesn't
        have enough credits to cover the maximum possible cost.

        Args:
            current_credits: User's current credit balance
            max_cost: Maximum possible cost for the request
            model_id: Model being requested
            max_tokens: Maximum output tokens parameter
            input_tokens: Optional estimated input tokens
            request_id: Optional request ID

        Returns:
            ErrorResponse with detailed insufficient credits error for reservation
        """
        code = ErrorCode.INSUFFICIENT_CREDITS

        # SECURITY: Sanitize user-facing message - no dollar amounts or credit values
        message = "Insufficient credits for this request. Please add credits to continue."

        detail = (
            "Your account does not have enough credits for this request. "
            "Consider reducing max_tokens or using a less expensive model."
        )

        # SECURITY: Sanitized suggestions without dollar amounts
        suggestions = [
            "Add more credits to your account",
            "Reduce max_tokens to lower the maximum possible cost",
            "Use a less expensive model",
            "Visit https://gatewayz.ai/pricing to add credits",
        ]

        # SECURITY: Do not include exact credit amounts in the response context
        context = ErrorContext(
            requested_model=model_id,
            requested_max_tokens=max_tokens,
            input_tokens=input_tokens,
            additional_info={
                "reason": "pre_flight_check",
                "check_type": "credit_reservation",
                "note": "This is a conservative estimate. Actual cost may be lower based on actual token usage.",
            },
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url="https://docs.gatewayz.ai/pricing-and-billing/credits",
            support_url="https://gatewayz.ai/support",
        )

        return ErrorResponse(error=error)

    # ==================== Rate Limiting Errors ====================

    @staticmethod
    def rate_limit_exceeded(
        limit_type: str,
        retry_after: int | None = None,
        limit_value: int | None = None,
        current_usage: int | None = None,
        reset_time: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a rate limit exceeded error.

        Args:
            limit_type: Type of rate limit (e.g., "requests_per_minute")
            retry_after: Seconds until retry is allowed
            limit_value: Rate limit threshold
            current_usage: Current usage count
            reset_time: When the limit resets (ISO 8601)
            request_id: Optional request ID

        Returns:
            ErrorResponse with rate limit error
        """
        code = ErrorCode.RATE_LIMIT_EXCEEDED
        message = get_error_message(code, limit_type=limit_type)

        context = ErrorContext(
            limit_type=limit_type,
            limit_value=limit_value,
            current_usage=current_usage,
            retry_after=retry_after,
            reset_time=reset_time,
        )

        suggestions = get_suggestions(code)
        if retry_after:
            suggestions.insert(0, f"Wait {retry_after} seconds before retrying")

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def daily_quota_exceeded(
        limit: int,
        used: int,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a daily quota exceeded error."""
        code = ErrorCode.DAILY_QUOTA_EXCEEDED
        message = get_error_message(code, limit=limit, used=used)

        context = ErrorContext(
            limit_type="daily_quota",
            limit_value=limit,
            current_usage=used,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Provider Errors ====================

    @staticmethod
    def provider_error(
        provider: str,
        model: str,
        provider_message: str | None = None,
        status_code: int = 502,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a provider error.

        Args:
            provider: Provider name
            model: Model ID
            provider_message: Original provider error message
            status_code: HTTP status code (default 502)
            request_id: Optional request ID

        Returns:
            ErrorResponse with provider error
        """
        code = ErrorCode.PROVIDER_ERROR
        message = get_error_message(
            code,
            provider=provider,
            model_id=model,
            error_message=provider_message or "Unknown error",
        )

        context = ErrorContext(
            provider=provider,
            requested_model=model,
            provider_error_message=provider_message,
            provider_status_code=status_code,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=status_code,
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def provider_timeout(
        provider: str,
        model: str | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a provider timeout error."""
        code = ErrorCode.PROVIDER_TIMEOUT
        message = get_error_message(code, provider=provider)

        context = ErrorContext(
            provider=provider,
            requested_model=model,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def provider_unavailable(
        provider: str,
        model: str,
        retry_after: int = 60,
        circuit_breaker_state: str = "open",
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create a provider unavailable error (circuit breaker open).

        This error is raised when the circuit breaker is open for a provider,
        preventing requests from being sent to an unhealthy provider.

        Args:
            provider: Provider name
            model: Model ID
            retry_after: Seconds until provider will be retried
            circuit_breaker_state: Current circuit breaker state (open/half_open)
            request_id: Optional request ID

        Returns:
            ErrorResponse with provider unavailable error (503)
        """
        code = ErrorCode.PROVIDER_ERROR
        message = f"Provider '{provider}' is temporarily unavailable for model '{model}'"

        detail = (
            f"The circuit breaker for provider '{provider}' is currently {circuit_breaker_state.upper()}. "
            f"This provider has been experiencing issues and has been temporarily disabled to prevent cascading failures. "
            f"The provider will be automatically retried in {retry_after} seconds."
        )

        context = ErrorContext(
            provider=provider,
            requested_model=model,
            provider_status="circuit_breaker_open",
            retry_after=retry_after,
            additional_info={
                "circuit_breaker_state": circuit_breaker_state,
                "auto_retry_in_seconds": retry_after,
                "reason": "Provider health check failed, circuit breaker activated",
            },
        )

        suggestions = [
            f"Wait {retry_after} seconds for automatic retry",
            "Try a different model that uses a different provider",
            "Check the status page for provider health: https://status.gatewayz.ai",
            "Contact support if the issue persists",
        ]

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=detail,
            code=code,
            status=503,  # Service Unavailable
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=suggestions,
            context=context,
            docs_url="https://docs.gatewayz.ai/troubleshooting/circuit-breaker",
            support_url="https://gatewayz.ai/support",
        )

        return ErrorResponse(error=error)

    @staticmethod
    def all_providers_failed(
        model: str,
        failed_providers: list[str],
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create an all providers failed error."""
        code = ErrorCode.ALL_PROVIDERS_FAILED
        message = get_error_message(code, model_id=model)

        context = ErrorContext(
            requested_model=model,
            failed_providers=failed_providers,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    # ==================== Service Errors ====================

    @staticmethod
    def internal_error(
        operation: str = "operation",
        error: Exception | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """
        Create an internal server error.

        Args:
            operation: Name of operation that failed
            error: Optional exception that caused the error
            request_id: Optional request ID

        Returns:
            ErrorResponse with internal error
        """
        code = ErrorCode.INTERNAL_ERROR
        message = get_error_message(code)

        # Don't expose internal error details to users
        error_info = None
        if error:
            error_info = {"operation": operation}

        context = ErrorContext(
            additional_info=error_info,
        )

        resp = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=resp)

    @staticmethod
    def service_unavailable(
        service: str = "service",
        retry_after: int | None = None,
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a service unavailable error."""
        code = ErrorCode.SERVICE_UNAVAILABLE
        message = get_error_message(code)

        context = ErrorContext(
            retry_after=retry_after,
        )

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            context=context,
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)

    @staticmethod
    def database_error(
        operation: str = "database operation",
        request_id: str | None = None,
    ) -> ErrorResponse:
        """Create a database error."""
        code = ErrorCode.DATABASE_ERROR
        message = get_error_message(code)

        error = ErrorDetail(
            type=get_error_type(code),
            message=message,
            detail=get_error_detail(code),
            code=code,
            status=get_status_code(code),
            request_id=request_id or f"req_{uuid.uuid4().hex[:12]}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            suggestions=get_suggestions(code),
            docs_url=get_docs_url(code),
        )

        return ErrorResponse(error=error)
