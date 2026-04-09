CREATE TABLE IF NOT EXISTS security_audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER,
    api_key_id TEXT,
    event_type VARCHAR(50) NOT NULL,
    ip_address VARCHAR(45),
    details JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_security_audit_event_type ON security_audit_log(event_type);
CREATE INDEX idx_security_audit_created_at ON security_audit_log(created_at DESC);
CREATE INDEX idx_security_audit_user_id ON security_audit_log(user_id);
