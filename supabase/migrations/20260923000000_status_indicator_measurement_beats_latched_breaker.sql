-- A latched circuit breaker must not outrank the measurement beside it.
--
-- #2366. `model_status_current.status_indicator` tested the breaker FIRST and
-- unconditionally:
--
--     WHEN mht.circuit_breaker_state = 'open' THEN 'offline'      -- short-circuits
--     WHEN mht.uptime_percentage_24h >= 99.9 THEN 'operational'   -- never reached
--
-- so one trip published `offline` forever, whatever the uptime column said.
-- Measured in production on 2026-09-23:
--
--     openai/gpt-4o   status_indicator = offline   circuit_breaker_state = open
--                     uptime_24h = 100.0           uptime_7d = 100.0
--                     last_checked = today         last_failure = 7 days ago
--
-- Offline, beside a hundred percent uptime, in one row. Twenty-five advertised
-- models were in that state: published as down to anyone reading the status
-- page while serving every check for a week. That is the same cost as the
-- incident where nine live flagship models were hidden, pointed the other way.
--
-- The fix is NOT to stop trusting the breaker -- it is a real routing state and
-- it stays published verbatim in `circuit_breaker_state`, so nothing is lost.
-- The fix is that it may only decide the HEALTH VERDICT when the measurement
-- agrees with it. Breaker open and uptime poor -> offline, as before. Breaker
-- open and uptime healthy -> the row stops contradicting itself and falls
-- through to the ladder underneath.
--
-- COALESCE to 0 is deliberate: no measurement at all plus an open breaker is
-- offline. Absent evidence must not read as healthy evidence.
--
-- Threshold is 99.0 rather than the ladder's 99.9 so the override releases only
-- on a clearly healthy window, not a marginal one.
--
-- Column list, order and types are reproduced exactly: CREATE OR REPLACE VIEW
-- requires it, and src/routes/status_page.py asserts the view's columns and
-- turns a missing one into a 500.

CREATE OR REPLACE VIEW model_status_current AS
SELECT
    mht.provider,
    mht.model,
    mht.gateway,
    mht.monitoring_tier,
    mht.last_status,
    mht.uptime_percentage_24h,
    mht.uptime_percentage_7d,
    mht.uptime_percentage_30d,
    mht.average_response_time_ms,
    mht.last_called_at,
    mht.last_success_at,
    mht.last_failure_at,
    mht.circuit_breaker_state,
    mht.consecutive_failures,
    mht.usage_count_24h,
    mht.is_enabled,
    CASE
        -- The breaker decides only when the measurement does not contradict it.
        WHEN mht.circuit_breaker_state = 'open'
             AND COALESCE(mht.uptime_percentage_24h, 0) < 99.0 THEN 'offline'
        WHEN mht.uptime_percentage_24h >= 99.9 THEN 'operational'
        WHEN mht.uptime_percentage_24h >= 95.0 THEN 'degraded'
        WHEN mht.uptime_percentage_24h >= 50.0 THEN 'partial_outage'
        ELSE 'major_outage'
    END as status_indicator,
    (SELECT COUNT(*) FROM model_health_incidents mhi
     WHERE mhi.provider = mht.provider
     AND mhi.model = mht.model
     AND mhi.status = 'active') as active_incidents_count
FROM model_health_tracking mht
WHERE mht.is_enabled = TRUE;

COMMENT ON VIEW model_status_current IS
    'Current status view for all monitored models (for status page). '
    'status_indicator: an open circuit breaker yields ''offline'' only when the '
    'measured 24h uptime agrees (<99%); a healthy measurement beside an open '
    'breaker falls through to the uptime ladder (#2366). The breaker state '
    'itself is published verbatim in circuit_breaker_state.';
