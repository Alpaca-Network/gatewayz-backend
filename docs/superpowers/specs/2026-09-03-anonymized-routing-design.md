# Anonymized Request Routing — Design (Milestone 3)

**Issues:** gatewayz-backend #2255 (epic), #2256 threat model, #2257 relayer, #2258 audit-log split, #2259 metadata scrubbing, #2260 anonymized billing.
**Threat model (binding):** `docs/security/ANONYMITY_THREAT_MODEL.md` — read it first; this spec implements its §5 dispositions.
**Research:** session scratchpad `m3/research.md` (file:line inventory, verified 2026-09-03).

## 1. Summary of what changes

The inference path is already close to the goal: providers see Gatewayz's fixed egress IP and no headers; prompt content is not persisted; billing tables hold only identity + counts. M3 therefore does not introduce a separate relayer service. It introduces **one scrubbing boundary** for everything outbound, **fixes Gatewayz's own internal deanonymization channels**, **hardens two tables**, and **tests all of it permanently**.

## 2. W-A — Table hardening (merge first; security)

Migration `supabase/migrations/20260903100000_usage_records_hardening.sql` (idempotent):
1. `usage_records`: add `api_key_hash text`; backfill `api_key_hash = encode(sha256(api_key::bytea),'hex')`... **No** — the app already has a salted hash (`sha256_key_hash()` with `KEY_HASH_SALT`) that the DB cannot reproduce. Decision: **stop writing the plaintext key**. Add column `api_key_id bigint` (FK `api_keys_new(id)`, nullable), change the writer (`src/db/users.py` `record_usage`) to write `api_key_id` + `api_key_last4` (new `text` column, 4 chars) and **NULL** for `api_key`; then a follow-up staged migration (in `supabase/staged-migrations/`, human-gated per repo convention) nulls the historical `api_key` column and drops it. Readers (`db/rate_limits.py`, `db/faucet.py`, `routes/admin.py`, `ledger_reconciliation.py`) — audit each: if any filters by `api_key`, switch it to `api_key_id`/`user_id`. Faucet eligibility uses `user_id` already.
2. `usage_records`: `REVOKE ALL ... FROM anon, authenticated; GRANT ALL ... TO service_role;` and add an explicit deny-all policy (`CREATE POLICY usage_records_service_only ON public.usage_records FOR ALL TO authenticated, anon USING (false)`) so the table is not one toggle away from the 2026-05-27 incident.
3. `chat_completion_requests`: `DROP POLICY IF EXISTS <the stub's USING (true) policy>` (name from `pg_policies`; the migration must look it up or use the exact name from `20251226000000_create_chat_completion_requests_stub.sql`).
4. A test that loads all migrations' SQL and asserts no `USING (true)`/`WITH CHECK (true)` policy is *created* for `anon`/`authenticated` on any table listed in `docs/security/ANONYMITY_THREAT_MODEL.md` §5 without a later drop (static check over the migration files — cheap, catches the footgun).

## 3. W-B — Upstream identity firewall (#2257, #2259)

### 3.1 The boundary
New module `src/services/upstream/anonymize.py`:
```python
OUTBOUND_DENY_FIELDS = frozenset({"user", "metadata", "extra_body", "extra_headers", "extra_query"})
IDENTITY_SENTINEL_KEYS = ("user_id", "email", "wallet", "api_key", "request_id", "ip", "user_agent")

def scrub_upstream_kwargs(kwargs: dict, *, billing_ref: str | None = None) -> dict:
    """Return a copy safe to send to any provider: deny-listed passthrough fields removed;
    if Config.UPSTREAM_ABUSE_PSEUDONYM is true and billing_ref given, kwargs['user'] = pseudonym(billing_ref)."""

def pseudonym(billing_ref: str) -> str:
    """'gw_' + hmac_sha256(Config.UPSTREAM_PSEUDONYM_SECRET, billing_ref)[:16]. Unlinkable without the secret; rotates per request."""
```
- Applied in exactly one place per path: `ChatInferenceHandler` where `kwargs` are assembled (non-stream and stream; `chat_handler.py` ~L951-970 and ~L1230-1249) — both call `scrub_upstream_kwargs`. `/v1/messages` shares that pipeline (already drops `metadata`); embeddings/images/audio/tools build their own payloads — add the same call on their payload dicts for uniformity (no-ops today).
- `anthropic_native_client.py:288-289`: keep the `metadata.user_id` construction but it can now only receive the pseudonym (or nothing).
- `openrouter_client.py`: `HTTP-Referer`/`X-Title` identify Gatewayz, not the user — allowed; document in the header allow-list.
- Config: `UPSTREAM_ABUSE_PSEUDONYM` (default `false`), `UPSTREAM_PSEUDONYM_SECRET` (required only when the flag is on; startup error otherwise).
- `ProxyRequest.user` stays accepted (OpenAI compatibility) but is documented as "accepted and discarded"; add `X-Gatewayz-User-Passthrough: stripped` response header? **No** — YAGNI; document in `docs/api.md`.

