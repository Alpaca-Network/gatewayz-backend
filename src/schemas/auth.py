from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator

from src.schemas.common import AuthMethod


class PrivyLinkedAccount(BaseModel):
    type: str
    subject: str | None = None
    email: str | None = None
    address: str | None = None
    name: str | None = None
    verified_at: int | None = None
    first_verified_at: int | None = None
    latest_verified_at: int | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        """Validate account type is a known provider"""
        valid_types = {
            "email",
            "google_oauth",
            "github",
            "apple_oauth",
            "discord",
            "farcaster",
        }
        if v not in valid_types:
            raise ValueError(f"Account type must be one of {valid_types}, got {v}")
        return v

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
    token: str
    email: str | None = None  # Optional top-level email field for frontend to send
    privy_access_token: str | None = None
    refresh_token: str | None = None
    session_update_action: str | None = None
    is_new_user: bool | None = None
    referral_code: str | None = None  # Referral code if user signed up with one
    environment_tag: str | None = "live"  # Environment tag for API keys (live, test, development)
    auto_create_api_key: bool | None = True  # Whether to automatically create API keys for new users

    @field_validator("token")
    @classmethod
    def validate_token(cls, v):
        """Validate token is not empty"""
        if not v or not v.strip():
            raise ValueError("Token cannot be empty")
        return v

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
    credits: float | None = None
    timestamp: datetime | None = None
    subscription_status: str | None = None
    tier: str | None = None
    tier_display_name: str | None = None
    trial_expires_at: str | None = None
    subscription_end_date: int | None = None
