# Gatewayz for GenLayer validators and cohort teams

This guide gets a GenLayer validator, or a cohort team's app, running on
Gatewayz without any code changes on your side. You change one block of YAML
and set one environment variable.

The guarantees:

- **You get the exact model you configured, or a 400.** Gatewayz never quietly
  swaps in a different model for a validator key.
- **Validator keys run in no-logging mode.** Only what billing needs is stored.
- **One key reaches every model in the catalog.** Spend caps fail with typed
  errors.

> Status: the validator flag (`purpose: "validator"`) and the per-model status
> endpoint (`GET /v1/status/model`) arrive with the PR that adds this guide.
> Until that is deployed, a key cannot be switched to validator mode.

---

## 1. Base URL and the GenVM `host` value

Gatewayz serves OpenAI-compatible chat at
`https://api.gatewayz.ai/v1/chat/completions`.

In `genvm-module-llm.yaml`, set the host **without** `/v1`:

```yaml
host: https://api.gatewayz.ai
```

GenVM's `openai-compatible` provider appends the path itself. From
`genlayerlabs/genvm`, `modules/implementation/src/llm/providers.rs` (commit
`abb71bf8`, L325; L406 for JSON mode):

```rust
let url = format!("{}/v1/chat/completions", self.config.host);
```

If you set `host: https://api.gatewayz.ai/v1`, requests go to
`/v1/v1/chat/completions` and fail. GenVM's own OpenRouter example follows the
same convention: `host: https://openrouter.ai/api`.

GenVM sends `Authorization: Bearer <key>`, which is how Gatewayz authenticates.

## 2. Create a validator key

Create the key in the Gatewayz dashboard, or call the API with any existing key
on the account:

```bash
curl -s https://api.gatewayz.ai/user/api-keys \
  -H "Authorization: Bearer $EXISTING_GATEWAYZ_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key_name": "genlayer-validator-1", "environment_tag": "live", "purpose": "validator"}'
```

To switch an existing key, send `PUT /user/api-keys/{key_id}` with
`{"purpose": "validator"}`. Send `{"purpose": "general"}` to switch it back.

`purpose` accepts only `"validator"`, `"general"` or nothing. Any other value is
rejected with a 400 and no key is created.

Keep in mind:

- **Live keys need a payment on the account.** Without one, the API returns 402
  with a `payment_required` body that explains how to fix it. Test keys work
  without a payment but are rate limited.
- **A switch can take up to 60 seconds to apply everywhere.** It applies at once
  on the server that handled the change. Other servers pick it up when their
  60-second user cache expires.
- **Keep keys out of the YAML.** Put the key in the node's `.env`, for example
  `GATEWAYZKEY=gw_live_...`, and reference it as `${ENV[GATEWAYZKEY]}`.

### What validator mode stores, and what it does not

| Per-request record | General key | Validator key |
|---|---|---|
| `credit_transactions` debit (amount, model, token counts, request ref) | stored | **stored**, because this is the charge |
| `usage_records` (user, key id, model, tokens, cost, timestamp) | stored | **stored**, because this is the billing record |
| `api_keys_new.requests_used` (request-cap counter) and `last_used_at` | updated | **updated**, because the cap depends on them |
| `chat_completion_requests` row (latency, provider, status, error text, tag) | stored | **not stored** |
| `activity_log` row | stored | **not stored** |
| Chat history (prompt and completion text), even if `session_id` is sent | stored | **not stored** |
| Per-request audit log line with client IP and user agent | logged | **not logged** |
| Auto web search (sends the prompt to a search vendor and rewrites it) | on by default | **off, always** |

Because a validator key never writes a `chat_completion_requests` row, it does
not show up in anything built on that table:

- rankings
- per-model and per-provider stats
- the per-key arrivals view
- the tag usage export

The only thing it contributes to is billing totals.

Some things are the same for every key:

- Gatewayz removes client identity fields (`user`, request ids) before calling
  the model provider.
- Sentry events contain no request bodies, prompts or client IPs.
- Prometheus metrics have no user, key or IP labels.
- Rate limits use short-lived Redis counters.

## 3. `genvm-module-llm.yaml`

```yaml
backends:
  gatewayz:
    enabled: true
    host: https://api.gatewayz.ai
    provider: openai-compatible
    key: ${ENV[GATEWAYZKEY]}
    models:
      openai/gpt-4o-mini:
        supports_json: true
        supports_image: true
        meta:
          greybox: { text: 1, image: 1 }
      anthropic/claude-haiku-4-5-20251001:
        supports_json: true
        supports_image: false
        meta:
          greybox: { text: 2 }
      zai/glm-5:
        supports_json: true
        supports_image: false
        meta:
          greybox: { text: 3 }
    meta:
      priority: 50
```

Notes on these settings:

- **Model naming.** Use the `id` exactly as `GET https://api.gatewayz.ai/v1/models`
  returns it, for example `openai/gpt-4o-mini` or `zai/glm-5`. That endpoint is
  public.
  - A bare vendor name such as `claude-sonnet-4-6` is accepted only when it
    matches exactly one catalog model. If it matches several, you get
    `400 model_ambiguous`.
  - Router ids are refused for validator keys with
    `400 model_substitution_refused`. These are `auto`, `openrouter/auto`,
    `router:*` and `gatewayz-router`.
- **Default script uses only the first model.** GenVM's default Lua script
  (`genvm-llm-default.lua`, L129) sends each backend's **first** model only, so
  list your preferred model first. The greybox script
  (`genvm-llm-greybox.lua`, node v0.5.7+) uses every model that has
  `meta.greybox`. A lower number is tried first.
