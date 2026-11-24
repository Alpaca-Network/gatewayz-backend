# Discount credits

Discount credits are a second balance on an account, next to the paid balance.
They come only from the Gatewayz vault. When billing for discount credits is
switched on, each request is paid mostly from the discount credits balance and
the rest from the paid balance, until the discount credits run out. After that
the account pays full price from its paid balance, as before.

One discount credit covers one US dollar of list price.

## How a request is billed

- Off (the default): billing is exactly what it was. No discount code path
  runs and no extra database call is made.
- On: a configured part of the request's list price (99% by default, and never
  more than 99%) is drawn from the discount credits balance. The remainder is
  charged to the paid balance. Every request therefore still writes its normal
  credit transaction and still counts towards the daily usage limit.
- If the discount credits balance covers only part of that share, it pays what
  it can and the paid balance pays the rest.
- If anything goes wrong while reading or drawing discount credits, the request
  is charged full price. A request never fails because of discount credits.
- A retried charge with the same billing request id draws nothing new.
- A refused charge puts the discount credits back. A refund returns discount
  credits to the discount credits balance and only the paid part to the paid
  balance.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `VAULT_DISCOUNT_BILLING_ENABLED` | `false` | Master switch. Off means billing is unchanged. |
| `VAULT_DISCOUNT_PERCENT` | `0.99` | Share of a request's list price drawn from discount credits. Capped at 99% in code and in the SQL. |
| `VAULT_DISCOUNT_HOLD_TTL_SECONDS` | `900` | How long a pre-request hold on the balance lives if the request is never billed (clamped to between one minute and one hour). |

There are no aliases for older key names.

## Where billing calls it

All calls go through `src/services/discount_credits/billing.py`.

| Caller | Function used | Purpose |
|---|---|---|
| `src/db/users.py::deduct_credits` | `discount_enabled`, `apply_for_request`, `reverse_for_request` | Draw the discount credits for a billed request; put them back if the charge is refused. |
| `src/handlers/chat_handler.py::_check_credit_sufficiency` | `discount_enabled`, `discount_aware_credits_async` | Pre-request check: hold this request's discount credits and count them. |
| `src/routes/chat.py::chat_completions` | `discount_enabled`, `discount_aware_credits_async` | Same pre-request check on the chat route. |
| `src/routes/audio.py::create_transcription`, `create_transcription_base64` | `discount_aware_credits_async` | Same pre-request check for audio. |
| `src/services/billing/simple_metering.py::precheck_credits` | `discount_aware_credits_async` | Same pre-request check for simply metered endpoints. |
| `src/services/billing/credit_handler.py::refund_credits` | `discount_enabled`, `reverse_or_none`, `charged_credits_for` | Refunds: return discount credits and cap the paid refund at what was actually charged. |

The database calls behind these are in `src/db/inference_discount.py`
(`hold_discount`, `apply_discount`, `reverse_discount`).

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

- `discount.enabled` reflects `VAULT_DISCOUNT_BILLING_ENABLED`.
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
- No daily cap is applied in the Python code. A per-account daily usage cap is
  planned separately.

## Linking a Cardano wallet

A user links a Cardano stake address once, by signing a server-issued message
(`POST /auth/wallet/cardano/nonce`, then `POST /auth/wallet/cardano/link`).
Linking is refused for residents or citizens of the United States or Canada.
The attestation can also be recorded on its own with `POST /wallet/eligibility`.
The code is in `src/routes/wallet_auth_cardano.py` and `src/services/wallet/`.

## Historical names

The tables and SQL functions keep the names they were created with:
`inference_discount_balances`, `inference_discount_ledger`,
`inference_discount_holds`, `discount_grant`, `discount_hold`,
`discount_apply`, `discount_reverse`, and the eligibility table
`delegation_eligibility`. They were introduced by an earlier, retired program;
only the names are historical.

## Pilot list

`VAULT_DISCOUNT_PILOT_USER_IDS` is a comma-separated list of account ids. While it names at least one account, billing draws on discount credits for those accounts only. Every other account is billed exactly as with the switch off, with no extra database call, and `GET /discount-credits` shows them the disabled shape.

Use it to try discount credits on a few accounts in production before opening them to everyone. Leave it empty to include every account (the switch must still be on).

| `VAULT_DISCOUNT_BILLING_ENABLED` | `VAULT_DISCOUNT_PILOT_USER_IDS` | Who gets the discount |
|---|---|---|
| off | anything | Nobody |
| on | `10,37` | Accounts 10 and 37 |
| on | empty | Every account with a balance |
