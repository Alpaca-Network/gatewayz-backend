-- Job keys: bounded in-flight requests (Codex P1 on #2382).
--
-- The job cap was checked with a plain read before each request, and spend only
-- rises when a request's usage line is appended afterwards. A burst of concurrent
-- requests could all read the same below-cap spent_usd and all be admitted, so a
-- job could overshoot its cap by the cost of the whole burst.
--
-- admit_job_request is now the gate for every billed request on a job key. It
-- locks the job row, re-checks running / deadline / cap, and takes a lease only
-- while fewer than p_max_inflight leases are live. The overshoot is therefore at
-- most p_max_inflight requests' cost. The account still pays for its own usage;
-- the cap is the user's own budget, not Gatewayz credit.
--
-- A lease is released when the response finishes (JobLeaseMiddleware). One left
-- behind by a crashed worker expires after p_ttl_seconds and is swept on the next
-- admission, so it can block a job for at most that long.

CREATE TABLE IF NOT EXISTS public.inference_job_leases (
    job_id     text NOT NULL REFERENCES public.inference_jobs(job_id) ON DELETE CASCADE,
    lease_id   uuid NOT NULL DEFAULT gen_random_uuid(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (job_id, lease_id)
);

CREATE OR REPLACE FUNCTION public.admit_job_request(
    p_api_key_id bigint, p_max_inflight integer, p_ttl_seconds integer
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_job   inference_jobs%ROWTYPE;
    v_live  integer;
    v_lease uuid;
BEGIN
    SELECT * INTO v_job FROM inference_jobs WHERE api_key_id = p_api_key_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN NULL;  -- not a job key
    END IF;
    IF v_job.status <> 'running' OR v_job.deadline <= now() THEN
        RAISE EXCEPTION 'job_not_running';
    END IF;
    IF v_job.spent_usd >= v_job.cap_usd THEN
        RAISE EXCEPTION 'job_cap_reached';
    END IF;
    DELETE FROM inference_job_leases WHERE job_id = v_job.job_id AND expires_at <= now();
    SELECT count(*) INTO v_live FROM inference_job_leases WHERE job_id = v_job.job_id;
    IF v_live >= GREATEST(p_max_inflight, 1) THEN
        RAISE EXCEPTION 'job_busy';
    END IF;
    INSERT INTO inference_job_leases (job_id, expires_at)
    VALUES (v_job.job_id, now() + make_interval(secs => GREATEST(p_ttl_seconds, 1)))
    RETURNING lease_id INTO v_lease;
    RETURN jsonb_build_object('job_id', v_job.job_id, 'lease_id', v_lease);
END;
$$;

CREATE OR REPLACE FUNCTION public.release_job_request(p_job_id text, p_lease_id uuid)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    DELETE FROM inference_job_leases WHERE job_id = p_job_id AND lease_id = p_lease_id;
$$;

ALTER TABLE public.inference_job_leases ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.inference_job_leases FROM PUBLIC;
REVOKE ALL ON public.inference_job_leases FROM anon, authenticated;
GRANT ALL ON public.inference_job_leases TO service_role;

REVOKE ALL ON FUNCTION public.admit_job_request(bigint, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.admit_job_request(bigint, integer, integer) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admit_job_request(bigint, integer, integer) TO service_role;
REVOKE ALL ON FUNCTION public.release_job_request(text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.release_job_request(text, uuid) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_job_request(text, uuid) TO service_role;
