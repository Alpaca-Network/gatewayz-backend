-- BYOK (bring-your-own-key) storage — Phase 5 of the direct-supply pivot.
-- Stores a customer's own upstream provider API key, encrypted at rest with
-- the Fernet keyring (KEY_VERSION / KEYRING_<version>). The plaintext key is
-- only ever recovered server-side to make the upstream provider call; it is
-- never returned by any API. See docs/BUSINESS_PIVOT_DIRECT_SUPPLY.md.

CREATE TABLE IF NOT EXISTS user_provider_keys (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider_slug TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    key_version INTEGER NOT NULL DEFAULT 1,
    key_last4 TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One key per (user, provider); re-adding updates in place.
    CONSTRAINT uq_user_provider UNIQUE (user_id, provider_slug)
);

CREATE INDEX IF NOT EXISTS idx_user_provider_keys_user
    ON user_provider_keys (user_id)
    WHERE is_active;

-- RLS: a user can only see/modify their own BYOK keys.
ALTER TABLE user_provider_keys ENABLE ROW LEVEL SECURITY;
