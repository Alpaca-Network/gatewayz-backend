# Discount credits

Discount credits are a second balance on an account, next to the paid balance.
They come only from the Gatewayz vault. When billing for discount credits is
switched on, each request is paid mostly from the discount credits balance and
the rest from the paid balance, until the discount credits run out. After that
the account pays full price from its paid balance, as before.

Balances are kept in US dollars to six decimal places. One discount credit is US$0.000001 of discount, so a balance of `5.000000` is five million credits.

## How a request is billed

- Off (the default): billing is exactly what it was. No discount code path
  runs and no extra database call is made.
- On: a configured part of the request's list price (99% by default, and never
  more than 99%) is drawn from the discount credits balance. The remainder is
  charged to the paid balance. Every request therefore still writes its normal
  credit transaction and still counts towards the daily usage limit.
- If the discount credits balance covers only part of that share, it pays what
  it can and the paid balance pays the rest.
- If drawing discount credits fails, the draw is tried once more (it is
  idempotent). If it fails again, any draw the failed calls may have made is
  reversed, and only then is the request charged the list price. A request is
  never both discounted and charged in full, and never fails because of
  discount credits.
- A retried charge with the same billing request id draws nothing new.
- A request settles against its own hold: lowering the daily cap after a hold
  was placed never shrinks that hold.
- A request that ends without being billed (provider error, nothing to charge,
  refused after the pre-check) releases its hold. If the release does not
  happen, the hold expires after `VAULT_DISCOUNT_HOLD_TTL_SECONDS`.
- A refused charge puts the discount credits back. A refund returns discount
  credits to the discount credits balance and only the paid part to the paid
  balance. Whether a request drew discount credits is read from the discount
  credits ledger, not from the switch, so this holds even after the switch or
  the pilot list changed. Each request is refunded at most once.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `VAULT_DISCOUNT_BILLING_ENABLED` | `false` | Master switch. Off means billing is unchanged. |
| `VAULT_DISCOUNT_PERCENT` | `0.99` | Share of a request's list price drawn from discount credits. Capped at 99% in code and in the SQL. |
| `VAULT_DISCOUNT_HOLD_TTL_SECONDS` | `900` | How long a pre-request hold on the balance lives if the request is never billed or released (clamped to between one minute and one hour). Must stay longer than the longest request: a hold that expires first no longer protects that request. |
| `VAULT_DISCOUNT_PILOT_USER_IDS` | empty | Which accounts the switch applies to: account ids, or `*` for all. Empty means nobody |

There are no aliases for older key names.

## Where billing calls it

All calls go through `src/services/discount_credits/billing.py`.

| Caller | Function used | Purpose |
|---|---|---|
| `src/db/users.py::deduct_credits` | `discount_enabled_for`, `apply_for_request`, `reverse_for_request`, `release_for_request` | Draw the discount credits for a billed request; put them back if the charge is refused; release the hold when an admin account is not charged. |
| `src/handlers/chat_handler.py::_check_credit_sufficiency` | `discount_enabled_for`, `discount_aware_credits_async` | Pre-request check: hold this request's discount credits and count them. |
| `src/handlers/chat_handler.py` (`process`, `process_stream`, `_charge_user`) | `release_for_request_async` | Release the hold when the request fails, is cancelled before the provider call, or has nothing to charge. |
| `src/routes/chat.py::chat_completions` | `discount_enabled_for`, `discount_aware_credits_async`, `release_for_request_async` | Same pre-request check on the chat route; release the hold on an error before billing. |
| `src/routes/audio.py::create_transcription`, `create_transcription_base64` | `discount_aware_credits_async` | Same pre-request check for audio. |
| `src/services/billing/simple_metering.py::precheck_credits` | `discount_aware_credits_async` | Same pre-request check for simply metered endpoints. |
| `src/services/billing/credit_handler.py::handle_credits_and_usage` | `release_for_request_async` | Release the hold when the cost is zero. |
| `src/services/billing/credit_handler.py::refund_credits` | `discount_drawn_for`, `reverse_or_none`, `charged_credits_for` | Refunds: if the request drew discount credits (from the ledger), return them and cap the paid refund at what was actually charged. |

The database calls behind these are in `src/db/inference_discount.py`
(`hold_discount`, `apply_discount`, `reverse_discount`, `release_discount`,
`find_use`).

The audio routes and `simple_metering` place holds but do not release them on
failure yet; their holds expire after `VAULT_DISCOUNT_HOLD_TTL_SECONDS`.

## Reading the balance

`GET /discount-credits` (authenticated) returns the caller's balance:

```json
{
  "success": true,
  "data": {
    "discount": {
      "enabled": false,
      "discount_percent": "0.99",
      "reward_share": "0",
      "balance_usd": "0.000000",
      "lifetime_granted_usd": "0.000000",
      "lifetime_used_usd": "0.000000"
    },
    "discount_history": [],
    "disclaimer": "..."
  }
}
```

- `discount.enabled` is true when the switch is on and it applies to the calling account (see "Who the switch applies to").
- `discount.discount_percent` is a fraction such as `"0.99"`; it is `null` for an account the switch does not apply to.
- While billing is off the balance fields are zero and the history is empty;
  no database read is made.
- `reward_share` is a leftover field kept so the response shape did not
  change. It is always `"0"` and should not be shown.
