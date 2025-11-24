# Sentry Release Tracking Setup

This document explains how to configure and use Sentry release tracking in the Gatewayz API Gateway.

## Overview

Sentry release tracking allows you to:
- **Monitor release health**: Track error rates, performance, and stability per release
- **Associate errors with deployments**: Understand which version introduced bugs
- **Track version adoption**: See which environments are running which versions
- **Correlate commits**: Link errors to specific commits and PRs
- **View release timelines**: Understand deployment history and regression patterns

## Configuration

### Environment Variables

Add the following to your `.env` file:

```bash
# Sentry DSN (from your Sentry project settings)
SENTRY_DSN=https://your-sentry-dsn@sentry.io/your-project-id

# Enable/disable Sentry
SENTRY_ENABLED=true

# Environment name (development, staging, production)
SENTRY_ENVIRONMENT=production

# Release version (should match your app version)
SENTRY_RELEASE=2.0.3

# Transaction sampling (0.0 to 1.0)
# Lower values reduce overhead but sample fewer transactions
SENTRY_TRACES_SAMPLE_RATE=0.1

# Profiling sampling (0.0 to 1.0)
SENTRY_PROFILES_SAMPLE_RATE=0.1
```

### Required Configuration

The following are **essential** for release tracking:

1. **SENTRY_DSN**: Your Sentry project's DSN (Data Source Name)
   - Found in Sentry: Settings → Projects → [Your Project] → Client Keys (DSN)

2. **SENTRY_RELEASE**: Should match your application version
   - Recommended: Use semantic versioning (e.g., `2.0.3`, `v2.0.3`)
   - The SDK automatically includes this in all error events

3. **SENTRY_ENVIRONMENT**: One of:
   - `production` - Production deployment
   - `staging` - Staging/pre-production
   - `development` - Local development

## Usage

### Automatic Release Tracking

Once configured, all errors are automatically associated with the release:

```python
# Error details in Sentry will include:
# - Release: 2.0.3
# - Environment: production
# - Timestamp and other context
```

### Manual Release Events

Use the release tracking utilities to capture deployment and health events:

```python
from src.utils.release_tracking import (
    capture_deployment_event,
    capture_release_event,
    capture_release_health,
    set_release_context,
)

# Track a deployment
capture_deployment_event(
    version="2.0.3",
    environment="production",
    status="succeeded",
    details={
        "duration_seconds": 45,
        "deployed_by": "ci-system",
        "services": ["api", "worker"]
    }
)

# Capture a release event (e.g., feature announcement)
capture_release_event(
    "Deployed release 2.0.3 with new rate limiting",
    level="info",
    release_metadata={"features": ["rate_limiting", "caching"]}
)

# Track release health metrics
capture_release_health(
    version="2.0.3",
    metric="error_rate",
    value=0.5,
    unit="percentage"
)

# Set release context for subsequent errors
set_release_context(
    version="2.0.3",
    commit="abc123def456",
    environment="production"
)
```

### API Reference

#### `capture_deployment_event(version, environment, status, details)`

Capture a deployment event for release tracking.

**Parameters:**
- `version` (str): Release version (e.g., "2.0.3")
- `environment` (str): Target environment (production, staging, development)
- `status` (str): Deployment status (succeeded, failed, in_progress)
- `details` (dict, optional): Additional metadata

**Returns:** Event ID or None if Sentry disabled

**Example:**
```python
capture_deployment_event(
    version="2.0.3",
    environment="production",
    status="succeeded",
    details={"duration_seconds": 45}
)
```

#### `capture_release_event(message, level, release_metadata)`

Capture a release-related event (announcements, milestones, etc.).

**Parameters:**
- `message` (str): Event message
- `level` (str): Log level (info, warning, error)
- `release_metadata` (dict, optional): Release details

**Returns:** Event ID or None

**Example:**
```python
capture_release_event(
    "Release 2.0.3 deployed",
    level="info",
    release_metadata={"new_features": 5}
)
```

#### `capture_release_health(version, metric, value, unit)`

Capture release health metrics.

**Parameters:**
- `version` (str): Release version
- `metric` (str): Metric name (error_rate, response_time, etc.)
- `value` (float|int): Metric value
- `unit` (str, optional): Unit of measurement

**Example:**
```python
capture_release_health(
    version="2.0.3",
    metric="error_rate",
    value=0.5,
    unit="percentage"
)
```

#### `set_release_context(version, commit, environment)`

Set release context for all subsequent errors.

**Parameters:**
- `version` (str): Release version
- `commit` (str, optional): Git commit hash
- `environment` (str, optional): Environment name

**Example:**
```python
set_release_context(
    version="2.0.3",
    commit="abc123def456",
    environment="production"
)
```

#### `get_current_release()`

Get the currently active release version.

**Returns:** Release version string or None

**Example:**
```python
current = get_current_release()
# Returns: "2.0.3"
```

#### `get_release_info()`

Get current release information from Sentry.

**Returns:** Dictionary with release and environment info

**Example:**
```python
info = get_release_info()
# Returns: {"release": "2.0.3", "environment": "production"}
```

## Integration with CI/CD

### Deployment Notifications

Capture deployment events in your CI/CD pipeline:

