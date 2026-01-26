# Performance Test Scripts

This directory contains automated performance testing scripts for the Gatewayz backend.

## Pricing Scheduler Performance Test

**Script**: `test_pricing_scheduler_performance.sh`

### Purpose

Automated testing of the pricing sync scheduler to verify:
- Sync duration and API response times
- Connection pool usage and leak detection
- Database query performance
- System stability under load
- Cache performance

### Prerequisites

1. Access to staging environment
2. Admin API key for staging
3. `jq` installed (`brew install jq` on macOS)
4. `bc` installed (usually pre-installed)
5. `curl` installed (usually pre-installed)

### Usage

```bash
# 1. Set your admin API key
export STAGING_ADMIN_KEY="gw_live_xxxxxxxxxxxxxx"

# 2. Run the test
./scripts/test_pricing_scheduler_performance.sh

# 3. View results
cat pricing_scheduler_performance_results_*.txt
```

### What It Tests

#### Test 1: Baseline Connection Pool Status
- Checks initial connection pool metrics
- Records baseline for leak detection

#### Test 2: Single Sync Duration Test
- Triggers one manual pricing sync
- Measures API response time and sync duration
- Validates against < 60s target

#### Test 3: Connection Pool Usage After Sync
- Checks for connection pool errors
- Validates active connections < 10

#### Test 4: Database Query Performance
- Measures average database query time
- Target: < 100ms average

#### Test 5: Cache Performance
- Checks cache hit/miss rates
- Identifies caching opportunities

#### Test 6: Load Test - Consecutive Syncs
- Runs 5 consecutive manual sync triggers
- Measures success rate and consistency
- Identifies server overload issues

#### Test 7: System Stability Check
- Verifies health endpoint after load test
- Ensures system recovered properly

#### Test 8: Post-Test Connection Pool Status
- Final connection pool check
- Detects memory/connection leaks

### Output

The script generates two types of output:

1. **Console Output**: Real-time colored output showing test progress
2. **Results File**: `pricing_scheduler_performance_results_YYYYMMDD_HHMMSS.txt`

Results file includes:
- All test results
- Performance benchmarks table
- Pass/fail status for each metric
- Guidance for manual checks (memory, CPU via Railway dashboard)

### Performance Targets

| Metric | Target |
|--------|--------|
| Sync Duration (avg) | < 30s |
| Sync Duration (p95) | < 60s |
| API Response Time | < 5s |
| Memory Increase | < 100MB |
| CPU Usage (peak) | < 80% |
| DB Query Time (avg) | < 100ms |
| Connection Pool Usage | < 50% |
| Success Rate | 100% |

### Known Issues

As of January 26, 2026:
- ⚠️ API response time exceeds target (37-108s vs 5s)
- ⚠️ Load test success rate: 30% (7/10 fail with 502/504)
- ⚠️ Inconsistent performance under load (3x variance)

See `pricing_scheduler_performance_findings.md` for full analysis.

### Customization

You can modify the script to:
- Change number of load test syncs (default: 5)
- Adjust timeout values (default: 180s for sync, 30s for metrics)
- Change target environment (edit `STAGING_URL`)
- Add additional metrics to track

### Troubleshooting

**Error: "STAGING_ADMIN_KEY environment variable not set"**
- Solution: `export STAGING_ADMIN_KEY="your-key"`

**Error: "jq: command not found"**
- Solution: `brew install jq` (macOS) or `apt-get install jq` (Linux)

**Timeout errors (exit code 28)**
- Indicates sync taking > 180 seconds
- May need to increase `--max-time` value in script
- Could indicate performance issue

**502/504 errors during load test**
- Expected based on current issues (see findings report)
- Indicates server cannot handle concurrent syncs
- Will be fixed with sync locking mechanism

### Related Documentation

- Manual Testing Guide: `docs/MANUAL_TESTING_GUIDE.md` (Part 9)
- Performance Findings: `pricing_scheduler_performance_findings.md`
- Runbook: `docs/runbooks/pricing_sync_slow_performance.md`
- Issue: #959

### Contributing

When adding new performance tests:
1. Follow the existing structure (sections with log helpers)
2. Include clear pass/fail criteria
3. Add results to summary table
4. Update this README
