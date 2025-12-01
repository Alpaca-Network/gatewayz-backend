-- Migration: Add message deduplication support
-- Date: 2025-12-01
-- Purpose: Prevent duplicate chat messages and improve message ordering

-- Add sequence number column for better message ordering
ALTER TABLE chat_messages
ADD COLUMN IF NOT EXISTS sequence_number INTEGER;

-- Create index on sequence number for efficient ordering
CREATE INDEX IF NOT EXISTS idx_chat_messages_sequence
ON chat_messages(session_id, sequence_number);

-- Add composite index to help with duplicate detection
-- Note: We can't use a partial index with NOW() since it's not IMMUTABLE
-- Instead, we rely on application-level duplicate detection within time windows
CREATE INDEX IF NOT EXISTS idx_chat_messages_duplicate_check
ON chat_messages (session_id, role, content, created_at DESC);

-- Add index on session_id and created_at for efficient history queries
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
ON chat_messages (session_id, created_at DESC);

-- Backfill sequence numbers for existing messages
-- Order by created_at to maintain chronological sequence
WITH numbered_messages AS (
  SELECT
    id,
    ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY created_at, id) as seq_num
  FROM chat_messages
  WHERE sequence_number IS NULL
)
UPDATE chat_messages cm
SET sequence_number = nm.seq_num
FROM numbered_messages nm
WHERE cm.id = nm.id;

-- Create function to auto-assign sequence numbers on insert
CREATE OR REPLACE FUNCTION assign_chat_message_sequence()
RETURNS TRIGGER AS $$
BEGIN
  -- Auto-assign sequence number if not provided
  IF NEW.sequence_number IS NULL THEN
    SELECT COALESCE(MAX(sequence_number), 0) + 1
    INTO NEW.sequence_number
    FROM chat_messages
    WHERE session_id = NEW.session_id;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to auto-assign sequence numbers
DROP TRIGGER IF EXISTS trg_assign_chat_message_sequence ON chat_messages;
CREATE TRIGGER trg_assign_chat_message_sequence
  BEFORE INSERT ON chat_messages
  FOR EACH ROW
  EXECUTE FUNCTION assign_chat_message_sequence();

-- Add statistics for query optimizer
ANALYZE chat_messages;
