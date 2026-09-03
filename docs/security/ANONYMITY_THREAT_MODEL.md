# Gatewayz Anonymity Threat Model

**Status:** binding for Milestone 3 (gatewayz-backend #2255–#2260); extended for Milestone 4's community GPU marketplace (#2261-#2267, testnet stage), see the M4 addendum in §2/§4/§5 below. Written 2026-09-03 from a file:line inventory of the request path, telemetry, persistence, and billing code on `main`.
**Spec:** `docs/superpowers/specs/2026-09-03-anonymized-routing-design.md` (M3); M4's GPU marketplace design lives in the M4 workstream's scratchpad spec (gatewayz-backend #2261 epic).

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
| **Community compute operator** (M4, opt-in `community/<model>` routing only) | n/a — didn't exist before M4 | Prompt + model params + response content, **by construction** (they run the inference — see N7). Never identity: no `user_id`, wallet address, email, API key, client IP, or User-Agent. They do receive the request's `billing_ref` (forwarded as `X-Gatewayz-Request-Id` on the *outbound* request to the node, a new exception to G1 — see N7) so their optional attestation signature can reference the request it covers; `billing_ref` alone does not identify the requester (it's an opaque per-request UUID, unlinked from any account without Gatewayz's own database). |

## 3. What IS protected (guarantees Gatewayz makes)

G1. **No identity leaves Gatewayz toward a provider.** No user id, wallet address, email, username, API key or key id, Gatewayz request id, client IP, client User-Agent, or client-supplied `user`/`metadata`/`extra_body`/`extra_headers` fields appear in any outbound provider request (headers or body), on any route (`/v1/chat/completions`, `/v1/messages`, embeddings, images/audio, tools). Enforced by a single scrubbing boundary and a **leak-canary test** that intercepts outbound HTTP for every provider client and asserts sentinel identity values never appear in the bytes. **M4 amendment (N7):** the `community` provider is a scoped, deliberate exception to the "Gatewayz request id" clause — `billing_ref` is forwarded to community nodes (never to any other provider) as `X-Gatewayz-Request-Id`, solely so an opt-in node can counter-sign an attestation covering that request. `billing_ref` is not itself identity (N7 explains why); every *other* identity field in this list is still stripped on the community path exactly as on every other provider, and the leak-canary test covers `community` like any other provider (see §6).
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
N7. **A community compute operator sees full prompt and response content, by construction** (M4). Unlike every other provider, a community node is not a contractual party bound by an enterprise agreement — it's a self-registered, admin-approved individual or organization running their own hardware. This is disclosed, not incidental: routing to `community/<model>` is the only way traffic reaches a community node (never automatic failover, never smart-routing selection), so the model-id prefix a client chooses **is** the user's informed consent to this exposure — documented in `docs/gpu/PROVIDER_ONBOARDING.md`'s "trust disclosure" and `docs/api.md`'s "GPU Marketplace" section. Identity guarantees (G1, minus the scoped `billing_ref` carve-out above) still hold on this path: no user id, wallet, email, or IP ever reaches the node. What changes is that, unlike every other provider, community operators have no contract preventing them from retaining or misusing content they see — treat community routing as strictly higher-trust-required-of-the-user than the default provider set, which is exactly why it defaults off and requires an explicit per-request opt-in.

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
| L14 | Community node operator sees full prompt/response content (M4) | n/a — feature doesn't exist pre-M4 | **Disclosed, opt-in** via the `community/` model-id prefix (N7); identity (G1) still stripped on this path except the scoped `billing_ref` carve-out for attestation; `community` added to the leak-canary provider list like any other provider |

## 6. Verification

- `tests/security/test_upstream_identity_firewall.py`: for every provider client, drive a request carrying sentinel identity values through the real handler with the HTTP layer intercepted; assert no sentinel appears in any outbound header or body byte. This test is the executable form of G1 and must stay green — **must include `community` in its provider list** (W-A2), asserting the same sentinels never leak *except* `billing_ref`, which the same test should assert IS present on the community path (the scoped, deliberate carve-out in G1/N7) but nowhere else.
- `tests/security/test_internal_channels.py`: Sentry scope has no `email`/`client_host`/bodies; `error_message` never contains a sentinel prompt; billing ref ≠ client `X-Request-ID`.
- Migration tests / `pg_policies` assertions for L9/L10.

## 7. Changing this document

Any new provider client, telemetry sink, or table that touches the inference path must be added to §5 with a disposition, and covered by the canary test, before merge.
