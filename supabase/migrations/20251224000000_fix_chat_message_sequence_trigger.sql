-- Migration: Fix chat message sequence trigger for PgBouncer compatibility
-- Date: 2025-12-24
-- Purpose: Remove FOR UPDATE clause that causes errors with stateless connection pooling
--
-- Issue: "UnknownError: Attempt to get a record from database without an in-progress transaction"
-- Root Cause: FOR UPDATE requires explicit transaction context, which is not available
-- in Supabase's stateless PostgREST + PgBouncer environment.
--
-- Solution: Replace FOR UPDATE with pg_advisory_xact_lock for session-level locking
-- that works properly with connection pooling and trigger contexts.

-- Create fixed function that uses advisory locks instead of FOR UPDATE
CREATE OR REPLACE FUNCTION assign_chat_message_sequence()
RETURNS TRIGGER AS $$
BEGIN
  -- Auto-assign sequence number if not provided
  IF NEW.sequence_number IS NULL THEN
    -- Use advisory lock on session_id hash for concurrency control
    -- pg_advisory_xact_lock is transaction-scoped and works with PgBouncer
    -- The lock is automatically released when the transaction commits/rollbacks
    PERFORM pg_advisory_xact_lock(hashtext(NEW.session_id::text));
    
    -- Get next sequence number for this session
    SELECT COALESCE(MAX(sequence_number), 0) + 1
    INTO NEW.sequence_number
    FROM chat_messages
    WHERE session_id = NEW.session_id;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Note: The trigger already exists from the previous migration,
-- so we don't need to recreate it. The function replacement takes effect immediately.

-- Add a comment explaining the fix
COMMENT ON FUNCTION assign_chat_message_sequence() IS 
  'Auto-assigns sequence numbers to chat messages. Uses pg_advisory_xact_lock for concurrency control instead of FOR UPDATE to ensure compatibility with PgBouncer connection pooling.';
