# GPU Provider Onboarding

Run a GPU and get paid in WAYZ for serving open-weight models to Gatewayz
users who opt in to community compute. This is the operator-facing guide
for Milestone 4's compute marketplace (gatewayz-backend #2261-#2267,
testnet stage). It assumes nothing about your setup beyond "a Linux box
with an NVIDIA GPU and a public HTTPS endpoint" — follow it top to bottom.

## Before you start: the trust disclosure

Read this section before registering. It is not boilerplate.

**You will see prompt content.** Unlike Gatewayz's other providers (OpenAI,
Anthropic, etc.), a community node *is* the compute — the request has to
reach your machine for you to answer it, so you see exactly what the user
sent and what your server replied. Gatewayz's identity firewall (see
`docs/security/ANONYMITY_THREAT_MODEL.md`) still applies to *you*: you
never receive the requester's user id, wallet address, email, IP address,
or API key, only the model id, the messages, and a per-request billing
reference used solely for payment attestation (see "Attestation" below).
But content is not identity, and content is exactly what you see.

**Traffic to you is opt-in only, never automatic.** A client has to
explicitly ask for a model id prefixed `community/` (e.g.
`community/llama-3.1-8b-instruct`). Community nodes are never part of an
automatic failover chain and are never selected by Gatewayz's smart
router — the `community/` prefix in the request *is* the user's consent.
If you're not comfortable with the above, don't run a node — or don't
register the models you'd rather not see prompts for.

**Only open-weight models are eligible**, and every node is reviewed by a
Gatewayz admin before it serves live traffic (see "Registration" below).

## Requirements

- Linux (any distro with a recent NVIDIA driver + CUDA userspace).
- NVIDIA GPU: **≥ 24 GB VRAM** to serve the `small` model class (models up
  to ~13B parameters, e.g. Llama-3.1-8B). Larger classes need
  proportionally more VRAM — see "Payouts" for the class boundaries.
- A publicly reachable **HTTPS** endpoint (plain HTTP is rejected at
  registration). A reverse proxy with a real TLS cert in front of your
  inference server (e.g. Caddy, nginx + certbot, or a tunnel provider)
  is the easiest way to get this on a home/colo box.
- Reasonable uptime. Gatewayz sweeps node liveness every 2 minutes: no
  heartbeat for 3 minutes marks a node `degraded`, 10 minutes marks it
  `offline` and it stops receiving traffic. Repeated offline periods hurt
  your `health_score` and therefore your spot-check pass rate (see
  "Verification").
- An Avalanche Fuji testnet wallet (any EOA — MetaMask, `cast wallet new`,
  etc.) to receive payouts and, optionally, to sign attestations.

## 1. Start your inference server

vLLM first — it's what this marketplace is built and tested against.
Ollama and other OpenAI-compatible servers should work too since Gatewayz
only talks the standard `/v1/models` and `/v1/chat/completions` shapes,
but they're untested here.

```bash
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --served-model-name llama-3.1-8b-instruct \
  --host 0.0.0.0 \
  --port 8000
```

Use `--served-model-name` to pick the exact id you'll register (see
step 3) — **do not** include a `community/` prefix here. Gatewayz strips
that prefix before routing to your node; your server only ever sees the
bare model id you registered (e.g. `llama-3.1-8b-instruct`), never
`community/llama-3.1-8b-instruct`.

Put a TLS-terminating reverse proxy in front of this (port 8000 stays
internal). Confirm it works from outside your network:

```bash
curl https://your-node.example.com/v1/models
```

## 2. Register as a provider

```http
POST /gpu/providers
Authorization: Bearer gw_live_your_api_key
Content-Type: application/json

{
  "display_name": "My GPU Farm",
  "payout_wallet_address": "0xYourFujiWallet",
  "contact_email": "you@example.com",
  "region_default": "us-east"
}
```

