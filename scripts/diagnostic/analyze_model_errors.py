#!/usr/bin/env python3
"""
Script to analyze model accessibility errors from the codebase.

This script examines the codebase patterns to identify common model accessibility
error scenarios captured in Sentry.
"""

import re
from pathlib import Path
from collections import defaultdict

# Common model error patterns to scan for
ERROR_PATTERNS = {
    "provider_unavailable": r"Provider.*unavailable|status_code=503",
    "model_not_found": r"model.*not.*found|status_code=404",
    "authentication_error": r"auth.*error|status_code=401",
    "permission_denied": r"permission.*denied|status_code=403",
    "bad_gateway": r"bad.*gateway|status_code=502",
    "gateway_timeout": r"gateway.*timeout|status_code=504",
    "rate_limit": r"rate.*limit|status_code=429",
    "timeout": r"TimeoutException|asyncio\.TimeoutError|timeout",
    "network_error": r"RequestError|network.*error|connection.*error",
}

# Provider error handling patterns
PROVIDER_ERROR_HANDLERS = [
    "capture_provider_error",
    "capture_model_health_error",
    "map_provider_error",
    "should_failover",
]

def analyze_file(file_path: Path) -> dict:
    """Analyze a single file for error patterns."""
    results = defaultdict(list)

    try:
        content = file_path.read_text()

        # Find error patterns
        for error_type, pattern in ERROR_PATTERNS.items():
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                line_num = content[:match.start()].count('\n') + 1
                context = content[max(0, match.start() - 100):match.end() + 100]
                results[error_type].append({
                    'line': line_num,
                    'file': str(file_path),
                    'context': context.replace('\n', ' ')[:200]
                })

        # Find error handler usage
        for handler in PROVIDER_ERROR_HANDLERS:
            if handler in content:
                matches = re.finditer(rf'\b{handler}\b', content)
                for match in matches:
                    line_num = content[:match.start()].count('\n') + 1
                    results['error_handlers'].append({
                        'handler': handler,
                        'line': line_num,
                        'file': str(file_path)
                    })

    except Exception as e:
        print(f"Error analyzing {file_path}: {e}")

    return results

def main():
    """Main analysis function."""
    repo_root = Path(__file__).parents[2]

    # Key files to analyze
    key_files = [
        repo_root / "src" / "routes" / "chat.py",
        repo_root / "src" / "services" / "provider_failover.py",
        repo_root / "src" / "services" / "openrouter_client.py",
        repo_root / "src" / "services" / "model_availability.py",
        repo_root / "src" / "services" / "model_health_monitor.py",
        repo_root / "src" / "utils" / "sentry_context.py",
    ]

    all_results = defaultdict(list)

    print("=" * 80)
    print("Model Accessibility Error Analysis")
    print("=" * 80)
    print()

    for file_path in key_files:
        if not file_path.exists():
            print(f"⚠️  File not found: {file_path}")
            continue

        print(f"Analyzing: {file_path.relative_to(repo_root)}")
        results = analyze_file(file_path)

        # Merge results
        for error_type, errors in results.items():
            all_results[error_type].extend(errors)

    print()
    print("=" * 80)
    print("Summary of Model Error Patterns Found in Codebase")
    print("=" * 80)
    print()

    # Print summary
    for error_type in ERROR_PATTERNS.keys():
        count = len(all_results.get(error_type, []))
        if count > 0:
            print(f"• {error_type.replace('_', ' ').title()}: {count} occurrences")

    print()
    print("=" * 80)
    print("Error Handler Coverage")
    print("=" * 80)
    print()

    error_handlers = all_results.get('error_handlers', [])
    handler_count = defaultdict(int)
    for handler_info in error_handlers:
        handler_count[handler_info['handler']] += 1

    for handler, count in sorted(handler_count.items(), key=lambda x: x[1], reverse=True):
        print(f"• {handler}: {count} uses")

    print()
    print("=" * 80)
    print("Common Model Accessibility Error Scenarios")
    print("=" * 80)
    print()

    scenarios = [
        {
            "name": "Provider Unavailable (503)",
            "description": "Provider service is down or unreachable",
            "handled": "Yes - failover to alternative providers",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Model Not Found (404)",
            "description": "Requested model doesn't exist on provider",
            "handled": "Yes - failover enabled for eligible providers",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Authentication Error (401)",
            "description": "Invalid or expired API key for provider",
            "handled": "Partial - no failover (auth issue)",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Permission Denied (403)",
            "description": "API key lacks permission for model/operation",
            "handled": "Yes - failover enabled",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Bad Gateway (502)",
            "description": "Upstream provider gateway error",
            "handled": "Yes - failover enabled",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Gateway Timeout (504)",
            "description": "Provider request exceeded timeout",
            "handled": "Yes - failover enabled",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Rate Limit (429)",
            "description": "Provider rate limit exceeded",
            "handled": "No - returns error to client",
            "captured": "Conditional - only for 5xx errors"
        },
        {
            "name": "Request Timeout",
            "description": "Request took too long to complete",
            "handled": "Yes - logged and may trigger failover",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Network Error",
            "description": "Connection failed or network unreachable",
            "handled": "Yes - logged as upstream network error",
            "captured": "Yes - via capture_provider_error()"
        },
        {
            "name": "Model Health Check Failure",
            "description": "Periodic health check detected model unavailability",
            "handled": "Yes - circuit breaker may open",
            "captured": "Yes - via capture_model_health_error()"
        },
    ]

    for scenario in scenarios:
        print(f"▪ {scenario['name']}")
        print(f"  Description: {scenario['description']}")
        print(f"  Handled: {scenario['handled']}")
        print(f"  Sentry Capture: {scenario['captured']}")
        print()

    print("=" * 80)
    print("Recommendations for Sentry Investigation")
    print("=" * 80)
    print()
    print("To investigate model accessibility errors in Sentry, look for:")
    print()
    print("1. Filter by tags:")
    print("   - provider: openrouter, portkey, featherless, etc.")
    print("   - model_id: specific model identifiers")
    print("   - operation: health_check, chat_completion, etc.")
    print()
    print("2. Search for error types:")
    print("   - 'Provider unavailable' or 'status_code=503'")
    print("   - 'Model not found' or 'status_code=404'")
    print("   - 'TimeoutException' or timeout-related errors")
    print("   - 'RequestError' for network issues")
    print()
    print("3. Check context data:")
    print("   - provider: which provider failed")
    print("   - model: which model was requested")
    print("   - endpoint: /v1/chat/completions, etc.")
    print("   - request_id: for distributed tracing")
    print()
    print("4. Review model_health context:")
    print("   - status: unhealthy, degraded, unavailable")
    print("   - error_count: number of consecutive failures")
    print("   - circuit_breaker_state: open, closed, half_open")
    print()
    print("=" * 80)

if __name__ == "__main__":
    main()
