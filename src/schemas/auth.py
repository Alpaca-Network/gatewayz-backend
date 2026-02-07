from datetime import datetime

from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator

from src.schemas.common import AuthMethod


class PrivyLinkedAccount(BaseModel):
    type: str
    subject: str | None = None
    email: str | None = None
    address: str | None = None
    name: str | None = None
    # Phone number for SMS/phone auth - Privy sends 'phoneNumber' in camelCase
    # Using AliasChoices to accept both snake_case and camelCase formats
    phone_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices("phone_number", "phoneNumber"),
    )
    verified_at: int | None = None
    first_verified_at: int | None = None
    latest_verified_at: int | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        """Normalize and validate account type"""
        # Normalize common Privy account type variations
        type_mappings = {
            "github_oauth": "github",
            "sms": "phone",  # Privy sends 'sms' but we use 'phone'
            "twitter_oauth": "twitter",
            "discord_oauth": "discord",
        }
        normalized = type_mappings.get(v, v)

        # Accept all known and likely Privy account types
        # Being permissive here since unknown types shouldn't break auth
        valid_types = {
            "email",
            "phone",
            "google_oauth",
            "github",
            "apple_oauth",
            "discord",
            "farcaster",
            "twitter",
            "passkey",
            "smart_wallet",
            "cross_app",
            "wallet",  # Allow wallet type to pass through (filtered in frontend but be safe)
        }

        # Log but don't reject unknown types - just pass them through
        if normalized not in valid_types:
            import logging
            logging.getLogger(__name__).warning(f"Unknown linked account type: {v} (normalized: {normalized})")

        return normalized

    @field_validator("email", "address")
    @classmethod
    def validate_email_format(cls, v):
        """Validate email format if provided"""
        if v is not None and "@" not in v:
            raise ValueError("Invalid email format")
        return v


class PrivyUserData(BaseModel):
    id: str
    created_at: int
    linked_accounts: list[PrivyLinkedAccount] = []
    mfa_methods: list[str] = []
    has_accepted_terms: bool = False
    is_guest: bool = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, v):
        """Validate privy user ID is not empty"""
        if not v or not v.strip():
            raise ValueError("Privy user ID cannot be empty")
        return v


class PrivySignupRequest(BaseModel):
    privy_user_id: str
    auth_method: AuthMethod
    email: EmailStr | None = None
    username: str | None = None
    display_name: str | None = None
    gmail_address: EmailStr | None = None
    github_username: str | None = None


class PrivySigninRequest(BaseModel):
    privy_user_id: str
    auth_method: AuthMethod


class PrivyAuthRequest(BaseModel):
    user: PrivyUserData
    token: str | None = None  # Privy access token (optional - not currently used for validation)
    email: str | None = None  # Optional top-level email field for frontend to send
    privy_access_token: str | None = None
    refresh_token: str | None = None
    session_update_action: str | None = None
    is_new_user: bool | None = None
    referral_code: str | None = None  # Referral code if user signed up with one
    environment_tag: str | None = "live"  # Environment tag for API keys (live, test, development)
    auto_create_api_key: bool | None = True  # Whether to automatically create API keys for new users

    @field_validator("environment_tag")
    @classmethod
    def validate_environment_tag(cls, v):
        """Validate environment tag is one of allowed values"""
        if v is None:
            return "live"
        valid_tags = {"live", "test", "development"}
        if v not in valid_tags:
            raise ValueError(f"Environment tag must be one of {valid_tags}, got {v}")
        return v


class PrivyAuthResponse(BaseModel):
    success: bool
    message: str
    user_id: int | None = None
    api_key: str | None = None
    auth_method: AuthMethod | None = None
    privy_user_id: str | None = None
    is_new_user: bool | None = None
    display_name: str | None = None
    email: str | None = None
    phone_number: str | None = None  # Phone number for users who authenticated via SMS
    credits: float | None = None
    timestamp: datetime | None = None
    subscription_status: str | None = None
    tier: str | None = None
    tier_display_name: str | None = None
    trial_expires_at: str | None = None
    subscription_end_date: int | None = None
    # Tiered credit fields (in cents for frontend consistency)
    subscription_allowance: int | None = None  # Monthly subscription allowance
    purchased_credits: int | None = None  # One-time purchased credits
    total_credits: int | None = None  # Sum of subscription_allowance + purchased_credits
    allowance_reset_date: str | None = None  # When allowance was last reset
