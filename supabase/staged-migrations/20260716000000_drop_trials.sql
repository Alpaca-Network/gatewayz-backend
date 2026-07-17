-- MVP refactor Task 4: drop trials subsystem tables (incl. partner trials).
-- STAGING ONLY. Do NOT apply to production. See docs/NORTH_STAR.md §5 Non-Goals.

DROP TABLE IF EXISTS trial_config CASCADE;
DROP TABLE IF EXISTS trial_grants CASCADE;
DROP TABLE IF EXISTS trial_conversion_metrics CASCADE;
DROP TABLE IF EXISTS partner_trials CASCADE;
DROP TABLE IF EXISTS partner_trial_analytics CASCADE;