`payout_wallet_address` must already be linked to your Gatewayz account
(link it first via the wallet-auth flow — `docs/api.md`'s "Wallet
Authentication (SIWE)" section — if you haven't). One provider record per
account. You get back `status: "pending"`.

**Wait for admin approval.** An admin reviews the request (open-weight
models only, reasonable operator identity) and flips your status to
`approved` or `suspended`. Check your status any time:

```http
GET /gpu/providers/me
Authorization: Bearer gw_live_your_api_key
```

Node registration (step 3) is rejected until your provider is `approved`.

## 3. Register a node

```http
POST /gpu/nodes
Authorization: Bearer gw_live_your_api_key
Content-Type: application/json

{
  "name": "farm-node-1",
  "region": "us-east",
  "gpu_model": "RTX 4090",
  "vram_gb": 24,
  "bandwidth_mbps": 1000,
  "endpoint_url": "https://your-node.example.com",
  "endpoint_api_key": "any-bearer-token-your-server-expects",
  "models": [{"id": "llama-3.1-8b-instruct", "max_context": 8192}]
}
```

`endpoint_api_key` is **required and cannot be empty** (an empty string
422s) — vLLM's default OpenAI-compat server doesn't require a bearer
token, so if yours doesn't either, put any random placeholder string here
rather than trying to omit it. Gatewayz still sends it as a bearer token
on every call to your endpoint, so if you'd rather actually require one,
generate a real one (e.g. `openssl rand -hex 32`) and configure vLLM to
expect it (`--api-key`).

Gatewayz probes `GET {endpoint_url}/v1/models` at registration time and
checks your declared model ids are actually being served (5 second
timeout) — have vLLM running *before* you call this.

The response includes a **`node_token`** (`gw_node_...`) — **shown once,
never again**. Save it immediately; it's what your node agent
authenticates heartbeats with. Losing it means `POST
/gpu/nodes/{id}/rotate-token` and updating your agent's config.

## 4. Run the node agent

`scripts/gpu_node_agent.py` sends a heartbeat every 30 seconds so
Gatewayz knows your node is alive and how loaded it is:

```bash
python scripts/gpu_node_agent.py \
  --gateway https://api.gatewayz.ai \
  --node-token gw_node_the_token_from_step_3 \
  --node-id 123 \
  --local-vllm http://127.0.0.1:8000
```

It self-checks your local server's `/v1/models` and best-effort reads
in-flight request counts from vLLM's `/metrics` (0 if unavailable),
POSTs a heartbeat, and retries with exponential backoff on failure. Run
it under a process supervisor (systemd, supervisord, tmux — your choice)
so it restarts if it crashes.

### Attested heartbeats (optional but recommended)

Pass `--wallet-keyfile /path/to/key` (a file containing your payout
wallet's private key, one line, hex) to have each heartbeat additionally
signed. Gatewayz marks a signed, valid heartbeat `attested_heartbeat:
true`, which lowers your spot-check sampling rate (see "Verification") —
signing costs you nothing and reduces how often you're re-checked.

**Keep this file off shared machines and out of version control**, and
lock down its permissions so only the account running the agent can read
it: `chmod 600 ~/.gatewayz/payout-key`. It holds your payout wallet's
key — the same key you'd use to receive WAYZ.

### Response attestation (optional, recommended)

`--attest-proxy PORT` runs a tiny local reverse proxy in front of your
vLLM server that adds an `X-Gatewayz-Attestation` header (a wallet
signature over the exchange) to every non-streaming response. This lets
Gatewayz's spot-check verifier trust your own signed claim about what you
served, without necessarily re-running the request. Requires
`--wallet-keyfile`. Point your public HTTPS reverse proxy at this port
instead of vLLM directly:

```bash
python scripts/gpu_node_agent.py \
  --gateway https://api.gatewayz.ai \
  --node-token gw_node_... \
  --node-id 123 \
  --local-vllm http://127.0.0.1:8000 \
  --wallet-keyfile ~/.gatewayz/payout-key \
  --attest-proxy 8080
# TLS reverse proxy -> 127.0.0.1:8080 -> vLLM on 127.0.0.1:8000
```

The exact attestation algorithm — which bytes get hashed and how the
signed message is built — is specified in `docs/api.md`'s "GPU
Marketplace" section. **v1 limitation**: streaming responses aren't
attested (only the non-streaming shape can be hashed as a single
document); the proxy still forwards streamed responses, just without the
header.

## 5. Confirm you're live

Check the public dashboard at `/gpu` (or `GET /gpu/public/nodes`) — your
node should show up by name, region, GPU model, and status once its
first heartbeat lands and it's `active`.

## Payouts

Payouts are in **WAYZ**, Gatewayz's utility token on **Avalanche Fuji
testnet** (chain id `43113`), sent to your `payout_wallet_address` — a
real token address on a real chain, but the network itself is a testnet,
so it has no monetary value yet.

Every unit of verified work accrues WAYZ per 1k tokens, by model size
class (seeded rates, `provider_payout_rates`):

| Class | Model size | Rate (WAYZ / 1k tokens) |
|---|---|---|
| `small` | ≤ 13B params | 0.05 |
| `medium` | ≤ 34B params | 0.10 |
| `large` | > 34B params | 0.25 |

These are testnet values and may change; check your actual accrued/settled
amounts any time at `GET /gpu/providers/me/earnings`.

**Model class is an exact allow-list, not a guess from your model's
name.** Only a fixed set of known open-weight model ids is recognized —
declaring a model id Gatewayz doesn't recognize means that work is
**not payable** (`skipped`, not `verified`), even if it otherwise passes
every check. Stick to well-known instruct model ids (the kind named
throughout this doc, e.g. `llama-3.1-8b-instruct`) to be sure your work
counts.

**Effective rate at testnet launch:** until Gatewayz has a configured
spot-check reference provider (an operator-side setting, not something
you control) *and* your work is attested, `medium`/`large`-class work is
still paid at the `small` rate as a safety margin against a node
misreporting output quality within loose token-count checks — the
allow-list above still determines *whether* you're paid at all, this only
caps *how much*. Attested work (`--wallet-keyfile`, ideally
`--attest-proxy`) is the one lever you control to get closer to full
rate once a reference provider is configured.

- Earnings accrue only from **verified** work (see "Verification" below)
  — unsampled-and-unresolved, unverified, or failed-verification work is
  unpaid.
- Settlement runs **daily**: any provider with ≥ 10 WAYZ accrued gets a
  single on-chain `transfer()` to their payout wallet, capped per run
  across all providers combined.
- Every settlement gets a transaction hash you can look up on
  [Snowtrace (Fuji)](https://testnet.snowtrace.io/).
- `GET /gpu/providers/me/earnings` lists accrued/settled totals, your
  last 50 work rows (no prompt/response content — see the threat model),
  and settlement history with Snowtrace links.

## Verification rules & penalties

Gatewayz can't take your word for every response, and doesn't store
prompts to check by default (see the threat model). Instead:

- A random sample of your completed requests (`COMMUNITY_SPOTCHECK_RATE`,
  5% by default — **doubled** for nodes without attested heartbeats) is
  spot-checked: the same prompt is re-run against your node at
  `temperature=0`, and the reply must be non-empty with a token count
  within 25% of what you reported. If Gatewayz has a trusted reference
  provider configured for your model, the reply is additionally compared
  against that provider's reply for prefix similarity — a stronger check
  that's only active once a reference provider is configured (see
  "Payouts" for how this also affects your effective rate).
- **Pass** → the work is marked `verified` and earnings accrue.
- **Fail** → the work is marked `failed`, its earnings are voided, and
  your node's `health_score` drops by 20. **Three failures in 24 hours
  disables the node** (and you're notified) — you'll need to re-register
  or contact support.
- Work that isn't sampled is marked `verified` automatically after 24
  hours, *unless* your node's failure rate that day was ≥ 5%, in which
  case it's `skipped` (unpaid) rather than assumed good.

Practical takeaway: run `--wallet-keyfile` (and ideally `--attest-proxy`)
so you're spot-checked less often, and don't return garbage — a bad day
can disable your node, not just cost you that request's payout.

## Support

Something not working, or a question this doc doesn't answer? Open an
issue against gatewayz-backend referencing #2267, or reach out on the
Gatewayz Discord/support channel listed at
[gatewayz.ai](https://gatewayz.ai). Don't paste your node token or wallet
private key into any support channel — Gatewayz staff will never ask for
either.
