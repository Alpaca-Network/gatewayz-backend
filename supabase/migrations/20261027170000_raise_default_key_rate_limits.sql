-- Raise the default per-API-key rate limits.
--
-- The original defaults (1,000 requests and 1M tokens per hour, burst 100,
-- concurrency 10) throttled every customer to ~16 requests and ~16.7k tokens
-- a minute; a user hit 429 after a single request on 2026-10-27. Keys whose
-- limits were set by hand to anything else keep them.
alter table public.rate_limit_configs
  alter column max_requests set default 36000,
  alter column max_tokens set default 50000000,
  alter column burst_limit set default 600,
  alter column concurrency_limit set default 50;

update public.rate_limit_configs
set max_requests = 36000,
    max_tokens = 50000000,
    burst_limit = 600,
    concurrency_limit = 50,
    updated_at = now() at time zone 'utc'
where max_requests = 1000
  and max_tokens = 1000000
  and burst_limit = 100
  and concurrency_limit = 10;
