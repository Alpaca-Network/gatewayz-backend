-- v2 usage lines for cost disputes (Gatewayz x GenLayer, shadow mode).
--
-- Builds on 20261005000000_inference_jobs.sql. Each job usage line keeps its v1
-- entry (keccak, InferenceEscrow — unchanged) and gains a v2 entry hashed with
-- sha256 for the CostDispute contract (Alpaca-Network/gatewayz-genlayer,
-- gzgl/billing.py). Still billing metadata only: content_hash is a fingerprint of
-- the client's messages + visible output; the text is never stored.
--
--   * inference_job_usage.entry_v2         the exact v2 entry that is hashed
--   * inference_job_usage.usage_estimated  true when the provider sent no usage
--                                          and tokens were estimated (len/4)
--   * inference_job_usage.request_id       joins to chat_completion_requests
--   * inference_jobs.usage_root_v2         sha256 Merkle root, written at close
--   * price_sheets                         content-addressed, immutable rate sheets
--                                          served at GET /v1/price-sheets/{hash}
--
-- append_job_usage stays the only writer. It now also stores the line's price
-- sheet in the same transaction, so a v2 line never points at a sheet the API
-- cannot serve, and recording a line is still one round trip.

CREATE TABLE IF NOT EXISTS public.price_sheets (
    hash       text PRIMARY KEY CHECK (hash ~ '^0x[0-9a-f]{64}$'),
    body       text NOT NULL CHECK (octet_length(body) <= 128000),
    created_at timestamptz NOT NULL DEFAULT now(),
    -- The served bytes must hash to the key, or a validator's check fails.
    CONSTRAINT price_sheets_hash_matches_body
        CHECK (hash = '0x' || encode(sha256(convert_to(body, 'UTF8')), 'hex'))
);

ALTER TABLE public.inference_job_usage
    ADD COLUMN IF NOT EXISTS entry_v2 jsonb,
    ADD COLUMN IF NOT EXISTS usage_estimated boolean NOT NULL DEFAULT false;

ALTER TABLE public.inference_job_usage
    ADD COLUMN IF NOT EXISTS request_id text GENERATED ALWAYS AS (entry_v2 ->> 'request_id') STORED;

CREATE INDEX IF NOT EXISTS inference_job_usage_request_id_idx
    ON public.inference_job_usage (request_id) WHERE request_id IS NOT NULL;

ALTER TABLE public.inference_jobs
    ADD COLUMN IF NOT EXISTS usage_root_v2 text
        CHECK (usage_root_v2 IS NULL OR usage_root_v2 ~ '^0x[0-9a-f]{64}$');

-- Replace, not overload: two append_job_usage signatures would make PostgREST's
-- named-argument resolution ambiguous. The new parameters default, so a caller
-- still sending (p_job_id, p_entry, p_cost) keeps working during a deploy.
DROP FUNCTION IF EXISTS public.append_job_usage(text, jsonb, numeric);

CREATE OR REPLACE FUNCTION public.append_job_usage(
    p_job_id           text,
    p_entry            jsonb,
    p_cost             numeric,
    p_entry_v2         jsonb   DEFAULT NULL,
    p_usage_estimated  boolean DEFAULT false,
    p_price_sheet_body text    DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_status text;
    v_seq    integer;
    v_sheet  text;
BEGIN
    SELECT status INTO v_status FROM inference_jobs WHERE job_id = p_job_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'job_not_found';
    END IF;
    IF v_status <> 'running' THEN
        -- See 20261005000000: a line that lands after close is reported, never
        -- folded into a root that is already public.
        RAISE EXCEPTION 'job_not_running';
    END IF;

    IF p_entry_v2 IS NOT NULL THEN
        IF jsonb_typeof(p_entry_v2) <> 'object' THEN
            RAISE EXCEPTION 'entry_v2_invalid';
        END IF;
        v_sheet := p_entry_v2 ->> 'price_sheet';
        IF v_sheet IS NULL OR v_sheet !~ '^0x[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'entry_v2_invalid: price_sheet';
        END IF;
        IF COALESCE(p_entry_v2 ->> 'content_hash', '') !~ '^0x[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'entry_v2_invalid: content_hash';
        END IF;
        -- The zero hash means "no sheet" (pricing lookup failed): the line is
        -- judged as price_unlisted. Any other hash must be servable.
        IF v_sheet <> '0x' || repeat('0', 64) THEN
            IF p_price_sheet_body IS NOT NULL THEN
                -- The CHECK constraint rejects a body that does not hash to v_sheet.
                INSERT INTO price_sheets (hash, body) VALUES (v_sheet, p_price_sheet_body)
                ON CONFLICT (hash) DO NOTHING;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM price_sheets WHERE hash = v_sheet) THEN
                RAISE EXCEPTION 'price_sheet_unknown';
            END IF;
        END IF;
    END IF;

    SELECT COALESCE(MAX(seq) + 1, 0) INTO v_seq FROM inference_job_usage WHERE job_id = p_job_id;
    INSERT INTO inference_job_usage (job_id, seq, entry, entry_v2, usage_estimated)
    VALUES (p_job_id, v_seq, p_entry, p_entry_v2, COALESCE(p_usage_estimated, false));
    UPDATE inference_jobs SET spent_usd = spent_usd + GREATEST(p_cost, 0) WHERE job_id = p_job_id;
    RETURN v_seq;
END;
$$;

ALTER TABLE public.price_sheets ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.price_sheets FROM PUBLIC;
REVOKE ALL ON public.price_sheets FROM anon, authenticated;
-- Published sheets are immutable: lines sealed into public roots point at them.
-- The backend may read and add, never change or remove.
REVOKE ALL ON public.price_sheets FROM service_role;
GRANT SELECT, INSERT ON public.price_sheets TO service_role;

REVOKE ALL ON FUNCTION public.append_job_usage(text, jsonb, numeric, jsonb, boolean, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.append_job_usage(text, jsonb, numeric, jsonb, boolean, text) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.append_job_usage(text, jsonb, numeric, jsonb, boolean, text) TO service_role;
