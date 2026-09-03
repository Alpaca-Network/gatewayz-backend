# Data Retention

**Status:** binding, part of Milestone 3 (gatewayz-backend #2258). Threat model L11 (`docs/security/ANONYMITY_THREAT_MODEL.md`): several tables that log identity ↔ usage have no natural retention pressure and grow unbounded. This document is the single source of truth for every table's retention window, how it's enforced, and where the enforcement code lives.

## Summary table

| Table | Window | Mechanism | Config | Notes |
|---|---|---|---|---|
| `chat_completion_requests` | 90 days | `pg_cron` daily @ 04:00 UTC | fixed in SQL | Rolled up into `chat_completion_daily_aggregates` *before* deletion, so lifetime analytics survive the TTL. |
| `chat_completion_daily_aggregates` | unbounded | — | — | Per-day/model/user aggregate rows populated by the rollup above; small and append-mostly, not pruned. |
| `model_health_history` | 7 days | `pg_cron` daily @ 04:00 UTC | fixed in SQL | `model_health_aggregates` holds the long-term trend view. |
| `rate_limit_alerts` | 1 day (resolved) / 30 days hard cap (unresolved) | `pg_cron` daily @ 04:00 UTC | fixed in SQL | |
| `reconciliation_logs` (`ttl_cleanup_%` / `ttl_rollup_%` rows) | 30 days | `pg_cron` daily @ 04:00 UTC | fixed in SQL | Audit trail of the cleanup/rollup jobs themselves. |
| `usage_records` | 400 days (default) | App-level APScheduler, `run_scheduled_retention_cleanup` | `USAGE_RECORDS_RETENTION_DAYS` | New in M3 (this doc). Legacy table; window intentionally >1 year to cover payment disputes. Batched (5000/batch, 20 batches/run cap) — see `src/db/retention.py`. |
| `activity_log` | 400 days (default) | App-level APScheduler, `run_scheduled_retention_cleanup` | `ACTIVITY_LOG_RETENTION_DAYS` | New in M3 (this doc). Same batching as `usage_records`. |
| `credit_transactions` | **never pruned** | — | — | Financial audit ledger. Deliberately excluded from both the SQL and app-level jobs. |
| `stripe_webhook_events` | 90 days (function exists) | none currently scheduled | — | `cleanup_old_events()` in `src/db/webhook_events.py` exists but has no caller/scheduler wired up — noted here for accuracy, not part of this milestone's scope. |
| Sentry events | 90 days | Sentry's own project retention setting | external (Sentry dashboard) | Not configured by this codebase; see `SENTRY_ENABLED`/`SENTRY_DSN` in `.env.example` for what we send (and `docs/security/ANONYMITY_THREAT_MODEL.md` G5 for what we deliberately don't). |
| `chat_history` / `chat_messages` / `shared_chats` | unbounded, opt-in | — | — | Explicit user feature; out of scope (threat model N1/G3 — using chat history is choosing to be identified to Gatewayz). |

## Database-level jobs (pre-existing, `supabase/migrations/`)

`chat_completion_requests`, `model_health_history`, and `rate_limit_alerts` are cleaned up by a single `pg_cron` job (`ttl-cleanup-daily`, 04:00 UTC) defined across two migrations:

- `supabase/migrations/20260525000000_add_ttl_cleanup_jobs.sql` — original job (chat_completion_requests at 30 days).
- `supabase/migrations/20260525010000_fix_ttl_cleanup_jobs.sql` — follow-up: extended `chat_completion_requests` to 90 days and added the `chat_completion_daily_aggregates` rollup (`rollup_chat_completion_requests()`) so the shorter-lived raw table doesn't destroy the `model_usage_analytics` view's lifetime numbers. Also fixed a missing `WHERE resolved = FALSE` on the `rate_limit_alerts` unresolved-cap delete and added `SET search_path` / per-function exception handling.

Every run is logged to `reconciliation_logs` (job names `ttl_cleanup_*` / `ttl_rollup_*`), which is itself pruned to the last 30 days by the same job. If `pg_cron` is unavailable, the migration logs a warning and the functions must be invoked manually (`SELECT run_and_log_ttl_cleanups();`).

## App-level job (new in M3, `src/services/scheduled_sync.py`)

`usage_records` and `activity_log` had no retention mechanism before M3 (threat model L11) and are not managed by `pg_cron` — the app itself owns this job, mirroring the existing ledger-reconciliation scheduler pattern:

- `run_scheduled_retention_cleanup()` calls `cleanup_usage_records()` / `cleanup_activity_log()` (`src/db/retention.py`).
- Each delete is batched: up to `batch_size` (default 5000) rows per batch, capped at `max_batches` (default 20) per run — i.e. at most 100,000 rows deleted per run, so a large backlog can't lock up the table in one pass; it catches up over subsequent runs instead.
- `start_retention_scheduler()` / `stop_retention_scheduler()` are wired into `src/services/startup.py`'s lifespan, running every `RETENTION_CLEANUP_INTERVAL_HOURS` hours (default 24).
- Never raises — a DB error is logged and the job simply retries on its next scheduled run.

### Config

| Var | Default | Meaning |
|---|---|---|
| `USAGE_RECORDS_RETENTION_DAYS` | `400` | Delete `usage_records` rows older than this. |
| `ACTIVITY_LOG_RETENTION_DAYS` | `400` | Delete `activity_log` rows older than this. |
| `RETENTION_CLEANUP_INTERVAL_HOURS` | `24` | How often the app-level job runs. |

## Why `credit_transactions` is never pruned

It is the financial audit ledger — the record of every credit addition/deduction, needed for billing disputes, refund tracing, and reconciliation (`src/services/billing/ledger_reconciliation.py`). Neither the SQL jobs nor the app-level retention job touch it. `credit_transactions.description`/`metadata` are separately bounded to never carry free-form content (threat model G6) — see `src/db/credit_transactions.py`'s `_METADATA_ALLOWED_KEYS`.

## Changing a window

Update the table above and the corresponding source (SQL migration for the `pg_cron`-managed tables, `.env`/`Config` for the app-level job) together — this file must stay accurate, not aspirational.
