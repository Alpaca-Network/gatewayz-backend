-- Migration: Add user_memories table for cross-session AI memory
-- This table stores extracted facts and preferences from user conversations
-- to provide personalized context in future chat sessions.

-- Create the user_memories table
CREATE TABLE IF NOT EXISTS "public"."user_memories" (
    "id" SERIAL PRIMARY KEY,
    "user_id" INTEGER NOT NULL,
    "category" VARCHAR(50) NOT NULL DEFAULT 'general',
    "content" TEXT NOT NULL,
    "source_session_id" INTEGER,
    "confidence" NUMERIC(3,2) DEFAULT 0.80,
    "is_active" BOOLEAN DEFAULT TRUE,
    "access_count" INTEGER DEFAULT 0,
    "last_accessed_at" TIMESTAMP WITH TIME ZONE,
    "created_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    "updated_at" TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Add foreign key constraints
ALTER TABLE "public"."user_memories"
    ADD CONSTRAINT "user_memories_user_id_fkey"
    FOREIGN KEY ("user_id")
    REFERENCES "public"."users"("id")
    ON DELETE CASCADE;

ALTER TABLE "public"."user_memories"
    ADD CONSTRAINT "user_memories_source_session_id_fkey"
    FOREIGN KEY ("source_session_id")
    REFERENCES "public"."chat_sessions"("id")
    ON DELETE SET NULL;

-- Add category constraint
ALTER TABLE "public"."user_memories"
    ADD CONSTRAINT "user_memories_category_check"
    CHECK (category IN ('preference', 'context', 'instruction', 'fact', 'name', 'project', 'general'));

-- Create indexes for performance
CREATE INDEX "idx_user_memories_user_id" ON "public"."user_memories"("user_id");
CREATE INDEX "idx_user_memories_user_active" ON "public"."user_memories"("user_id", "is_active");
CREATE INDEX "idx_user_memories_category" ON "public"."user_memories"("user_id", "category");
CREATE INDEX "idx_user_memories_created" ON "public"."user_memories"("user_id", "created_at" DESC);
CREATE INDEX "idx_user_memories_accessed" ON "public"."user_memories"("user_id", "last_accessed_at" DESC NULLS LAST);

-- Add trigger for auto-updating updated_at
CREATE TRIGGER "update_user_memories_updated_at"
    BEFORE UPDATE ON "public"."user_memories"
    FOR EACH ROW
    EXECUTE FUNCTION "public"."update_updated_at_column"();

-- Set table ownership
ALTER TABLE "public"."user_memories" OWNER TO "postgres";

-- Add table and column comments
COMMENT ON TABLE "public"."user_memories" IS 'Stores extracted user facts and preferences for cross-session AI memory';
COMMENT ON COLUMN "public"."user_memories"."category" IS 'Type of memory: preference, context, instruction, fact, name, project, general';
COMMENT ON COLUMN "public"."user_memories"."confidence" IS 'Confidence score 0.00-1.00 of memory extraction accuracy';
COMMENT ON COLUMN "public"."user_memories"."access_count" IS 'Number of times this memory has been used in chat context';
COMMENT ON COLUMN "public"."user_memories"."source_session_id" IS 'The chat session this memory was extracted from';

-- Enable Row Level Security
ALTER TABLE "public"."user_memories" ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users can only access their own memories
CREATE POLICY "user_memories_select_own" ON "public"."user_memories"
    FOR SELECT
    USING (auth.uid()::text = user_id::text OR auth.role() = 'service_role');

CREATE POLICY "user_memories_insert_own" ON "public"."user_memories"
    FOR INSERT
    WITH CHECK (auth.uid()::text = user_id::text OR auth.role() = 'service_role');

CREATE POLICY "user_memories_update_own" ON "public"."user_memories"
    FOR UPDATE
    USING (auth.uid()::text = user_id::text OR auth.role() = 'service_role');

CREATE POLICY "user_memories_delete_own" ON "public"."user_memories"
    FOR DELETE
    USING (auth.uid()::text = user_id::text OR auth.role() = 'service_role');

-- Grant permissions
GRANT ALL ON TABLE "public"."user_memories" TO "anon";
GRANT ALL ON TABLE "public"."user_memories" TO "authenticated";
GRANT ALL ON TABLE "public"."user_memories" TO "service_role";
GRANT USAGE, SELECT ON SEQUENCE "public"."user_memories_id_seq" TO "anon";
GRANT USAGE, SELECT ON SEQUENCE "public"."user_memories_id_seq" TO "authenticated";
GRANT USAGE, SELECT ON SEQUENCE "public"."user_memories_id_seq" TO "service_role";
