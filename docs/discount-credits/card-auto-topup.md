# Card auto top-up

Billing order for a request: **discount credits, then the paid balance, then
a saved card, then refuse.** Until this was built the backend could not
charge a card for usage: a top-up was a one-off Stripe Checkout payment that
saved nothing. Code: `src/services/billing/card_auto_topup.py`, routes in
`src/routes/billing_card.py`, migration
`supabase/migrations/20261127200000_card_auto_topup.sql`.

## Settings (all off or empty by default)

| Setting | Default | Meaning |
|---|---|---|
| `CARD_AUTO_TOPUP_ENABLED` | `false` | Master switch. Off: no card can be saved, nothing is charged, the pre-checks make no extra call |
| `CARD_AUTO_TOPUP_USER_IDS` | empty | Accounts it applies to: ids, or `*` for all. Empty means nobody |
| `CARD_AUTO_TOPUP_DEFAULT_AMOUNT_CENTS` | `2000` | Amount per top-up until the account picks one ($20) |
| `CARD_AUTO_TOPUP_MIN_AMOUNT_CENTS` / `_MAX_` | `500` / `50000` | Range an account may pick from ($5 to $500) |
| `CARD_AUTO_TOPUP_DAILY_LIMIT_CENTS` | `10000` | Most charged per account per UTC day ($100). Also the default; an account may pick a lower limit only |
| `CARD_AUTO_TOPUP_TIMEOUT_SECONDS` | `20` | How long a request waits for the charge |
| `CARD_AUTO_TOPUP_COOLDOWN_SECONDS` | `600` | Pause after a charge that failed for a non-card reason |

The failure email uses `VAULT_DISCOUNT_NOTIFY_EMAIL_ENABLED` (off by default),
like the other discount credits notifications.

## Saving a card

1. `POST /user/billing/card/setup-session` (sign-in key only) creates the
   account's Stripe customer if it has none (stored in
   `users.stripe_customer_id`, looked up by id from then on, never by email)
   and a Checkout session in **setup** mode. The return URLs are fixed
   server-side: `/settings/credits?card_setup=success&session_id=...`.
2. On return the app calls `POST /user/billing/card/confirm {session_id}`.
   The backend checks the session is a card setup session for this account
   and its Stripe customer, that it is complete and its SetupIntent
   succeeded, then stores the payment method id and a display summary
   (brand, last 4, expiry). The `checkout.session.completed` webhook does the
   same, idempotently, if the app never returns.
3. `PUT /user/billing/auto-topup {enabled, amount_cents?, daily_limit_cents?}`
   switches it on (needs a saved card) or off.
4. `DELETE /user/billing/card` stops using the card and switches auto top-up
   off. The card is also detached in Stripe unless the account has an active
   subscription that may renew on it.
5. `GET /user/billing/card` returns the card summary, the settings, what was
   charged today and the allowed range.

The legacy `users.settings.auto_topup_*` keys (written by an older settings
form that never charged anything) are ignored: only the new `auto_topup_on`
column, set through the route above, allows a charge.

## Topping up

When a pre-check (chat completions and the chat handler it dispatches to,
embeddings and paid tools) finds that the
paid balance, after discount credits, cannot cover the request's maximum
cost:

1. Skip at once (no DB or Stripe call) unless the feature is on for the
   account and its row shows auto top-up on, a saved card and no pause.
2. Skip when one top-up would not cover the shortfall (the charge would not
   admit the request).
3. `auto_topup_begin` (under the users row lock) refuses when another attempt
   is pending, a recent attempt failed for a non-card reason (cooldown), or
   today's pending + succeeded + unknown charges plus this one exceed the
   daily limit. Otherwise it records a pending attempt with a new UUID.
4. A `payments` row is created, then a PaymentIntent with `off_session=true,
   confirm=true` for the top-up amount, idempotency key
   `gatewayz-auto-topup-<attempt uuid>`, metadata `source=auto_topup`.
5. On `succeeded` the paid balance is credited (amount less
   `CREDIT_TOPUP_FEE_RATE`, as for a manual top-up) under the same key the
   `payment_intent.succeeded` handler uses (`pi:<id>`), so the webhook cannot
   credit it twice. The pre-check then runs again with the new balance.

Audio routes do not top up yet.

## Failure states

| Outcome | Cause | Effect |
|---|---|---|
| `declined` | Card declined (any card error except SCA) | PaymentIntent cancelled, auto top-up **paused**, request refused (402), one email |
| `requires_action` | Bank wants the customer to confirm (SCA, `authentication_required`) | Same as declined: off-session nobody can confirm |
| `failed` + `card_unusable` | Saved card or customer gone in Stripe | Paused, one email |
| `failed` | Stripe auth, rate limit, other request error: nothing charged | Not paused; cooldown |
| `unknown` | Stripe unreachable or timed out, or still processing | Counted against the daily limit; not paused; cooldown; the webhook settles it |
| refused | daily limit, in flight, cooldown, paused, no card, amount too small | Nothing charged, request refused as before |

A pause is cleared when the account saves a card or switches auto top-up on
again. Each worker also remembers a refusal for a short while (until UTC
midnight for the daily limit) so a refused account does not cost a database
call on every request.

## Webhook events

| Event | Use |
|---|---|
| `checkout.session.completed` (mode `setup`, metadata `purpose=gatewayz_save_card`) | Store the saved card. Setup sessions never grant credits |
| `payment_intent.succeeded` (metadata `source=auto_topup`) | Credit the top-up if the request path did not (idempotent); checks amount and account against the attempt |
| `payment_intent.payment_failed` (metadata `source=auto_topup`) | Record a decline the request path missed; pause and email once |
| `payment_method.detached` | A card removed in Stripe stops being used (matched by payment method id) |

`payment_method.detached` must be added to the webhook endpoint's events in
the Stripe dashboard. The other three are events the backend already
handles for manual top-ups; check they are selected too.