- `discount_history` lists the most recent movements, newest first.

The route lives in `src/routes/discount_credits.py`.

## Where discount credits come from

- In production, only the vault mirror job may grant discount credits, from
  on-chain mints. That job is not built yet.
- For tests and developer-run scripts,
  `src/db/inference_discount.py::grant_discount(user_id, amount_usd, request_id, ref)`
  wraps the SQL function `discount_grant`. It is idempotent on `request_id`.
- No HTTP route grants or adjusts discount credits.
- The per-account daily cap is applied in the database (see "Daily cap").

## Linking a Cardano wallet

A user links a Cardano stake address once, by signing a server-issued message
(`POST /auth/wallet/cardano/nonce`, then `POST /auth/wallet/cardano/link`).
Linking is refused for residents or citizens of the United States or Canada.
The attestation can also be recorded on its own with `POST /wallet/eligibility`.
The code is in `src/routes/wallet_auth_cardano.py` and `src/services/wallet/`.
Besides the attestation, the request country (a trusted header, or the client
IP looked up in a local country database) can refuse the link; see
[docs/wallet/ip-country.md](../wallet/ip-country.md).

## Historical names

The tables and SQL functions keep the names they were created with:
`inference_discount_balances`, `inference_discount_ledger`,
`inference_discount_holds`, `discount_grant`, `discount_hold`,
`discount_apply`, `discount_reverse`, and the eligibility table
`delegation_eligibility`. They were introduced by an earlier, retired program;
only the names are historical. That program's attribution functions
(`delegation_grant_attribution`, `delegation_reserve_attribution`,
`delegation_attribution_spent`) were dropped in
`supabase/migrations/20261126120000_discount_credits_hardening.sql`, and its
last functions (`delegation_account_spent`, `delegation_reserve_accrual`,
`delegation_claim_accrual`, `delegation_release_claim`) in
`supabase/migrations/20261126190500_drop_retired_delegation_functions.sql`.

## Who the switch applies to

`VAULT_DISCOUNT_PILOT_USER_IDS` decides which accounts the switch applies to.

| `VAULT_DISCOUNT_BILLING_ENABLED` | `VAULT_DISCOUNT_PILOT_USER_IDS` | Who gets the discount |
|---|---|---|
| off | anything | Nobody |
| on | empty, or nothing valid in it | **Nobody** |
| on | `10,37` | Accounts 10 and 37 |
| on | `*` | Every account with a balance |

An empty or mistyped value admits nobody, so a missing setting can never open discount billing to every account. Opening it to everyone takes an explicit `*`.

For an account the switch does not apply to, billing takes exactly the switch-off path: no draw, no hold, and `GET /discount-credits` returns the disabled shape (zeros, `discount_percent: null`) without reading the database. Every charge and hold call site checks `discount_enabled_for(user_id)`, never the switch alone; a test enforces that.

Refunds are the exception: a refund looks up whether the request drew discount credits (one ledger read), whatever the switch and the list say now. If it did, the discount goes back to the discount credits balance and the paid refund is capped at what was charged in paid credits. If that lookup fails, the refund is not paid and is logged for review.

## Daily cap

An account may use at most its daily cap of discount credits per UTC calendar day. The default is US$50. The account owner sets it.

| Rule | Detail |
|---|---|
| Reaching the cap | The request is not refused. `discount_apply` draws what is left of the cap (possibly nothing) and the rest is charged to the paid balance as usual |
| The day | UTC calendar day. Use counted on an earlier day reads as 0 |
| Lowering the cap | Applies at once |
| Raising the cap | Applies 24 hours after you raise it, so a raise can never be spent the day it is made. Raises recorded before migration 20261126120000 still apply from their date |
| A cap lowered while a request is in flight | The request still draws the hold it was admitted with, so today's use can end above the new cap; "left today" then reads 0 |
| A cap of 0 | Stops discount credits from being used |
| Refunds | Reversing a use made today frees that much of today's cap. Reversing an older use does not |
| Holds | A pre-request hold never reserves more than what is left of the cap |

`PUT /discount-credits/daily-cap` with `{"daily_cap_usd": "20"}` sets it (0 to 1,000,000). It needs the account's own sign-in key (a primary key or a session key); an inference API key gets 403 `account_key_required`. It is rate limited to 10 requests a minute. The answer has `status` `set` or `scheduled` and the same `daily_cap` object that `GET /discount-credits` returns under `discount`:

| Field | Meaning |
|---|---|
| `daily_cap_usd` | The cap in force today |
| `used_today_usd`, `left_today_usd` | Use so far today and what remains (never below 0) |
| `resets_at` | The next UTC midnight |
| `next_daily_cap_usd`, `next_daily_cap_from` | A raise waiting to take effect (its UTC date), or null |
| `next_daily_cap_at` | The exact time that raise takes effect (ISO, UTC), or null |

The rules live in the database (`supabase/migrations/20261125120000_discount_daily_cap.sql` and `20261126120000_discount_credits_hardening.sql`), under the same row lock as the balance, and are tested against a real database in `tests/sql/test_discount_daily_cap_sql.py` and `tests/sql/test_discount_credits_hardening_sql.py`.

Not built yet: a notification when the cap or the balance runs out, credit batches with expiry, and a ceiling on the cap.
