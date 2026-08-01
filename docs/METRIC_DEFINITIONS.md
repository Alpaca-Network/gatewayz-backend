# Metric Definitions

Single source of truth for every number Gatewayz reports — in dashboards, in the deck, and in diligence. If a figure appears anywhere and is not defined here, it is not a metric.

Machine-readable copy: `GET /admin/payers/definitions`. Implementation: `src/services/payer_metrics.py`.

---

## Why this document exists

A credit-farming bot wave inflated the signup count. The number itself is dead, but the deeper problem was that no metric had a written definition, so the same word meant different things in the product dashboard and in a conversation with an investor. Every metric below has exactly one definition and one implementation.

**Standing rule: no metric derived from signups or API key counts.** Both were farmable. Everything reported is derived from settled payments and served requests.

---

## Core definitions

### Settled payment
A row in `payments` with `status` in (`succeeded`, `completed`, `paid`).

Pending, failed, refunded and disputed payments are excluded. Amounts are read from `amount_usd` (dollars) when present, falling back to Stripe's `amount` (cents ÷ 100) on older rows.

### Paying account
A `user_id` with **at least one settled payment of any amount, ever**.

Not a signup. Not an API key holder. Not a trial user. Not someone with granted credits who never paid.

> This is the account count used in every external communication. When the deck says "100 paying accounts", it means 100 distinct `user_id`s that have sent us money.

### New paying account
An account whose **first** settled payment falls inside the reporting window.

First-payment date, not any-payment date. A customer who topped up in week 1 and again in week 3 is one new paying account in week 1 and zero in week 3. Counting them twice would turn retention into fake acquisition.

### Credit revenue (USD)
Sum of settled payment amounts inside the window.

**Gross credit purchases, not net revenue.** Provider costs are not deducted. Gateway margins are thin and the take-rate story is told separately — see the honesty note below.

### Second top-up rate (%)
Of all paying accounts, the percentage with **≥ 2 settled payments**.

`null` when there are no paying accounts. That is "no data", not "0% retention" — a zero here would misrepresent an empty denominator as a failure.

GTM target: 40%+.

### Tokens through gateway
Sum of `input_tokens + output_tokens` across `chat_completion_requests` in the window.

Counts all served traffic regardless of who paid, including free-tier and trial usage. This is a volume metric, not a revenue metric — do not divide it by revenue to imply a rate.

### Week-over-week (WoW) %
`(current − previous) / previous × 100`

`null` when the previous period was zero. Growth from zero is undefined, not infinite. Reports render this as "n/a (no prior-week baseline)" and never as a large number.

---

## Cache metrics

Introduced with cache-aware billing. Implementation: `src/services/pricing/cache_pricing.py`.

### Cache read tokens
Input tokens served from a provider's prompt cache. Reported by the provider as `cache_read_input_tokens` (Anthropic) or `prompt_tokens_details.cached_tokens` (OpenAI).

### Cache write tokens
Input tokens written into the cache on the turn that populated it (`cache_creation_input_tokens`).

### Prompt tokens
**Inclusive** of cache reads and cache writes. Anthropic reports `input_tokens` excluding cached tokens; the gateway adds all three classes together so `prompt_tokens` always means "every input token in this request".

### Cache savings (USD)
What the request would have cost with every input token billed at the full input rate, minus what it actually cost.

This is the figure behind any cost-advantage claim. It is computed per request and stored, not reconstructed later from aggregates.

### Cache hit rate (%)
`cache_read_tokens / prompt_tokens × 100` over the window.

> **Publishing gate:** if the benchmark harness reports zero cache reads across a run with `cache_control` set, caching is broken and no cost-advantage claim may be published from that run. The harness emits this as an explicit warning (`scripts/benchmarks/coding_benchmark.py`).

---

## Benchmark metrics

Implementation: `scripts/benchmarks/coding_benchmark.py`.

### Time to first token (TTFT)
Milliseconds from request send to the first chunk containing content or a tool call. The opening `role` chunk does not count — it carries no output and counting it would flatter the number.

### Total time
Milliseconds from request send to stream close.

### Tokens per second
`completion_tokens / (total_ms / 1000)`. `null` when no completion tokens were produced.

### p50 / p95
Nearest-rank percentiles over successful samples only. Failed requests are counted separately and never averaged in.

Reported alongside the mean because an interactive coding agent is judged on its bad turns, not its average one.

---

## Honesty notes

These are the things an investor will probe. Stating them up front is cheaper than being caught.

1. **The 42K signups were contaminated.** A credit-farming bot wave inflated them. The number is retired. Payment gating on live API key issuance (`src/services/payment_gate.py`), zero signup credits, and zero referral payouts closed the three doors. All reported metrics start from a clean Day 0.

2. **Credit revenue is gross, not net.** Gateway margins are thin. The raise narrative rests on payer count and growth slope, not gross profit. Take-rate economics are a separate conversation and should be volunteered, not extracted.

3. **Token volume includes non-paying usage.** It measures gateway load, not monetisation.

4. **WoW on small numbers is volatile.** At double-digit payer counts a single account moves the percentage by several points. Report the absolute count next to every percentage.

5. **Benchmark numbers are point-in-time.** Provider latency varies by time of day and load. Every published benchmark states its run timestamp and sample count, and direct-vs-gateway legs run back to back so they see comparable provider conditions.
