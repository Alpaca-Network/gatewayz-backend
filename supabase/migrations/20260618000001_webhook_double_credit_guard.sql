-- Database-level guard against double-crediting a Stripe payment.
--
-- The webhook credit handlers (_handle_checkout_completed / _handle_payment_succeeded)
-- are now idempotent in application code (they skip already-completed payments),
-- but a narrow race remains if two duplicate webhook deliveries are processed
-- concurrently. A partial UNIQUE index on the purchase grant closes that window
-- at the database level: a second "purchase" credit_transactions row for the same
-- payment_id will fail to insert.
--
-- SAFETY: a plain CREATE UNIQUE INDEX would fail the whole migration if the
-- pre-idempotency webhook already produced duplicate purchase rows in this
-- environment. To guarantee the deploy never breaks, we only create the index
-- when the data is already clean, and emit a WARNING (with the duplicate count)
-- otherwise so operators can dedupe and re-run. The index is created with
-- IF NOT EXISTS so re-running after cleanup is a no-op.

DO $$
DECLARE
    dup_count integer;
BEGIN
    SELECT count(*) INTO dup_count
    FROM (
        SELECT payment_id
        FROM public.credit_transactions
        WHERE transaction_type = 'purchase' AND payment_id IS NOT NULL
        GROUP BY payment_id
        HAVING count(*) > 1
    ) duplicates;

    IF dup_count > 0 THEN
        RAISE WARNING
            'Skipping unique index on credit_transactions(payment_id): % payment_id(s) '
            'already have duplicate purchase rows. Dedupe them, then re-run this migration '
            'to enable the double-credit guard.', dup_count;
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS "idx_credit_transactions_purchase_payment_uniq"
            ON public.credit_transactions ("payment_id")
            WHERE transaction_type = 'purchase' AND payment_id IS NOT NULL;
        RAISE NOTICE 'Created unique index idx_credit_transactions_purchase_payment_uniq';
    END IF;
END $$;
