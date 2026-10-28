-- Dispute content vault (Gatewayz x GenLayer cost disputes, opt-in).
--
-- Builds on 20261023000000_job_usage_v2.sql. By default Gatewayz stores no prompt
-- or output text: a v2 usage line holds only content_hash, and a user who contests
-- the line must keep and reveal the content bundle themselves. That is workable for
-- agents and painful for people.
--
-- A request on a job key may opt in (header X-Gatewayz-Dispute-Vault: on) to having
-- Gatewayz keep its content bundle, ENCRYPTED, until the dispute window closes:
-- 72 hours after the job closes (or after its deadline if it is never closed).
-- Contesting then needs one call: the owner asks for a reveal link, and the GenLayer
-- validators fetch the bundle from it and check it against content_hash.
--
--   * dispute_content  one encrypted bundle per request (AES-256-GCM, app-side key;
--                      the database never sees plaintext or the key)
--   * dispute_reveals  short-lived public links, stored as sha256(token) only
--   * purge_dispute_content()  hourly; deletes everything past its window

CREATE TABLE IF NOT EXISTS public.dispute_content (
    request_id   text PRIMARY KEY CHECK (length(request_id) BETWEEN 1 AND 128),
    job_id       text NOT NULL REFERENCES public.inference_jobs (job_id) ON DELETE CASCADE,
    content_hash text NOT NULL CHECK (content_hash ~ '^0x[0-9a-f]{64}$'),
    key_id       text NOT NULL CHECK (length(key_id) BETWEEN 1 AND 32),
    nonce        bytea NOT NULL CHECK (octet_length(nonce) = 12),
    -- The contract refuses bundles over 256 000 bytes; GCM adds a 16-byte tag.
    ciphertext   bytea NOT NULL CHECK (octet_length(ciphertext) <= 256016),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dispute_content_job_idx ON public.dispute_content (job_id);

CREATE TABLE IF NOT EXISTS public.dispute_reveals (
    token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    request_id text NOT NULL REFERENCES public.dispute_content (request_id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dispute_reveals_request_idx ON public.dispute_reveals (request_id);

ALTER TABLE public.dispute_content ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dispute_reveals ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.dispute_content FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.dispute_reveals FROM PUBLIC, anon, authenticated, service_role;
-- No UPDATE: a stored bundle is never rewritten, only deleted.
GRANT SELECT, INSERT, DELETE ON public.dispute_content TO service_role;
GRANT SELECT, INSERT, DELETE ON public.dispute_reveals TO service_role;

-- Window end for a job's content: 72 h after close, or after the deadline if the
-- job was never closed (a job key stops working at its deadline).
CREATE OR REPLACE FUNCTION public.dispute_content_expiry(p_job_id text)
RETURNS timestamptz
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT coalesce(closed_at, deadline) + interval '72 hours'
    FROM inference_jobs WHERE job_id = p_job_id
$$;

CREATE OR REPLACE FUNCTION public.purge_dispute_content()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_reveals bigint := 0;
    v_content bigint := 0;
BEGIN
    WITH d AS (DELETE FROM dispute_reveals WHERE expires_at < now() RETURNING 1)
    SELECT count(*) INTO v_reveals FROM d;

    WITH d AS (
        DELETE FROM dispute_content c
        USING inference_jobs j
        WHERE j.job_id = c.job_id
          AND coalesce(j.closed_at, j.deadline) + interval '72 hours' < now()
        RETURNING 1
    )
    SELECT count(*) INTO v_content FROM d;

    RETURN jsonb_build_object('timestamp', now(), 'reveals', v_reveals, 'content', v_content);
END;
$$;

REVOKE ALL ON FUNCTION public.dispute_content_expiry(text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.purge_dispute_content() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.dispute_content_expiry(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.purge_dispute_content() TO service_role;

-- Hourly purge. No-op if pg_cron is unavailable (then the API's own purge-on-write
-- and the expiry checks on every read still keep expired content unreachable).
DO $block$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        PERFORM cron.unschedule('purge-dispute-content')
        WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'purge-dispute-content');

        PERFORM cron.schedule(
            'purge-dispute-content',
            '17 * * * *',
            $$SELECT purge_dispute_content()$$
        );
    ELSE
        RAISE WARNING 'pg_cron not installed; run SELECT purge_dispute_content() on a schedule';
    END IF;
END;
$block$;