### 3.2 The canary test (the deliverable that matters)
`tests/security/test_upstream_identity_firewall.py`:
- Sentinels: `user_id=424242`, `email="canary-424242@example.test"`, wallet `0xCA11A2…`, api key `gw_live_CANARY…`, client `X-Request-ID: canary-req-424242`, IP `203.0.113.77`, UA `CanaryUA/1.0`, client `user="canary-end-user"`, `metadata={"user_id":"canary-meta"}`.
- For **each** provider client module under `src/services/providers/` (enumerate dynamically so new clients are covered automatically) plus embeddings/images/audio/tools routes: monkeypatch the HTTP layer at the lowest common point (`httpx.Client.send`/`httpx.AsyncClient.send`, and for OpenAI-SDK-based clients its `httpx` transport) to capture the outbound request (method, URL, headers, body bytes) and return a minimal valid provider response; drive a request through the real route with TestClient and a mocked user/API-key resolution carrying the sentinels; assert **none** of the sentinel strings (case-insensitive, also checksummed/lowercased wallet forms) appear in URL, headers, or body. Also assert the outbound header set ⊆ per-provider allow-list (`Authorization`, `Content-Type`, `Accept`, `User-Agent` of the SDK, `anthropic-version`, `x-api-key`, `HTTP-Referer`, `X-Title`, provider-specific documented extras).
- One negative control: with the scrub disabled via monkeypatch, the `user` sentinel DOES appear for Anthropic and one OpenAI-compat provider — proves the test can fail.
- With `UPSTREAM_ABUSE_PSEUDONYM=true`: outbound `user` equals `pseudonym(billing_ref)` and is not equal to, nor derivable from, the client `X-Request-ID`.

## 4. W-C — Internal channels, billing ref, retention (#2258, #2260)

1. **Server-minted billing ref.** `request_id_middleware.py` continues to accept/echo the client's `X-Request-ID` for *their* tracing, but the value used internally for idempotency (`handle_credits_and_usage(request_id=…)`, `refund_credits(original_request_id=…)`), `chat_completion_requests.request_id`, `credit_transactions.request_id`, and Sentry tagging becomes `request.state.billing_ref = uuid4()` minted server-side. Response header `X-Gatewayz-Request-Id: <billing_ref>` is added so users can quote it to support. (This is the "opaque per-request token" of #2257/#2260: it never goes upstream, it's not client-chosen, and it is the only join key between billing rows and a request.)
2. **Sentry** (`auto_sentry_middleware.py`): user context = `{"id": <user_id>}` only when authenticated (needed for support), plus tags `api_key_hash` (existing, 16 hex) and `billing_ref`; **remove `email` and `client_host`**; `sentry_sdk.init(..., send_default_pii=False, before_send=_strip_bodies)` where `_strip_bodies` deletes `request.data`, `request.cookies`, and truncates `exception.values[*].value` to 300 chars after `sanitize_for_logging`. Test via a fake Sentry hub/transport capturing the event.
3. **`error_message` scrubbing**: `error_persistence.py` / `chat.py` write `f"{type(e).__name__}: {sanitize_provider_error_for_user(str(e))[:200]}"`; never raw `str(e)`. Test: an exception whose message contains a sentinel prompt fragment persists without it.
4. **Dead telemetry config**: remove `ARIZE_*`, `LOKI_*`, Traceloop/Tempo vars from `config.py` and `.env.example` (grep to confirm zero readers); startup guard in `services/startup.py`: if `OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set, log `CRITICAL` and refuse to enable exporting (spans stay local). `ai_tracing.set_user_info` stops attaching `user_id` (keep tier/model); it exports nowhere today, but the attribute shouldn't exist.
5. **Retention**: read `cleanup_chat_completion_requests`/`rollup_chat_completion_requests` and document their windows in the threat model §5/L11; add an APScheduler job (mirroring `start_ledger_reconciliation_scheduler`) that deletes `usage_records` older than `USAGE_RECORDS_RETENTION_DAYS` (default 400 — >1 year for disputes; `usage_records` is legacy) and `activity_log` rows older than `ACTIVITY_LOG_RETENTION_DAYS` (default 400), in batches of 5000 with a per-run cap; `credit_transactions` is the financial ledger and is **not** pruned. Document all windows in `docs/security/DATA_RETENTION.md`.
6. **Defensive rule for free-form columns**: `credit_transactions.description` ≤ 200 chars and `metadata` keys restricted to an allow-list in the writer (`db/credit_transactions.py`); `activity_log.metadata` likewise (token breakdown keys only). Tests.
7. **`tests/security/test_internal_channels.py`** covering 1–3 and 6.

## 5. Not in scope (recorded)
Async/deferred deduction (identity living in one request's memory is not a leak); a separate relayer service; batching/jitter against timing correlation; chat-history/shared-chats (opt-in, identity-linked by design); response cache (not wired into the live path — leave as is, note the dead file for cleanup); EIP-1271; allowance enforcement (must follow the same yes/no-then-discard rule when built).

## 6. Config additions
`UPSTREAM_ABUSE_PSEUDONYM` (false), `UPSTREAM_PSEUDONYM_SECRET`, `USAGE_RECORDS_RETENTION_DAYS` (400), `ACTIVITY_LOG_RETENTION_DAYS` (400). Removed: `ARIZE_*`, `LOKI_*`, Traceloop/Tempo vars.

## 7. Rollout
W-A first (migration; the writer change ships with it; staged drop of `usage_records.api_key` is human-gated). W-B and W-C in parallel. Railway needs no new vars unless the pseudonym is turned on. Update `docs/api.md` (passthrough fields stripped; `X-Gatewayz-Request-Id`) and link the threat model from `README`/`docs/architecture.md`.

## 8. Workstreams
| WS | Scope | Issues |
|---|---|---|
| W-A | usage_records hardening, chat_completion_requests leftover policy, policy footgun test | #2258 (hardening), pre-req |
| W-B | `anonymize.py` boundary, applied everywhere; canary test; docs | #2257 #2259 |
| W-C | billing ref, Sentry, error_message, dead telemetry + OTLP guard, retention jobs, free-form column rules, tests, DATA_RETENTION.md | #2258 #2260 |
| (doc) | threat model | #2256 |
