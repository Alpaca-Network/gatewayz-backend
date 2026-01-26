#!/usr/bin/env python3
"""
Diagnostic script to check why OpenTelemetry is not initializing.
Run this in your Railway backend environment.
"""

import os
import sys

print("=" * 80)
print("OpenTelemetry Diagnostic Script")
print("=" * 80)

# Check 1: Environment Variables
print("\n1. CHECKING ENVIRONMENT VARIABLES:")
print("-" * 80)

tempo_enabled = os.environ.get("TEMPO_ENABLED", "true")
print(f"   TEMPO_ENABLED (raw): '{tempo_enabled}'")
print(f"   TEMPO_ENABLED (normalized): '{tempo_enabled.lower()}'")
is_enabled = tempo_enabled.lower() in {"1", "true", "yes"}
print(f"   ✓ Will be treated as: {is_enabled}")

tempo_endpoint = os.environ.get("TEMPO_OTLP_HTTP_ENDPOINT", "http://tempo:4318")
print(f"\n   TEMPO_OTLP_HTTP_ENDPOINT: '{tempo_endpoint}'")

skip_check = os.environ.get("TEMPO_SKIP_REACHABILITY_CHECK", "true")
print(f"   TEMPO_SKIP_REACHABILITY_CHECK: '{skip_check}'")

# Check 2: Package Availability
print("\n2. CHECKING OPENTELEMETRY PACKAGES:")
print("-" * 80)

packages = [
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.resources",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.instrumentation.fastapi",
    "opentelemetry.semconv.resource",
]

all_available = True
for package in packages:
    try:
        __import__(package)
        print(f"   ✓ {package}")
    except ImportError as e:
        print(f"   ✗ {package} - MISSING: {e}")
        all_available = False

if all_available:
    print("\n   ✓ All OpenTelemetry packages are installed")
else:
    print("\n   ✗ Some OpenTelemetry packages are MISSING")
    print(
        "   Run: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-fastapi"
    )

# Check 3: Config Loading
print("\n3. CHECKING CONFIG MODULE:")
print("-" * 80)

try:
    from src.config.config import Config

    print(f"   ✓ Config module loaded")
    print(f"   Config.TEMPO_ENABLED: {Config.TEMPO_ENABLED}")
    print(f"   Config.TEMPO_OTLP_HTTP_ENDPOINT: {Config.TEMPO_OTLP_HTTP_ENDPOINT}")
    print(f"   Config.TEMPO_SKIP_REACHABILITY_CHECK: {Config.TEMPO_SKIP_REACHABILITY_CHECK}")
    print(f"   Config.OTEL_SERVICE_NAME: {Config.OTEL_SERVICE_NAME}")
    print(f"   Config.APP_ENV: {Config.APP_ENV}")
except Exception as e:
    print(f"   ✗ Failed to load Config: {e}")
    sys.exit(1)

# Check 4: OpenTelemetry Config Module
print("\n4. CHECKING OPENTELEMETRY CONFIG:")
print("-" * 80)

try:
    from src.config.opentelemetry_config import OpenTelemetryConfig, OPENTELEMETRY_AVAILABLE

    print(f"   ✓ OpenTelemetryConfig module loaded")
    print(f"   OPENTELEMETRY_AVAILABLE: {OPENTELEMETRY_AVAILABLE}")
    print(f"   _initialized: {OpenTelemetryConfig._initialized}")
except Exception as e:
    print(f"   ✗ Failed to load OpenTelemetryConfig: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

# Check 5: Network Connectivity
print("\n5. CHECKING NETWORK CONNECTIVITY:")
print("-" * 80)

if ".railway.internal" in tempo_endpoint:
    # Extract host and port
    import re

    match = re.search(r"https?://([^:/]+)(?::(\d+))?", tempo_endpoint)
    if match:
        host = match.group(1)
        port = match.group(2) or "4318"
        print(f"   Checking internal DNS: {host}:{port}")

        # Try DNS resolution
        try:
            import socket

            ip = socket.gethostbyname(host)
            print(f"   ✓ DNS resolution: {host} -> {ip}")
        except Exception as e:
            print(f"   ✗ DNS resolution failed: {e}")

        # Try TCP connection
        try:
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, int(port)))
            sock.close()
            if result == 0:
                print(f"   ✓ TCP connection successful to {host}:{port}")
            else:
                print(f"   ✗ TCP connection failed to {host}:{port} (error code: {result})")
        except Exception as e:
            print(f"   ✗ TCP connection test failed: {e}")

        # Try HTTP request
        try:
            import urllib.request
            import urllib.error

            full_url = f"http://{host}:{port}/v1/traces"
            print(f"   Testing HTTP POST to: {full_url}")
            req = urllib.request.Request(full_url, method="POST", data=b"{}")
            req.add_header("Content-Type", "application/json")
            try:
                response = urllib.request.urlopen(req, timeout=5)
                print(f"   ✓ HTTP POST successful (status: {response.status})")
            except urllib.error.HTTPError as he:
                # 400/404/405 are expected for invalid data, but means service is up
                if he.code in [400, 404, 405]:
                    print(f"   ✓ HTTP service is running (got {he.code} - expected for test data)")
                else:
                    print(f"   ⚠ HTTP error: {he.code} {he.reason}")
            except urllib.error.URLError as ue:
                print(f"   ✗ HTTP connection failed: {ue.reason}")
        except Exception as e:
            print(f"   ⚠ HTTP test failed: {e}")
else:
    print(f"   Skipping internal DNS check (not using .railway.internal)")
    print(f"   Endpoint: {tempo_endpoint}")

# Summary
print("\n" + "=" * 80)
print("DIAGNOSIS SUMMARY:")
print("=" * 80)

if not is_enabled:
    print("❌ TEMPO_ENABLED is FALSE - tracing is disabled")
    print("   Fix: Set TEMPO_ENABLED=true in Railway environment variables")
elif not all_available:
    print("❌ OpenTelemetry packages are MISSING")
    print("   Fix: Install packages in requirements.txt and redeploy")
elif not Config.TEMPO_ENABLED:
    print("❌ Config.TEMPO_ENABLED is FALSE (environment variable parsing issue)")
    print(f"   Raw value: '{tempo_enabled}'")
    print("   Fix: Check for whitespace or invalid characters in Railway variable")
else:
    print("✅ Configuration looks good - tracing should initialize")
    print("\nNext steps:")
    print("1. Check backend startup logs for '🔭 Initializing OpenTelemetry tracing...'")
    print("2. If still not appearing, check main.py line 607 is executing")
    print("3. Check for exceptions in try/except block (main.py:604-610)")

print("\nTo test initialization manually, run:")
print(
    "   python -c 'from src.config.opentelemetry_config import OpenTelemetryConfig; OpenTelemetryConfig.initialize()'"
)
print("=" * 80)