```yaml
# GitHub Actions Example
- name: Notify Sentry of Deployment
  run: |
    python -c "
    from src.utils.release_tracking import capture_deployment_event
    capture_deployment_event(
        version='${{ github.ref }}',
        environment='production',
        status='succeeded',
        details={'github_run': '${{ github.run_id }}'}
    )
    "
```

### Version Management

For automatic version detection from git:

```python
# In your deployment script
import subprocess

def get_version_from_git():
    try:
        # Get latest tag
        version = subprocess.check_output(
            ['git', 'describe', '--tags', '--abbrev=0'],
            text=True
        ).strip()
        return version
    except:
        return "unknown"

# Use in environment
os.environ['SENTRY_RELEASE'] = get_version_from_git()
```

## Sentry CLI Integration (Optional)

For advanced release management, use the Sentry CLI:

```bash
# Install Sentry CLI
pip install sentry-cli
# or
npm install -g @sentry/cli

# Create a new release
sentry-cli releases --org your-org new --project your-project 2.0.3

# Associate commits with release
sentry-cli releases --org your-org --project your-project \
    set-commits 2.0.3 \
    --auto

# Deploy release to environment
sentry-cli releases --org your-org --project your-project \
    deploys 2.0.3 new \
    -e production

# Finalize release
sentry-cli releases --org your-org --project your-project \
    finalize 2.0.3
```

## Monitoring in Sentry

### Release Dashboard

1. Go to Sentry: **Releases** → [Your Release]
2. View:
   - **Release Overview**: Key metrics and status
   - **Health**: Error rates, session data, transaction performance
   - **Commits**: Associated commits and PR links
   - **Deploys**: Deployment history and timing

### Health Metrics

Track for each release:
- **Crash Free Sessions**: Percentage of error-free user sessions
- **Adoption**: How many users are on each version
- **Error Rate**: New errors introduced in this release
- **Performance**: Response times and transaction performance

### Regression Detection

Sentry automatically detects:
- New errors in a release
- Performance regressions
- Increased error rates compared to previous version
- Session crashes

## Best Practices

1. **Semantic Versioning**: Use `major.minor.patch` format (e.g., `2.0.3`)

2. **Environment Separation**: Always set `SENTRY_ENVIRONMENT` correctly
   - Helps isolate issues by environment
   - Prevents mixing staging and production data

3. **Commit Association**: Link commits to releases
   - Enables "suspected commit" identification
   - Helps with regression attribution

4. **Sampling Rates**: Adjust for your traffic
   - **Low traffic** (< 1M requests/month): `traces_sample_rate=1.0`
   - **Medium traffic** (1M-10M): `traces_sample_rate=0.1`
   - **High traffic** (> 10M): `traces_sample_rate=0.01`

5. **PII Protection**: Review `send_default_pii` setting
   - Enabled: Sends request headers, IP addresses, etc.
   - Disabled: More privacy, less debugging info

6. **Release Finalization**: After deployment, finalize releases
   - Prevents new issues from being retroactively assigned
   - Marks release as "complete"

## Troubleshooting

### Releases Not Appearing

1. Check `SENTRY_DSN` is valid
2. Verify `SENTRY_ENABLED=true`
3. Confirm `SENTRY_RELEASE` format (should be a string, not null)
4. Check Sentry project settings → Client Keys

### Release Health Not Showing

1. Events must be sent within 30 days of release creation
2. Check sample rates aren't filtering events
3. Verify errors are actually occurring (check error counts)

### Commit Association Missing

1. Install Sentry CLI: `npm install -g @sentry/cli`
2. Authenticate: `sentry-cli login`
3. Run: `sentry-cli releases set-commits [version] --auto`
4. Requires GitHub/GitLab integration in Sentry

## Example: Complete Deployment Flow

```python
from src.utils.release_tracking import (
    capture_deployment_event,
    capture_release_health,
)
import os

def deploy_release(version: str):
    """Handle deployment with Sentry integration"""

    # 1. Start deployment
    capture_deployment_event(
        version=version,
        environment=os.getenv("APP_ENV", "production"),
        status="in_progress"
    )

    # 2. Do deployment work...
    try:
        # Deploy containers, run migrations, etc.
        deployment_success = True
    except Exception as e:
        deployment_success = False

    # 3. Report deployment outcome
    status = "succeeded" if deployment_success else "failed"
    capture_deployment_event(
        version=version,
        environment=os.getenv("APP_ENV"),
        status=status,
        details={"success": deployment_success}
    )

    # 4. After stabilization period, report health
    if deployment_success:
        # Check error rate after 5 minutes
        import time
        time.sleep(300)

        error_rate = check_error_rate()
        capture_release_health(
            version=version,
            metric="error_rate",
            value=error_rate,
            unit="percentage"
        )
```

## References

- [Sentry Release Tracking Docs](https://docs.sentry.io/product/releases/setup/)
- [Sentry Python SDK](https://docs.sentry.io/platforms/python/)
- [Sentry Release Health](https://docs.sentry.io/product/releases/health/)
- [Sentry CLI](https://docs.sentry.io/cli/)

## Questions or Issues?

If you encounter issues with release tracking:

1. Check Sentry project settings for DSN and project ID
2. Review logs: `grep "Sentry" logs/`
3. Verify environment variables in deployment
4. Contact DevOps or check Sentry documentation