- **`use_max_completion_tokens`: leave it unset (false).** Send `max_tokens`.
  Gatewayz renames it to `max_completion_tokens` for the OpenAI models that
  require that name (`src/services/providers/reasoning_effort.py`,
  `normalize_token_limit`). Gatewayz does not read a `max_completion_tokens`
  sent by the client.

### `supports_json` / `supports_image`: evidence, not assumption

The values above come from the live catalog (`GET /v1/models`), measured on
2026-10-05:

- **JSON.** `supports_json: true` only where the model's `supported_parameters`
  include `response_format`. GenVM's JSON mode sends
  `response_format: {"type": "json_object"}`.
- **Images.** `supports_image: true` only where both of these hold:
  - `capabilities.vision` is `true`
  - `metadata.architecture.input_modalities` includes `image`

  For several models these two fields disagree. For example, the Claude models
  show `vision: true` with text-only input modalities. Where they disagree, this
  guide sets `supports_image: false`.

| Model id | `response_format` listed | vision and image input both listed |
|---|---|---|
| `openai/gpt-4o`, `openai/gpt-4o-mini` (and dated snapshots), `openai/gpt-4-turbo` | yes | yes |
| `anthropic/claude-*` (all 13) | yes | no (fields disagree) |
| `zai/glm-*` (all 11), `grok-*` (all 6), `moonshot/kimi-*` (all 3) | yes | no |
| `openai/gpt-5*` family | yes | no (fields disagree) |
| `openai/gpt-6*`, `openai/gpt-audio*` | **no** | no |

The table records what the catalog advertises. Before relying on a model in
production, send one `json_object` request through it.

## 4. Limits and failure codes

Errors come back in the OpenAI envelope, for example
`{"error": {"message", "type", "code"}}`. Branch on the `code` field, not on the
message text.

| HTTP | `code` | Meaning | Retry? |
|---|---|---|---|
| 400 | `model_not_found` | That model id is not in the catalog | No. Fix the id. |
| 400 | `model_ambiguous` | A bare name matched more than one model | No. Send the full id. |
| 400 | `model_not_priced` | The id is unknown or deliberately unpriced, including fully-qualified ids that are not in the catalog | No |
| 400 | `model_substitution_refused` | Validator key only: the request would have been served by a different model, either through a router id or a provider mapping | No |
| 401 | `invalid_api_key` / `api_key_inactive` / `api_key_expired` | Key problem | No |
| 402 | `request_cap_exhausted` | The key hit its `max_requests` cap | No. The cap must be raised. |
| 402 | `insufficient_credits` | Balance is empty (auth-layer check) | No. Top up first. |
| 402 | (plain `detail`) | Balance cannot cover this request (per-request pre-check). The body is the string "Insufficient credits…" with no `code`. | No. Top up first. |
| 403 | `ip_not_allowed` / `domain_not_allowed` | The key's IP or domain allowlist rejected the request | No |
| 422 | `model_pricing_missing` | Pricing data is temporarily unavailable for this model | Later |
| 429 | (rate limit, `Retry-After` header) | Per-key rate or concurrency limit | Yes, after `Retry-After` |
| 429 | (plain `detail`) | Plan limit or daily usage limit exceeded | After the limit window |
| 503 | `pricing_not_configured` | A catalog model is missing pricing. This is a Gatewayz problem and pages us. | Yes |

Sources: `src/security/deps.py` (`_KEY_FAILURE_MAP`),
`src/security/inference_gates.py`, `src/routes/chat.py`,
`src/services/key_purpose.py`.

### How GenVM reacts

GenVM treats any non-200 response as a provider failure. Source:
`modules/implementation/src/scripting/mod.rs` L441.

With the default script, it then moves to your next backend in `meta.priority`
order:

- Statuses 408, 429, 503 and 504 log "service is overloaded, looking for next".
- Any other status logs "provider failed" and also moves on.

So if your key hits its cap (402), the node falls through to your next backend.
It does not keep retrying Gatewayz. If you want the node to stop instead, make
Gatewayz the only backend.

**Cohort teams:** one key reaches every model in the catalog. To bound spend,
set `max_requests` on the key, or keep a low balance. The cap returns 402
`request_cap_exhausted` and a retry never clears it.

## 5. Per-model availability

These endpoints are public, so no key is needed:

- **`GET https://api.gatewayz.ai/v1/status/model?id=<full model id>`** returns
  every provider row for one model, plus an `available` boolean. `available` is
  true when at least one provider is currently measured as `operational` or
  `degraded`. A stale measurement shows as `unknown` and does not count as
  available. A model that is not monitored returns `404 model_not_monitored`.
- **`GET https://api.gatewayz.ai/v1/status/models`** lists per-model status rows.
  Filter with `?provider=` or `?status=`. Pages hold up to 1000 rows; use
  `?limit=` and `?offset=`.
- **`GET https://api.gatewayz.ai/v1/status/`** and **`/v1/status/providers`**
  give the overall summary and per-provider status.

Use the query-parameter form for single models. The older path form,
`/v1/status/models/{provider}/{model_id}`, cannot address an id that contains a
slash. Every catalog id except the `grok-*` models contains one.

## 6. Catalog coverage for GenLayer's default chains

GenLayer's greybox defaults are:

- **Text:** deepseek-v3.2 → qwen3-235b → claude-haiku-4.5 → kimi-k2 → glm-5 →
  llama-3.3
- **Image:** gpt-5.1-mini → gemini-3-flash → claude-haiku-4.5

Gatewayz serves the Anthropic, Moonshot (Kimi), Z.ai (GLM), OpenAI and xAI
entries. As of 2026-10-05, the live catalog has **no** Gemini, Mistral,
DeepSeek or Qwen models. Configure those through another backend until they are
listed in `GET /v1/models`.
