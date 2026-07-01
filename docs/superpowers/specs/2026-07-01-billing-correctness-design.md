# Billing Correctness Fixes — Design

Date: 2026-07-01
Status: Approved

## Context

A full audit of the subscription/credit/top-up system (4 parallel agents across backend + `gatewayz-frontend`) found:

1. Paying `basic`/`max` tier subscribers get correct dollar credits but never receive their rate-limit entitlement upgrade — they're silently stuck on default trial request/token limits.
2. The one-time top-up flow enforces a $0.50 minimum, not the intended $5.
3. There is no "first top-up bonus" mechanism at all.
4. The referral program grants the *referrer* $10 in free credits for zero payment, violating the "no free credits" policy.
5. Dead trial-credit code exists (inert, `TRIAL_DURATION_DAYS=0`, zero production callers) but still present and confusing.
6. The frontend (`gatewayz-frontend`) actively markets "free trial credits" and a "free credits today" tagline, which contradicts backend policy — plus a stale $3 threshold and a divergent, possibly-dead credit-package table vs. the backend's real `/api/stripe/credit-packages`.

Policy going forward: **no user ever receives free credits**, with exactly one exception — a one-time **$5 bonus** granted when a user's **first** top-up is **$5 or more**. Minimum top-up amount is **$5**.

## Scope

In scope for this pass:
- Backend: subscription tier→plan_id fix, top-up minimum, first-topup bonus, referral bonus removal (referrer side only), dead trial code removal.
- Frontend: remove/rescope free-credit marketing copy, raise top-up input minimum to $5.

Out of scope (deferred, noted only):
- Consolidating the 3 duplicate frontend pricing-config sources into one.
- Reconciling the frontend's dollar-denominated `creditPackages` table against the backend's cent-denominated `/api/stripe/credit-packages` response (needs confirmation of whether the frontend table is even wired to checkout).
- `get-credits-button.tsx`, `credits-display.tsx` (only grepped, not read).

## 1. Subscription tier → plan rate-limit fix

**Root cause**: `get_plan_id_by_tier()` (`src/db/plans.py:138`) does `ilike("name", f"%{tier}%")` against `plans.name` (Free, Free Trial, Starter, Professional, Business, Enterprise, Admin), but is called with tier codes `basic`/`pro`/`max` (from `subscription_products.tier`). Only `pro` matches by luck (substring of "Professional"). `basic` and `max` never match, so `user_plans` rows are never created for those subscribers at 4 call sites:
- `_handle_subscription_created` (`payments.py:1648`)
- `_handle_subscription_updated` (`payments.py:1784`)
- `upgrade_subscription` (`payments.py:2451`)
- `downgrade_subscription` (`payments.py:2717`)

Effect: `check_plan_entitlements()` falls back to `DEFAULT_*_LIMIT` trial-tier request/token limits for these subscribers, even though their dollar `subscription_allowance` is credited correctly (that part uses a separate, correct `eq("tier", tier)` lookup against `subscription_products`).

**Fix**:
- Migration: add a `tier TEXT` column to `plans` (nullable; values `basic`/`pro`/`max`/`NULL`).
- Backfill / repurpose existing rows (ordinal match, increasing limits):
  - `Starter` (id=3) → `tier='basic'`, `price_per_month` 20 → 35
  - `Professional` (id=4) → `tier='pro'`, `price_per_month` 50 → 120
  - `Business` (id=5) → `tier='max'`, `price_per_month` 100 → 350
  - `Free`, `Free Trial`, `Enterprise`, `Admin` unchanged (tier stays NULL; Enterprise left as an unused/future tier).
- Code: `get_plan_id_by_tier()` switches from `.ilike("name", f"%{tier}%")` to `.eq("tier", tier)`.
- No changes needed at the 4 call sites — they already call `get_plan_id_by_tier` correctly; only the underlying query was broken.

## 2. Top-up minimum: $5

`src/schemas/payments.py:176-183` (`CreateCheckoutSessionRequest.validate_amount`) currently enforces `if v < 50: raise ValueError("Amount must be at least $0.50 (50 cents)")`.

**Fix**: change to `if v < 500: raise ValueError("Amount must be at least $5.00 (500 cents)")`.

Frontend: `src/app/checkout/page.tsx:488` custom top-up amount input has `min={1}` — change to `min={5}` with matching client-side validation copy, so users aren't allowed to submit sub-$5 amounts that the backend will now reject.

## 3. First-topup $5 bonus

No such mechanism currently exists (only a referral-specific `has_made_first_purchase` check gated on being referred + $10 spend).

**Fix**: add a new, unconditional check in `_handle_checkout_completed` (`src/services/billing/payments.py`, near line 1018-1020), independent of the referral code path:

```
if not user.has_made_first_purchase and amount_dollars >= 5.0:
    add_credits_to_user(
        user_id, credits=5.0,
        transaction_type="first_topup_bonus",
        description="First top-up bonus",
    )
mark_first_purchase(user_id)  # called exactly once, regardless of bonus eligibility
```

`mark_first_purchase(user_id)` already exists (`src/services/referral.py:739`) and is idempotent; ensure it's called exactly once per checkout completion regardless of whether the bonus fired, and that the bonus check runs *before* the flag is flipped.

## 4. Referral bonus: remove referrer-side free grant

`apply_referral_bonus()` (`src/services/referral.py:367-466`) currently credits **both** the referee ($10, tied to their own real $10+ purchase) and the **referrer** ($10, for zero payment — a genuine free credit).

**Fix**: remove only the referrer-side `$10` grant (`REFERRAL_BONUS = 10.0` payout to the referrer). The referee's own purchase crediting is untouched — it is payment for their own purchase, not a free grant, and was never out of policy.

## 5. Dead trial code: delete

`start_trial_for_key()` (`src/db/trials.py`) grants `trial_days * 5` dollars via RPC `record_trial_grant`, but `TRIAL_DURATION_DAYS = 0` (`src/config/usage_limits.py:19`) and it has zero production callers (only `test_new_user_gets_3_day_trial` invokes it).

**Fix**: delete `start_trial_for_key()`, its RPC call/reference, and the now-pointless test.

## 6. Frontend: remove free-credit messaging

- `src/components/dialogs/trial-credits-notice.tsx` — actively markets "Welcome to Gatewayz! 🎉 You're starting with $X in free trial credits" and a stale "$3 or more" expiry threshold. Remove/disable this component (stop rendering it in whatever flow triggers it; delete if nothing else needs it).
- `src/components/sections/PricingSection.tsx:88` — hero tagline "Try now with free credits today." Remove/rewrite to not reference free credits.
- `src/app/checkout/page.tsx:488` — same fix as backend #2, `min={1}` → `min={5}`.

## Testing

- Unit tests for `get_plan_id_by_tier()` exact-match behavior post-migration.
- Unit/integration test: subscription created for `basic`/`max` tier now creates a `user_plans` row with correct limits.
- Unit test: `validate_amount` rejects < $5, accepts >= $5.
- Integration test: first top-up of $5+ grants exactly $5 bonus once; second top-up grants no bonus.
- Unit test: referral flow credits referee normally, grants referrer $0 (removed).
- Confirm `test_new_user_gets_3_day_trial` removed alongside `start_trial_for_key`.
- Manual: verify Stripe webhook flow locally (`stripe listen` already wired) end-to-end for a real checkout session with proper metadata (not just `stripe trigger`, which lacks metadata).
