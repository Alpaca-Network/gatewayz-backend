-- Drop the last four SQL functions of the retired delegation programme.
--
-- delegation_account_spent, delegation_reserve_accrual and
-- delegation_release_claim were created in 20261008230000_delegated_staking.sql;
-- delegation_claim_accrual was created there and replaced (same signature) in
-- 20261009010000_delegation_claim_inactive_user.sql. Nothing in the
-- application, the other SQL functions or the tests calls them. The
-- programme's other functions were dropped in
-- 20261126120000_discount_credits_hardening.sql.
--
-- No table is dropped: delegation_eligibility backs the wallet-link check, and
-- the other delegation_* tables are kept for the record.

drop function if exists public.delegation_account_spent(bigint, date, text[], bigint);
drop function if exists public.delegation_reserve_accrual(
  text, text, date, numeric, numeric, numeric, bigint, numeric, numeric);
drop function if exists public.delegation_claim_accrual(bigint, bigint, numeric);
drop function if exists public.delegation_release_claim(bigint, bigint);
