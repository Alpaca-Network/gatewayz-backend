"""
Centralized schema exports
"""

# Admin models
from src.schemas.admin import (
    AdminMonitorResponse,
    RateLimitConfig,
    RateLimitResponse,
    SetRateLimitRequest,
    UsageMetrics,
    UsageRecord,
    UserMonitorResponse,
)

# API Key models
from src.schemas.api_keys import (
    ApiKeyResponse,
    ApiKeyUsageResponse,
    CreateApiKeyRequest,
    DeleteApiKeyRequest,
    DeleteApiKeyResponse,
    ListApiKeysResponse,
    UpdateApiKeyRequest,
    UpdateApiKeyResponse,
)

# Auth models
from src.schemas.auth import (
    PrivyAuthRequest,
    PrivyAuthResponse,
    PrivyLinkedAccount,
    PrivySigninRequest,
    PrivySignupRequest,
    PrivyUserData,
)

# Common enums
from src.schemas.common import AuthMethod, PaymentMethod, PlanType, SubscriptionStatus

# Payment models (includes both generic payment and Stripe-specific models)
from src.schemas.payments import (
    AddCreditsRequest,
    CheckoutSessionResponse,
    CreateCheckoutSessionRequest,
    CreatePaymentIntentRequest,
    CreateRefundRequest,
    CreateStripeCustomerRequest,
    CreateSubscriptionRequest,
    CreditPackage,
    CreditPackagesResponse,
    CreditPurchaseRequest,
    CreditPurchaseResponse,
    PaymentCreate,
    PaymentHistoryResponse,
    PaymentIntentResponse,
    PaymentRecord,
    PaymentResponse,
    PaymentStatsResponse,
    PaymentStatus,
    PaymentSummary,
    PaymentUpdate,
    RefundResponse,
    StripeCurrency,
    StripeCustomerResponse,
    StripeErrorResponse,
    StripePaymentMethodType,
    StripeWebhookEvent,
    StripeWebhookEventType,
)
from src.schemas.payments import (
    SubscriptionPlan as PaymentSubscriptionPlan,  # Stripe-specific models; Rename to avoid conflict
)
from src.schemas.payments import (
    SubscriptionResponse,
    WebhookProcessingResult,
)

# Plan models
from src.schemas.plans import SubscriptionPlan  # This is the correct one for trial service
from src.schemas.plans import (
    AssignPlanRequest,
    PlanEntitlementsResponse,
    PlanResponse,
    PlanUsageResponse,
    SubscriptionHistory,
    SubscriptionPlansResponse,
    UserPlanResponse,
)

# Proxy models
from src.schemas.proxy import (  # Anthropic Messages API
    AnthropicMessage,
    CacheControl,
    CitationConfig,
    ContentBlock,
    DocumentSource,
    ImageSource,
    Message,
    ProxyRequest,
    ResponseFormat,
    ResponseFormatType,
    SystemContentBlock,
    TextBlockResponse,
    ThinkingBlockResponse,
    ThinkingConfig,
    ToolChoice,
    ToolChoiceAny,
    ToolChoiceAuto,
    ToolChoiceNone,
    ToolChoiceTool,
    ToolDefinition,
    ToolResultContentBlock,
    ToolUseBlockResponse,
    UsageResponse,
)

# User models
from src.schemas.users import (
    CreateUserRequest,
    CreateUserResponse,
    DeleteAccountRequest,
    DeleteAccountResponse,
    UserProfileResponse,
    UserProfileUpdate,
    UserRegistrationRequest,
    UserRegistrationResponse,
)

__all__ = [
    # Common
    "AuthMethod",
    "PaymentMethod",
    "SubscriptionStatus",
    "PlanType",
    # Auth
    "PrivySignupRequest",
    "PrivySigninRequest",
    "PrivyAuthRequest",
    "PrivyAuthResponse",
    # Users
    "UserRegistrationRequest",
    "UserRegistrationResponse",
    "UserProfileResponse",
    # API Keys
    "CreateApiKeyRequest",
    "ApiKeyResponse",
    "UpdateApiKeyRequest",
    # Payments
    "PaymentStatus",
    "PaymentCreate",
    "PaymentResponse",
    "PaymentUpdate",
    "PaymentRecord",
    # Stripe
    "StripeCurrency",
    "StripePaymentMethodType",
    "StripeWebhookEventType",
    "CreateCheckoutSessionRequest",
    "CheckoutSessionResponse",
    "CreatePaymentIntentRequest",
    "PaymentIntentResponse",
    "StripeWebhookEvent",
    "WebhookProcessingResult",
    "CreditPackage",
    "PaymentSummary",
    # Plans
    "PlanResponse",
    "SubscriptionPlan",
    "UserPlanResponse",
    # Admin
    "UsageMetrics",
    "AdminMonitorResponse",
    "RateLimitConfig",
    # Proxy
    "ProxyRequest",
    "Message",
    "ResponseFormat",
    "ResponseFormatType",
    # Anthropic Messages API
    "CacheControl",
    "CitationConfig",
    "ContentBlock",
    "DocumentSource",
    "ImageSource",
    "AnthropicMessage",
    "SystemContentBlock",
    "ToolChoiceAuto",
    "ToolChoiceAny",
    "ToolChoiceNone",
    "ToolChoiceTool",
    "ThinkingConfig",
    "ToolDefinition",
    "UsageResponse",
    "TextBlockResponse",
    "ThinkingBlockResponse",
    "ToolUseBlockResponse",
]
