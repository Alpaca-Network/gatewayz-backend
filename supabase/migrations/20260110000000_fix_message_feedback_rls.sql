-- =====================================================
-- Fix Message Feedback RLS Policies
-- =====================================================
-- The original RLS policies used auth.uid() which only works with
-- Supabase Auth. Our backend uses a custom authentication system
-- with API keys and integer user IDs, so auth.uid() returns NULL
-- and all INSERT operations are blocked.
--
-- This migration disables RLS on the message_feedback table since
-- authorization is handled at the application level in the backend
-- (the API routes verify user ownership before allowing operations).
-- =====================================================

-- Drop existing RLS policies that don't work with our auth system
DROP POLICY IF EXISTS "Users can view their own feedback" ON public.message_feedback;
DROP POLICY IF EXISTS "Users can insert their own feedback" ON public.message_feedback;
DROP POLICY IF EXISTS "Users can update their own feedback" ON public.message_feedback;
DROP POLICY IF EXISTS "Users can delete their own feedback" ON public.message_feedback;
DROP POLICY IF EXISTS "Service role can manage all feedback" ON public.message_feedback;

-- Disable RLS on the message_feedback table
-- Authorization is enforced at the application layer in the backend
ALTER TABLE public.message_feedback DISABLE ROW LEVEL SECURITY;

-- =====================================================
-- Migration Complete
-- =====================================================

-- Verify RLS is disabled
DO $$
DECLARE
    rls_enabled boolean;
BEGIN
    SELECT relrowsecurity INTO rls_enabled
    FROM pg_class
    WHERE relname = 'message_feedback'
    AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public');

    IF rls_enabled = false THEN
        RAISE NOTICE '✓ RLS disabled on message_feedback table';
        RAISE NOTICE '✓ Feedback submission will now work for authenticated users';
    ELSE
        RAISE EXCEPTION 'Failed to disable RLS on message_feedback table';
    END IF;
END $$;

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';
