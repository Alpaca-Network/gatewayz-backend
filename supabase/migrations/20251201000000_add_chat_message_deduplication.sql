-- Migration: Add message deduplication support
-- Date: 2025-12-01
-- Purpose: Prevent duplicate chat messages and improve message ordering

-- Add sequence number column for better message ordering
ALTER TABLE chat_messages
ADD COLUMN IF NOT EXISTS sequence_number INTEGER;

-- Create index on sequence number for efficient ordering
CREATE INDEX IF NOT EXISTS idx_chat_messages_sequence
ON chat_messages(session_id, sequence_number);

-- Add partial unique index to prevent exact duplicates within recent time window
-- This prevents accidental duplicate saves (e.g., from retries) while still allowing
-- the same content in different conversation contexts or after sufficient time
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_messages_unique_recent
ON chat_messages (session_id, role, md5(content), DATE_TRUNC('minute', created_at))
WHERE created_at > NOW() - INTERVAL '5 minutes';

-- Create index for duplicate detection queries
CREATE INDEX IF NOT EXISTS idx_chat_messages_duplicate_check
ON chat_messages (session_id, role, created_at DESC)
WHERE created_at > NOW() - INTERVAL '1 hour';

-- Add comment to explain the deduplication strategy
COMMENT ON INDEX idx_chat_messages_unique_recent IS
'Prevents duplicate messages within 5-minute window. Uses MD5 hash of content to work around PostgreSQL text comparison limits in unique indexes.';

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
