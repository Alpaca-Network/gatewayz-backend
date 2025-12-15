-- =====================================================
-- Message Feedback System Migration
-- =====================================================
-- Creates the message_feedback table for tracking user feedback
-- on chat assistant responses (thumbs up, thumbs down, etc.)
-- =====================================================

-- Create feedback type enum
DO $$ BEGIN
    CREATE TYPE feedback_type AS ENUM (
        'thumbs_up',
        'thumbs_down',
        'regenerate'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create message_feedback table
CREATE TABLE IF NOT EXISTS public.message_feedback (
    id BIGSERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES public.chat_sessions(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES public.chat_messages(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    feedback_type feedback_type NOT NULL,
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),
    comment TEXT,
    model VARCHAR(255),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_message_feedback_user_id ON public.message_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_message_feedback_session_id ON public.message_feedback(session_id);
CREATE INDEX IF NOT EXISTS idx_message_feedback_message_id ON public.message_feedback(message_id);
CREATE INDEX IF NOT EXISTS idx_message_feedback_type ON public.message_feedback(feedback_type);
CREATE INDEX IF NOT EXISTS idx_message_feedback_created_at ON public.message_feedback(created_at);
CREATE INDEX IF NOT EXISTS idx_message_feedback_model ON public.message_feedback(model);

-- Composite indexes for common queries
CREATE INDEX IF NOT EXISTS idx_message_feedback_user_type ON public.message_feedback(user_id, feedback_type);
CREATE INDEX IF NOT EXISTS idx_message_feedback_session_type ON public.message_feedback(session_id, feedback_type);
CREATE INDEX IF NOT EXISTS idx_message_feedback_model_type ON public.message_feedback(model, feedback_type);

-- Enable Row Level Security
ALTER TABLE public.message_feedback ENABLE ROW LEVEL SECURITY;

-- Create RLS policies for message_feedback
CREATE POLICY "Users can view their own feedback" ON public.message_feedback
    FOR SELECT USING (auth.uid()::text = user_id::text);

CREATE POLICY "Users can insert their own feedback" ON public.message_feedback
    FOR INSERT WITH CHECK (auth.uid()::text = user_id::text);

CREATE POLICY "Users can update their own feedback" ON public.message_feedback
    FOR UPDATE USING (auth.uid()::text = user_id::text);

CREATE POLICY "Users can delete their own feedback" ON public.message_feedback
    FOR DELETE USING (auth.uid()::text = user_id::text);

CREATE POLICY "Service role can manage all feedback" ON public.message_feedback
    FOR ALL USING (auth.role() = 'service_role');

-- Add comments
COMMENT ON TABLE public.message_feedback IS 'Stores user feedback on chat assistant responses';
COMMENT ON COLUMN public.message_feedback.session_id IS 'Optional reference to the chat session';
COMMENT ON COLUMN public.message_feedback.message_id IS 'Optional reference to the specific message';
COMMENT ON COLUMN public.message_feedback.feedback_type IS 'Type of feedback (thumbs_up, thumbs_down, regenerate)';
COMMENT ON COLUMN public.message_feedback.rating IS 'Optional 1-5 star rating';
COMMENT ON COLUMN public.message_feedback.comment IS 'Optional text feedback from the user';
COMMENT ON COLUMN public.message_feedback.model IS 'The model that generated the response';
COMMENT ON COLUMN public.message_feedback.metadata IS 'Additional context (response content, prompt, etc.)';

-- Create function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_message_feedback_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for message_feedback table
DROP TRIGGER IF EXISTS trigger_update_message_feedback_updated_at ON public.message_feedback;
CREATE TRIGGER trigger_update_message_feedback_updated_at
    BEFORE UPDATE ON public.message_feedback
    FOR EACH ROW
    EXECUTE FUNCTION update_message_feedback_updated_at();

-- =====================================================
-- Migration Complete
-- =====================================================

-- Verify table was created
DO $$
BEGIN
  IF EXISTS (
    SELECT FROM information_schema.tables
    WHERE table_schema = 'public'
    AND table_name = 'message_feedback'
  ) THEN
    RAISE NOTICE '✓ Message feedback table created successfully';
    RAISE NOTICE '✓ Table: message_feedback';
    RAISE NOTICE '✓ Enum: feedback_type';
    RAISE NOTICE '✓ RLS enabled with proper policies';
  ELSE
    RAISE EXCEPTION 'Failed to create message_feedback table';
  END IF;
END $$;
