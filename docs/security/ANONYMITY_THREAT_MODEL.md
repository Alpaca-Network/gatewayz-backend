# Gatewayz Anonymity Threat Model

**Status:** binding for Milestone 3 (gatewayz-backend #2255–#2260). Written 2026-09-03 from a file:line inventory of the request path, telemetry, persistence, and billing code on `main`.
**Spec:** `docs/superpowers/specs/2026-09-03-anonymized-routing-design.md`.

## 1. Two different things called "anonymous"

1. **Unauthenticated ("anonymous") callers** — no API key at all. Rate-limited per hashed IP, free models only, `user_id` NULL on every write. This already exists and is not what this document is about.
2. **Anonymized routing for authenticated, billed users** — the Milestone 3 target. Gatewayz knows *who* is asking (to check quota and bill) but no party downstream of Gatewayz can link *what* was asked to *who* asked. "Resolve identity to a yes/no quota decision, bill by an opaque reference, and let nothing identifying leave the process."

Everything below is about (2).

## 2. Parties

| Party | Sees today | Should see after M3 |
|---|---|---|
| **Upstream model provider** (OpenAI, Anthropic, xAI, DeepInfra, … OpenRouter as last resort) | Prompt + model params; Gatewayz's fixed egress IP; **whatever the client put in the OpenAI `user` field** (forwarded verbatim, incl. to Anthropic `metadata.user_id`) | Prompt + model params + Gatewayz egress IP. **Nothing else.** |
| **Gatewayz** (operator, DB, Sentry) | Identity ↔ token counts/cost per request (`chat_completion_requests`, `usage_records`, `credit_transactions`, `activity_log`); Sentry gets `user_id + email + real IP` per request; prompt content is **not** stored by default | Identity ↔ token counts/cost (needed to bill). Sentry: request id + hashed key only, no email, no IP, no bodies. Still no prompt content unless the user opts into chat history. |
| **Network observers** | TLS to Gatewayz; TLS from Gatewayz to providers | unchanged |
| **Other Gatewayz users** | nothing (response cache is not wired into the live path) | unchanged |

## 3. What IS protected (guarantees Gatewayz makes)

G1. **No identity leaves Gatewayz toward a provider.** No user id, wallet address, email, username, API key or key id, Gatewayz request id, client IP, client User-Agent, or client-supplied `user`/`metadata`/`extra_body`/`extra_headers` fields appear in any outbound provider request (headers or body), on any route (`/v1/chat/completions`, `/v1/messages`, embeddings, images/audio, tools). Enforced by a single scrubbing boundary and a **leak-canary test** that intercepts outbound HTTP for every provider client and asserts sentinel identity values never appear in the bytes.
G2. **Providers see one fixed egress**, Gatewayz's Railway IP, never the requester's. (Already true; now asserted and documented. The Vercel serverless entry `api/index.py` is not a production egress for inference.)
G3. **Gatewayz stores no prompt or response content in the inference path.** Billing tables hold identity + token counts + cost only. Chat history (`chat_sessions`/`chat_messages`) is an explicit opt-in product feature and is identity-linked by design; using it is choosing to be identified to Gatewayz.
G4. **Billing correlation is by an opaque server-minted reference**, not by the client-settable `X-Request-ID` and not by anything a provider ever sees. The reference is generated per request, used as the idempotency key for deduction/refund, and stored beside identity in billing tables only.
G5. **Gatewayz's own error tooling cannot re-link content to identity**: Sentry receives no email, no client IP, no request/response bodies; exception messages persisted to `chat_completion_requests.error_message` are type + sanitized message, never echoing user input; logs never contain prompt content (existing convention, now tested).
G6. **Free-form billing columns never carry content**: `credit_transactions.description/metadata` and `activity_log.metadata` are reason strings / token breakdowns only (defensive rule + test).

## 4. What is NOT protected (and we say so)

N1. **Gatewayz itself knows who you are** and how much you used — it has to, to bill you. Anonymity is *from the model providers*, not from Gatewayz. A subpoena to Gatewayz yields identity ↔ usage counts, not content (unless chat history was enabled).
N2. **The provider sees the prompt.** If you put your name in the prompt, the provider has your name. Anonymized routing is not content redaction.
N3. **Timing and volume correlation.** A provider colluding with Gatewayz, or anyone with both Gatewayz billing records and provider request logs, can correlate by timestamp/token counts. Batching, jitter, or mixing are out of scope for M3.
N4. **Wallet ↔ stake correlation.** Stake sizes are public on-chain and `wallet_stakes` mirrors them; the wallet ↔ account link (`user_wallets`) is private to Gatewayz. Future allowance enforcement must follow the same rule as billing: resolve to yes/no at request time, never attach wallet or stake to anything outbound or to content.
N5. **Client-side identifiers.** If your client SDK sets `user`, we now strip it; if it embeds identifiers in message content, we do not.
N6. **Legal/operator access** to Gatewayz's own database and Sentry.

## 5. Leak vectors enumerated (inventory → disposition)

| # | Vector | Today | Disposition |
|---|---|---|---|
| L1 | Client `user` field → provider body / Anthropic `metadata.user_id` | forwarded verbatim | **Strip** (W-B). Optional per-request HMAC pseudonym, off by default. |
| L2 | Client `metadata`/`extra_body`/`extra_headers` passthrough | dropped on `/v1/messages`; `extra_body` not modelled | **Deny-list at one boundary** (W-B) |
| L3 | Client IP / User-Agent / `X-Forwarded-For` to providers | not forwarded | Assert with canary test (W-B) |
| L4 | Egress IP | fixed Railway egress | Document (this doc) |
| L5 | Sentry user context: `user_id`, `email`, `api_key_id`, `client_host` (real IP) | attached to every request scope | **Drop email + IP; keep hashed key + request ref; `before_send` strips bodies** (W-C) |
| L6 | `chat_completion_requests.error_message = str(e)[:500]` | may echo input | **Type + sanitized message only** (W-C) |
| L7 | Client-settable `X-Request-ID` used as billing idempotency key and Sentry tag | yes | **Server-minted billing ref**; client id only echoed back (W-C) |
| L8 | OTLP/Arize/Loki config vars | dead code, but a stray `OTEL_EXPORTER_OTLP_ENDPOINT` would silently export spans carrying `user_id` | **Remove dead config; startup guard** (W-C) |
| L9 | `usage_records.api_key` plaintext column; `GRANT ALL` to anon/authenticated; no RLS policy | protected only by default-deny | **Hash the column, revoke grants, add explicit deny policies** (W-A) |
| L10 | Leftover `USING (true)` policy row on `chat_completion_requests` | inert (REVOKE precedes) | **Drop it** (W-A) |
| L11 | Retention: `usage_records`, `credit_transactions`, `activity_log` grow unbounded; `chat_completion_requests` has `cleanup_/rollup_` functions with an unverified window | — | **Document windows; schedule cleanup where missing** (W-C) |
| L12 | Chat history / shared chats | identity-linked by design | Out of scope; documented as opt-in (N1, G3) |
| L13 | Timing/volume correlation | — | Out of scope (N3) |

## 6. Verification

- `tests/security/test_upstream_identity_firewall.py`: for every provider client, drive a request carrying sentinel identity values through the real handler with the HTTP layer intercepted; assert no sentinel appears in any outbound header or body byte. This test is the executable form of G1 and must stay green.
- `tests/security/test_internal_channels.py`: Sentry scope has no `email`/`client_host`/bodies; `error_message` never contains a sentinel prompt; billing ref ≠ client `X-Request-ID`.
- Migration tests / `pg_policies` assertions for L9/L10.

## 7. Changing this document

Any new provider client, telemetry sink, or table that touches the inference path must be added to §5 with a disposition, and covered by the canary test, before merge.
