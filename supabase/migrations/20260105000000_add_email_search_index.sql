-- Migration: Add trigram index for fast email partial search
-- Created: 2026-01-05
-- Purpose: Enable fast ILIKE '%pattern%' searches on email column for 40K+ user dataset

-- Enable the pg_trgm extension for trigram-based pattern matching
-- This extension provides GIN and GiST index operator classes that allow
-- you to create indexes that can be used for similarity searching
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Create a GIN (Generalized Inverted Index) on the email column
-- This index specifically supports the ILIKE operator with wildcards on both sides
-- e.g., email ILIKE '%radar%' will use this index
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_email_trgm
ON users USING gin (email gin_trgm_ops);

-- Update table statistics to help the query planner make better decisions
ANALYZE users;

-- Expected performance improvement:
-- Before: Full table scan on 40K+ rows = 10-30s (often times out)
-- After: Index scan = 50-200ms (should work reliably)
