-- Enable the double-credit guard now that historical duplicate purchase rows
-- have been cleaned up.
--
-- Migration 20260618000001 added this index defensively but skipped it on any
-- environment that still had duplicate "purchase" rows for the same payment_id
-- (prod had 2, caused by a single checkout firing both checkout.session.completed
-- and payment_intent.succeeded before the webhook handlers were made idempotent).
-- Those duplicates have been removed, so a plain creation is now safe and makes
-- the guard reproducible across environments.
--
-- A second "purchase" credit_transactions row for the same payment_id will now
-- fail to insert, backstopping the application-layer idempotency check.

CREATE UNIQUE INDEX IF NOT EXISTS "idx_credit_transactions_purchase_payment_uniq"
    ON public.credit_transactions ("payment_id")
    WHERE transaction_type = 'purchase' AND payment_id IS NOT NULL;
