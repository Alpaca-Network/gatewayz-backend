# Community node work receipts & attestation

Every call Gatewayz routes to a `community/<model>` node (gatewayz-backend
#2262 #2265) is recorded as one `provider_work` row — a *receipt*, never the
prompt or response itself (threat model G3: content is never retained by
Gatewayz for this path). A receipt lets the payout/verification pipeline
(W-B, #2265 #2266) confirm a node actually did the work it's being paid for,
without Gatewayz storing what was said.

## Canonical hashing

`src/services/gpu/hashing.py` (`canonical_json`/`sha256_hex`, and the
`hash_prompt`/`hash_response` wrappers named to match the node agent) defines
the **only** correct way to hash a prompt or response for this system. Both
the gateway and the node agent (`scripts/gpu_node_agent.py`'s own
`_canonical_json`/`hash_prompt`/`hash_response`) MUST produce byte-identical
hashes, or attestation signatures will never verify.

```python
canonical_json(value) = json.dumps(value, sort_keys=True, separators=(",", ":"))
sha256_hex(text)       = sha256(text.encode("utf-8")).hexdigest()

hash_prompt(messages)       = sha256_hex(canonical_json(messages))
hash_response(response_text) = sha256_hex(response_text)
```

- `prompt_hash = hash_prompt(messages)` — `messages` is the exact
  OpenAI-format list Gatewayz sends to the node (list of
  `{"role": ..., "content": ...}` dicts, in the order sent).
- `response_hash = hash_response(response_text)` — `response_text` is the
  assistant's final message content as a plain string (streamed responses:
  the concatenation of every `delta.content` chunk, in order — not the raw
  SSE bytes).
- **`n > 1` (multiple completions): only `choices[0]` counts.** If a
  request asks for more than one completion, `response_text` is
  `choices[0].message.content` alone (`""` if absent) — matching
  `community_adapter.py`'s `_record_receipt`, which never hashes any
  choice past the first. Do not concatenate every choice's content; that
  produces a hash the gateway's own receipt was never computed against,
  so the attestation signature won't verify.

**`ensure_ascii` matters.** `json.dumps` defaults to `ensure_ascii=True`
(non-ASCII characters rendered as `\uXXXX` escapes) unless told otherwise —
neither side passes `ensure_ascii`, so both get that default. Do **not**
"clean this up" to `ensure_ascii=False` on either side: it reads more
naturally but silently changes every hash of a prompt/response containing a
non-ASCII character (accents, CJK, emoji, ...), breaking attestation for
exactly the traffic most likely to use it.

If your language's JSON encoder doesn't support `sort_keys`/compact
separators/`ensure_ascii=True` directly, reproduce the same output byte for
byte: keys sorted lexicographically, no spaces after `:` or `,`, and every
non-ASCII character emitted as a `\uXXXX` (lowercase hex) escape.

## Attestation header

A node MAY (recommended, not required at testnet stage) sign its response
and return the signature in the `X-Gatewayz-Attestation` response header.
Gatewayz verifies it against the operator's registered `payout_wallet_address`
(`verify_wallet_signature`, `src/security/wallet_signature.py` — standard EOA
`personal_sign` recovery, same primitive SIWE wallet auth uses) and, if
valid, marks the receipt `attested=true`. Unattested work is still recorded
and still eligible for payout (subject to W-B's spot-check sampling), just
without this extra signal.

**Message signed** (exact string, `|`-joined, no extra whitespace):

```
f"{billing_ref}|{model}|{prompt_hash}|{response_hash}|{prompt_tokens}|{completion_tokens}"
```

- `billing_ref` — Gatewayz's per-request correlation id. The node receives
  this as the `X-Gatewayz-Request-Id` request header on the inbound call
  from Gatewayz (the same header name Gatewayz's own `RequestIDMiddleware`
  sets on its response to the client, reused here for the node-facing leg).
- `model` — the bare model id *without* the `community/` prefix (e.g.
  `llama-3.1-8b-instruct`).
- `prompt_tokens` / `completion_tokens` — plain base-10 integers, as
  reported in the response's `usage` object.

Sign with the operator's registered payout wallet key (`eth_account`'s
`Account.sign_message(encode_defunct(text=message))` in Python, or the
equivalent `personal_sign` call in any EOA-signing library), and set the hex
signature (with `0x` prefix) as `X-Gatewayz-Attestation` on the HTTP
response.

## Known limitation (v1)

Attestation capture currently only applies to **non-streaming** responses —
reading response headers from a streaming OpenAI-SDK call requires a
different chunk-consumption shape (`with_streaming_response`) than the rest
of Gatewayz's provider adapters use for `stream()`. Streaming community
calls are still receipted (hashes, token counts, latency, status) and still
eligible for payout; they just can't currently carry a verified attestation
signature. Non-streaming is recommended for operators who want attested
work counted preferentially.
