"""
Error Message Templates

Pre-defined, user-friendly error messages and suggestions for each error type.
Provides consistent, helpful error messages across the API.

Usage:
    from src.utils.error_messages import get_error_message, get_suggestions

    message = get_error_message(ErrorCode.MODEL_NOT_FOUND, model_id="gpt-5")
    suggestions = get_suggestions(ErrorCode.MODEL_NOT_FOUND)
"""

from src.utils.error_codes import ErrorCode

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
    ErrorCode.INSUFFICIENT_CREDITS: "Insufficient credits. Required: ${required_credits:.4f}, Current: ${current_credits:.4f}",
    ErrorCode.CREDIT_BELOW_MINIMUM: "Credit balance (${current_credits:.4f}) is below the minimum required amount (${minimum:.4f})",
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
