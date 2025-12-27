-- Migration: Add message deduplication support
-- Date: 2025-12-01
-- Purpose: Prevent duplicate chat messages and improve message ordering

-- Add sequence number column for better message ordering
ALTER TABLE chat_messages
ADD COLUMN IF NOT EXISTS sequence_number INTEGER;

-- Create index on sequence number for efficient ordering
CREATE INDEX IF NOT EXISTS idx_chat_messages_sequence
ON chat_messages(session_id, sequence_number);

-- Add content hash column for efficient duplicate detection
-- Using MD5 hash to avoid index size limitations (content can be very large)
ALTER TABLE chat_messages
ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- Update existing rows with content hashes
UPDATE chat_messages
SET content_hash = md5(content)
WHERE content_hash IS NULL;

-- Create index for duplicate detection using hash
-- This avoids the 8KB index size limit that occurs when indexing large TEXT columns
CREATE INDEX IF NOT EXISTS idx_chat_messages_duplicate_check
ON chat_messages (session_id, role, content_hash, created_at DESC);

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

-- Create function to auto-assign sequence numbers and content hash on insert
CREATE OR REPLACE FUNCTION assign_chat_message_sequence()
RETURNS TRIGGER AS $$
DECLARE
  v_max_seq INTEGER;
BEGIN
  -- Auto-assign content hash if not provided
  IF NEW.content_hash IS NULL THEN
    NEW.content_hash := md5(NEW.content);
  END IF;

  -- Auto-assign sequence number if not provided
  IF NEW.sequence_number IS NULL THEN
    -- Lock the session row to prevent concurrent inserts from racing
    -- This is more efficient than locking all message rows
    PERFORM 1 FROM chat_sessions WHERE id = NEW.session_id FOR UPDATE;

    -- Now safely get the max sequence number for this session
    SELECT COALESCE(MAX(sequence_number), 0)
    INTO v_max_seq
    FROM chat_messages
    WHERE session_id = NEW.session_id;

    -- Assign next sequence number
    NEW.sequence_number := v_max_seq + 1;
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
