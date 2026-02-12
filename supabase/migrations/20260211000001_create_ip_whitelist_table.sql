-- IP Whitelist Table
-- Allows administrators to whitelist specific IPs or CIDR ranges to bypass rate limiting
-- even during velocity mode. Useful for known good actors, internal services, etc.

CREATE TABLE IF NOT EXISTS public.ip_whitelist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- IP address or CIDR range (e.g., "203.0.113.5" or "203.0.113.0/24")
    ip_address TEXT NOT NULL,

    -- Optional: Associate with specific user (NULL = global whitelist)
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,

    -- Reason for whitelisting (for audit purposes)
    reason TEXT NOT NULL,

    -- Admin who created this whitelist entry
    created_by UUID NOT NULL REFERENCES auth.users(id),

    -- Enable/disable without deleting
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    -- Optional expiration date (NULL = never expires)
    expires_at TIMESTAMPTZ,

    -- Metadata for additional context
    metadata JSONB DEFAULT '{}'::jsonb,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Ensure unique IP addresses (but allow same IP for different users)
    CONSTRAINT unique_ip_whitelist UNIQUE(ip_address, user_id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_ip_whitelist_ip_address ON public.ip_whitelist(ip_address);
CREATE INDEX IF NOT EXISTS idx_ip_whitelist_user_id ON public.ip_whitelist(user_id);
CREATE INDEX IF NOT EXISTS idx_ip_whitelist_enabled ON public.ip_whitelist(enabled);
CREATE INDEX IF NOT EXISTS idx_ip_whitelist_expires_at ON public.ip_whitelist(expires_at);

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_ip_whitelist_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_ip_whitelist_updated_at_trigger
    BEFORE UPDATE ON public.ip_whitelist
    FOR EACH ROW
    EXECUTE FUNCTION update_ip_whitelist_updated_at();

-- Row Level Security (RLS)
ALTER TABLE public.ip_whitelist ENABLE ROW LEVEL SECURITY;

-- Policy: Admins can read all whitelist entries
CREATE POLICY "Admins can read all IP whitelist entries"
    ON public.ip_whitelist
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE users.id = auth.uid()
            AND users.role IN ('admin', 'superadmin')
        )
    );

-- Policy: Admins can insert whitelist entries
CREATE POLICY "Admins can insert IP whitelist entries"
    ON public.ip_whitelist
    FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE users.id = auth.uid()
            AND users.role IN ('admin', 'superadmin')
        )
    );

-- Policy: Admins can update whitelist entries
CREATE POLICY "Admins can update IP whitelist entries"
    ON public.ip_whitelist
    FOR UPDATE
    USING (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE users.id = auth.uid()
            AND users.role IN ('admin', 'superadmin')
        )
    );

-- Policy: Admins can delete whitelist entries
CREATE POLICY "Admins can delete IP whitelist entries"
    ON public.ip_whitelist
    FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE users.id = auth.uid()
            AND users.role IN ('admin', 'superadmin')
        )
    );

-- Comment on table
COMMENT ON TABLE public.ip_whitelist IS 'IP addresses or CIDR ranges whitelisted to bypass rate limiting';
COMMENT ON COLUMN public.ip_whitelist.ip_address IS 'IP address or CIDR range (e.g., "203.0.113.5" or "203.0.113.0/24")';
COMMENT ON COLUMN public.ip_whitelist.user_id IS 'Optional: Associate whitelist with specific user (NULL = global)';
COMMENT ON COLUMN public.ip_whitelist.reason IS 'Reason for whitelisting (for audit purposes)';
COMMENT ON COLUMN public.ip_whitelist.created_by IS 'Admin who created this whitelist entry';
COMMENT ON COLUMN public.ip_whitelist.enabled IS 'Enable/disable without deleting the entry';
COMMENT ON COLUMN public.ip_whitelist.expires_at IS 'Optional expiration date (NULL = never expires)';
