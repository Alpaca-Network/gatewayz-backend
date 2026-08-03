-- MVP refactor Task 8: drop notifications + vanity-ping subsystem tables.
-- STAGING ONLY. Do NOT apply to production. See docs/NORTH_STAR.md §5 Non-Goals.

DROP TABLE IF EXISTS ping_stats CASCADE;
DROP TABLE IF EXISTS notifications CASCADE;
DROP TABLE IF EXISTS notification_preferences CASCADE;
DROP TABLE IF EXISTS admin_notifications CASCADE;
