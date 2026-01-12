-- =====================================================
-- Admin Dashboard Notifications Migration
-- =====================================================
-- Creates the admin_notifications table for in-app notifications
-- in the admin dashboard (separate from email notifications)
-- =====================================================

-- Create admin notification types enum
CREATE TYPE admin_notification_type AS ENUM (
    'info',
    'warning',
    'error',
    'success'
);

-- Create admin notification categories enum
CREATE TYPE admin_notification_category AS ENUM (
    'user',
    'payment',
    'health',
    'system',
    'security'
);

-- Create admin_notifications table
CREATE TABLE IF NOT EXISTS public.admin_notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    type admin_notification_type NOT NULL DEFAULT 'info',
    category admin_notification_category,
    is_read BOOLEAN DEFAULT FALSE NOT NULL,
    link VARCHAR(255),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    read_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_admin_notifications_user_id ON public.admin_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_admin_notifications_user_unread ON public.admin_notifications(user_id, is_read) WHERE is_read = FALSE;
CREATE INDEX IF NOT EXISTS idx_admin_notifications_user_created ON public.admin_notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_notifications_category ON public.admin_notifications(category);
CREATE INDEX IF NOT EXISTS idx_admin_notifications_expires ON public.admin_notifications(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_admin_notifications_type ON public.admin_notifications(type);

-- Enable Row Level Security
ALTER TABLE public.admin_notifications ENABLE ROW LEVEL SECURITY;

-- Create RLS policies for admin_notifications
-- Users can only view their own notifications
CREATE POLICY "Users can view their own admin notifications" ON public.admin_notifications
    FOR SELECT USING (auth.uid()::text = user_id::text);

-- Users can update their own notifications (mark as read)
CREATE POLICY "Users can update their own admin notifications" ON public.admin_notifications
    FOR UPDATE USING (auth.uid()::text = user_id::text);

-- Users can delete their own notifications
CREATE POLICY "Users can delete their own admin notifications" ON public.admin_notifications
    FOR DELETE USING (auth.uid()::text = user_id::text);

-- Service role can manage all notifications (for system-generated notifications)
CREATE POLICY "Service role can manage all admin notifications" ON public.admin_notifications
    FOR ALL USING (auth.role() = 'service_role');

-- Add table and column comments
COMMENT ON TABLE public.admin_notifications IS 'In-app notifications for the admin dashboard';
COMMENT ON COLUMN public.admin_notifications.title IS 'Short notification title';
COMMENT ON COLUMN public.admin_notifications.message IS 'Detailed notification message';
COMMENT ON COLUMN public.admin_notifications.type IS 'Visual type: info, warning, error, success';
COMMENT ON COLUMN public.admin_notifications.category IS 'Notification category for filtering';
COMMENT ON COLUMN public.admin_notifications.is_read IS 'Whether the notification has been read';
COMMENT ON COLUMN public.admin_notifications.link IS 'Optional URL to navigate to when clicked';
COMMENT ON COLUMN public.admin_notifications.metadata IS 'Additional context data as JSON';
COMMENT ON COLUMN public.admin_notifications.read_at IS 'Timestamp when notification was marked as read';
COMMENT ON COLUMN public.admin_notifications.expires_at IS 'Optional expiration timestamp for auto-cleanup';

-- Create function to delete expired notifications
CREATE OR REPLACE FUNCTION delete_expired_admin_notifications()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM public.admin_notifications
    WHERE expires_at IS NOT NULL AND expires_at < NOW();
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to cleanup expired notifications after each insert
CREATE TRIGGER cleanup_expired_admin_notifications
AFTER INSERT ON public.admin_notifications
EXECUTE FUNCTION delete_expired_admin_notifications();

-- Create helper function to get unread count for a user
CREATE OR REPLACE FUNCTION get_admin_notification_unread_count(p_user_id INTEGER)
RETURNS INTEGER AS $$
DECLARE
    unread_count INTEGER;
BEGIN
    SELECT COUNT(*)
    INTO unread_count
    FROM public.admin_notifications
    WHERE user_id = p_user_id AND is_read = FALSE;

    RETURN COALESCE(unread_count, 0);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION get_admin_notification_unread_count(INTEGER) TO authenticated;

-- Create helper function to mark all notifications as read
CREATE OR REPLACE FUNCTION mark_all_admin_notifications_read(p_user_id INTEGER)
RETURNS VOID AS $$
BEGIN
    UPDATE public.admin_notifications
    SET is_read = TRUE, read_at = NOW()
    WHERE user_id = p_user_id AND is_read = FALSE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION mark_all_admin_notifications_read(INTEGER) TO authenticated;

-- =====================================================
-- Migration Complete
-- =====================================================

-- Verify table was created
DO $$
BEGIN
  IF EXISTS (
    SELECT FROM information_schema.tables
    WHERE table_schema = 'public'
    AND table_name = 'admin_notifications'
  ) THEN
    RAISE NOTICE '✓ Admin notifications table created successfully';
    RAISE NOTICE '✓ Table: admin_notifications';
    RAISE NOTICE '✓ Enums: admin_notification_type, admin_notification_category';
    RAISE NOTICE '✓ Indexes: 6 performance indexes created';
    RAISE NOTICE '✓ RLS: Enabled with proper policies';
    RAISE NOTICE '✓ Functions: get_admin_notification_unread_count, mark_all_admin_notifications_read';
    RAISE NOTICE '✓ Trigger: Auto-cleanup for expired notifications';
  ELSE
    RAISE EXCEPTION 'Failed to create admin_notifications table';
  END IF;
END $$;
