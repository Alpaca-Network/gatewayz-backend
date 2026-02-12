-- Create velocity_mode_events table for tracking velocity mode activations
-- This table helps monitor when and why the velocity protection mode is triggered

CREATE TABLE IF NOT EXISTS public.velocity_mode_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Timestamp when velocity mode was activated
    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Timestamp when velocity mode was deactivated (NULL if still active)
    deactivated_at TIMESTAMPTZ,

    -- Error metrics that triggered activation
    error_rate DECIMAL(5,4) NOT NULL, -- e.g., 0.1234 for 12.34%
    total_requests INTEGER NOT NULL,
    error_count INTEGER NOT NULL,

    -- Breakdown of errors by status code
    error_details JSONB DEFAULT '{}'::jsonb,
    -- Example: {"499": 45, "500": 12, "502": 8, "503": 15}

    -- Duration of velocity mode in seconds
    duration_seconds INTEGER,

    -- Trigger reason/context
    trigger_reason TEXT DEFAULT 'error_threshold_exceeded',

    -- Additional metadata
    metadata JSONB DEFAULT '{}'::jsonb,
    -- Can store: affected endpoints, top error sources, etc.

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_velocity_events_activated_at
    ON public.velocity_mode_events(activated_at DESC);

CREATE INDEX IF NOT EXISTS idx_velocity_events_deactivated_at
    ON public.velocity_mode_events(deactivated_at DESC);

CREATE INDEX IF NOT EXISTS idx_velocity_events_error_rate
    ON public.velocity_mode_events(error_rate DESC);

CREATE INDEX IF NOT EXISTS idx_velocity_events_trigger_reason
    ON public.velocity_mode_events(trigger_reason);

-- Add comment to table
COMMENT ON TABLE public.velocity_mode_events IS
    'Tracks velocity mode activations for rate limiting and system protection monitoring';

-- Add comments to columns
COMMENT ON COLUMN public.velocity_mode_events.error_rate IS
    'Error rate that triggered velocity mode (e.g., 0.10 for 10%)';

COMMENT ON COLUMN public.velocity_mode_events.error_details IS
    'JSON object with breakdown of errors by HTTP status code';

COMMENT ON COLUMN public.velocity_mode_events.duration_seconds IS
    'How long velocity mode was active (calculated when deactivated)';

-- Enable Row Level Security (if needed)
ALTER TABLE public.velocity_mode_events ENABLE ROW LEVEL SECURITY;

-- Create policy for admin access only
CREATE POLICY "Admin users can view velocity mode events"
    ON public.velocity_mode_events
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM public.users
            WHERE users.id = auth.uid()
            AND users.role IN ('admin', 'super_admin')
        )
    );

-- Create policy for system to insert events
CREATE POLICY "System can insert velocity mode events"
    ON public.velocity_mode_events
    FOR INSERT
    WITH CHECK (true);

-- Create policy for system to update events (for deactivation)
CREATE POLICY "System can update velocity mode events"
    ON public.velocity_mode_events
    FOR UPDATE
    USING (true);

-- Grant necessary permissions
GRANT SELECT, INSERT, UPDATE ON public.velocity_mode_events TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.velocity_mode_events TO service_role;
