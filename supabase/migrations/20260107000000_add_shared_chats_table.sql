-- Migration: Add shared_chats table for chat link sharing functionality
-- This table stores shared chat links with unique tokens for public access

-- Create the shared_chats table
CREATE TABLE IF NOT EXISTS "public"."shared_chats" (
    "id" SERIAL PRIMARY KEY,
    "session_id" INTEGER NOT NULL,
    "share_token" VARCHAR(64) NOT NULL UNIQUE,
    "created_by_user_id" INTEGER NOT NULL,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "expires_at" TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    "view_count" INTEGER DEFAULT 0,
    "last_viewed_at" TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    "is_active" BOOLEAN DEFAULT TRUE,
    CONSTRAINT "fk_shared_chats_session_id" FOREIGN KEY ("session_id")
        REFERENCES "public"."chat_sessions"("id") ON DELETE CASCADE,
    CONSTRAINT "fk_shared_chats_user_id" FOREIGN KEY ("created_by_user_id")
        REFERENCES "public"."users"("id") ON DELETE CASCADE
);

-- Add comments for documentation
COMMENT ON TABLE "public"."shared_chats" IS 'Stores shareable links for chat sessions';
COMMENT ON COLUMN "public"."shared_chats"."share_token" IS 'Unique token used in share URLs';
COMMENT ON COLUMN "public"."shared_chats"."expires_at" IS 'Optional expiration date for the share link';
COMMENT ON COLUMN "public"."shared_chats"."view_count" IS 'Number of times this shared link has been viewed';
COMMENT ON COLUMN "public"."shared_chats"."is_active" IS 'Soft delete flag - FALSE means share link is disabled';

-- Create indexes for efficient lookups
CREATE INDEX IF NOT EXISTS "idx_shared_chats_share_token" ON "public"."shared_chats" ("share_token");
CREATE INDEX IF NOT EXISTS "idx_shared_chats_session_id" ON "public"."shared_chats" ("session_id");
CREATE INDEX IF NOT EXISTS "idx_shared_chats_user_id" ON "public"."shared_chats" ("created_by_user_id");
CREATE INDEX IF NOT EXISTS "idx_shared_chats_is_active" ON "public"."shared_chats" ("is_active") WHERE "is_active" = TRUE;

-- Enable Row Level Security
ALTER TABLE "public"."shared_chats" ENABLE ROW LEVEL SECURITY;

-- RLS Policies

-- Policy: Users can view their own share links
CREATE POLICY "Users can view own share links"
    ON "public"."shared_chats"
    FOR SELECT
    TO authenticated
    USING (created_by_user_id = (current_setting('request.jwt.claims', true)::json->>'sub')::integer);

-- Policy: Users can create share links for their own sessions
CREATE POLICY "Users can create share links"
    ON "public"."shared_chats"
    FOR INSERT
    TO authenticated
    WITH CHECK (
        created_by_user_id = (current_setting('request.jwt.claims', true)::json->>'sub')::integer
        AND EXISTS (
            SELECT 1 FROM "public"."chat_sessions"
            WHERE id = session_id
            AND user_id = created_by_user_id
        )
    );

-- Policy: Users can update/delete their own share links
CREATE POLICY "Users can manage own share links"
    ON "public"."shared_chats"
    FOR UPDATE
    TO authenticated
    USING (created_by_user_id = (current_setting('request.jwt.claims', true)::json->>'sub')::integer);

CREATE POLICY "Users can delete own share links"
    ON "public"."shared_chats"
    FOR DELETE
    TO authenticated
    USING (created_by_user_id = (current_setting('request.jwt.claims', true)::json->>'sub')::integer);

-- Grant permissions
GRANT ALL ON TABLE "public"."shared_chats" TO "anon";
GRANT ALL ON TABLE "public"."shared_chats" TO "authenticated";
GRANT ALL ON TABLE "public"."shared_chats" TO "service_role";

GRANT ALL ON SEQUENCE "public"."shared_chats_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."shared_chats_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."shared_chats_id_seq" TO "service_role";
