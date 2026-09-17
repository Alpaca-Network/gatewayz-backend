-- Migration: UNIQUE (UPPER(code)) on public.coupons.
--
-- THE BUG
-- =======
-- `coupons.code` is UNIQUE, but case-SENSITIVELY. Every lookup in the system
-- matches on UPPER(code) -- idx_coupons_code_upper (20251009040000),
-- is_coupon_redeemable(), and redeem_coupon() (20260917010000). So 'WELCOME' and
-- 'welcome' are two rows that a user experiences as one coupon code.
--
-- This is not cosmetic, and it is not "one row shadows the other". It defeats
-- uq_coupon_user outright. Demonstrated on Postgres 16 against the real
-- functions:
--
--   rows: (id=1,'WELCOME',$50) and (id=2,'welcome',$5), both max_uses 5
--
--   Monday    redeem_coupon('welcome', user 1) -> coupon_id=1, granted $50
--             (an admin edits the coupon; a routine VACUUM FULL rewrites the heap)
--   Tuesday   redeem_coupon('welcome', user 1) -> coupon_id=2, granted $5
--
--   user 1 balance $55, two redemption rows, from ONE typed code.
--
-- uq_coupon_user is UNIQUE(coupon_id, user_id) and cannot see this: the two rows
-- differ by coupon_id, so the constraint is satisfied. The lookup has no ORDER BY,
-- so which row a code resolves to is unspecified -- stable in practice only
-- because idx_coupons_code_upper is used, and VACUUM FULL rewrites the heap in
-- index order and flips it. A latent bug whose fuse is routine maintenance.
--
-- HOW A COLLISION GETS CREATED
-- ============================
-- src/routes/admin_coupons.py::_assert_code_available matches with .ilike(), so a
-- single create of the twin is refused with 409 coupon_code_taken. But that check
-- and the INSERT are two statements with no lock between them, and the table's
-- UNIQUE(code) is case-sensitive, so two CONCURRENT creates of 'welcome' and
-- 'WELCOME' both pass the check and both insert. Same check-then-write shape as
-- the redemption race, one table over. This index is what closes it: the losing
-- INSERT now raises 23505, which the route maps back to the same 409.
--
-- normalize_code() upper-cases everything created through the admin API since
-- #2341, so the realistic sources of a collision are pre-#2341 rows and direct
-- database writes.
--
-- BEHAVIOUR ON A DIRTY TABLE
-- ==========================
-- Verified, so the next reader does not have to fear it: if a collision already
-- exists, CREATE UNIQUE INDEX fails with 23505 ("Key (upper(code::text))=(WELCOME)
-- is duplicated"), no index is created, and because `supabase db push` wraps the
-- push in a transaction the WHOLE migration aborts -- there is no partial state to
-- clean up. It fails at deploy time, loudly, and changes nothing.
--
-- Prod (ynleroehyrmaafkgjgmr) was checked read-only on 2026-09-16: one coupon row
-- ('GATEWAYZ'), zero UPPER(code) collisions, coupon_redemptions empty. This applies
-- cleanly today. The pre-flight below is for the window between writing this and
-- applying it, and for any environment that is not prod.
--
-- WHY THIS MIGRATION DOES NOT REPAIR A COLLISION ITSELF
-- ====================================================
-- Deliberate. Choosing which of two colliding coupons survives is a product
-- decision, not a mechanical one: both are money, either may already carry
-- redemptions, and nothing in the table records which spelling users were actually
-- given. A DELETE here would be guessing with somebody's money, and it would do it
-- silently, at deploy time, with no human in the loop.
--
-- *** If you are reading this because the migration failed and you are about to
-- *** add a DELETE or an UPDATE to "fix" it: don't. The pre-flight below tells you
-- *** which rows collide, what they are worth and which has been redeemed. Decide
-- *** with that in hand and repair the data by a separate, reviewed change.

-- ============================================================================
-- PRE-FLIGHT: fail with the rows, not just the key
-- ============================================================================
-- The raw 23505 from CREATE UNIQUE INDEX names the duplicated key and nothing
-- else. Whoever hits this at deploy time cannot act on that: they need to know
-- which ids collide, what each is worth, and which one has already paid out.
DO $$
DECLARE
    v_report TEXT;
    v_count  INTEGER;
BEGIN
    SELECT count(*), string_agg(detail, E'\n' ORDER BY detail)
    INTO v_count, v_report
    FROM (
        SELECT format(
                   '  UPPER(code)=%s -> %s',
                   UPPER(c.code),
                   string_agg(
                       format('[id=%s code=%L value_usd=%s times_used=%s is_active=%s redemptions=%s]',
                              c.id, c.code, c.value_usd, c.times_used, c.is_active,
                              (SELECT count(*) FROM public.coupon_redemptions r WHERE r.coupon_id = c.id)),
                       ', ' ORDER BY c.id
                   )
               ) AS detail
        FROM public.coupons c
        GROUP BY UPPER(c.code)
        HAVING count(*) > 1
    ) collisions;

    IF v_count > 0 THEN
        RAISE EXCEPTION
            E'Cannot add UNIQUE (UPPER(code)): % coupon code(s) already collide case-insensitively.\n%\n\n%',
            v_count,
            v_report,
            'Each colliding pair is money and may already have paid out. Decide which '
            'row survives as a reviewed data change, then re-run this migration. Do NOT '
            'add a DELETE to this file -- see the header for why.'
        USING ERRCODE = 'unique_violation';
    END IF;
END $$;

-- ============================================================================
-- THE INDEX
-- ============================================================================
-- Not CONCURRENTLY: repo convention, because `supabase db push` wraps the push in
-- a transaction and CREATE INDEX CONCURRENTLY cannot run inside one. The table
-- holds a single row in prod, so the brief SHARE lock is immaterial.
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupons_code_upper
    ON public.coupons (UPPER(code));

COMMENT ON INDEX public.uq_coupons_code_upper IS
'Coupon codes are unique case-insensitively. Every lookup in the system matches on
UPPER(code), so two rows differing only in case are one code to a user and two
coupon_ids to uq_coupon_user -- which lets one user redeem the same typed code
twice. This index makes that unrepresentable, and turns the check-then-insert race
in admin_coupons.create into a 23505 the route maps to 409 coupon_code_taken.';

-- ============================================================================
-- DETERMINISTIC LOOKUP IN redeem_coupon()
-- ============================================================================
-- Handled in 20260917010000 itself rather than by replacing the function here.
-- That migration has not been applied anywhere yet, so editing it in place keeps
-- one definition of redeem_coupon() in the tree. Re-declaring the function in this
-- file to change two lines would have meant carrying a second 300-line copy --
-- and the copy would have shipped without the rationale comments that make the
-- original reviewable.
--
-- The change there: the coupon lookup gains `ORDER BY id LIMIT 1`. With the index
-- above at most one row can match, so it is a no-op today. It exists so the
-- function is not INDEPENDENTLY fragile -- see the note at that line.

-- ============================================================================
-- DOWN MIGRATION (commented out - run manually to rollback)
-- ============================================================================
-- DROP INDEX IF EXISTS public.uq_coupons_code_upper;
