#!/usr/bin/env python3
"""
Tempo Connection Diagnostic Script

This script helps diagnose OpenTelemetry/Tempo connection issues by:
1. Checking environment variables
2. Testing DNS resolution
3. Testing TCP connectivity
4. Verifying endpoint URL format
5. Testing OTLP exporter configuration

Usage:
    python scripts/diagnose_tempo_connection.py
"""

import os
import socket
import sys
from urllib.parse import urlparse

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print(f"{'=' * 80}\n")


def check_environment_variables():
    """Check OpenTelemetry environment variables."""
    print_section("1. Environment Variables Check")

    env_vars = {
        "TEMPO_ENABLED": os.getenv("TEMPO_ENABLED", "NOT SET"),
        "TEMPO_OTLP_HTTP_ENDPOINT": os.getenv("TEMPO_OTLP_HTTP_ENDPOINT", "NOT SET"),
        "TEMPO_OTLP_GRPC_ENDPOINT": os.getenv("TEMPO_OTLP_GRPC_ENDPOINT", "NOT SET"),
        "TEMPO_SKIP_REACHABILITY_CHECK": os.getenv("TEMPO_SKIP_REACHABILITY_CHECK", "NOT SET"),
        "OTEL_SERVICE_NAME": os.getenv("OTEL_SERVICE_NAME", "NOT SET"),
    }

    for key, value in env_vars.items():
        status = "✅" if value != "NOT SET" else "❌"
        print(f"{status} {key}: {value}")

    # Check if TEMPO is enabled
    tempo_enabled = os.getenv("TEMPO_ENABLED", "true").lower() in {"1", "true", "yes"}
    if not tempo_enabled:
        print("\n⚠️  TEMPO_ENABLED is set to false - tracing is disabled")
        return False

    return True


def parse_endpoint_url(endpoint: str):
    """Parse and validate endpoint URL."""
    print_section("2. Endpoint URL Parsing")

    if not endpoint or endpoint == "NOT SET":
        print("❌ TEMPO_OTLP_HTTP_ENDPOINT is not set!")
        print("\n💡 Set this environment variable:")
        print("   export TEMPO_OTLP_HTTP_ENDPOINT=http://tempo.railway.internal:4318")
        return None

    print(f"Raw endpoint: {endpoint}")

    parsed = urlparse(endpoint)
    print(f"\n📋 Parsed URL components:")
    print(f"   - Scheme: {parsed.scheme}")
    print(f"   - Hostname: {parsed.hostname}")
    print(f"   - Port: {parsed.port}")
    print(f"   - Path: {parsed.path}")

    # Validate components
    issues = []

    if not parsed.scheme:
        issues.append("Missing scheme (http:// or https://)")
    elif parsed.scheme not in {"http", "https"}:
        issues.append(f"Invalid scheme: {parsed.scheme}")

    if not parsed.hostname:
        issues.append("Missing hostname")

    if not parsed.port:
        issues.append("❌ CRITICAL: Missing port number!")
        print("\n🔴 PORT MISSING - This causes 'Connection refused to port 80' errors!")
        print("   The OTLPSpanExporter will default to port 80 instead of 4318")
        print("\n💡 Fix: Add :4318 to your endpoint:")
        print(f"   CORRECT: http://{parsed.hostname}:4318")
        print(f"   WRONG:   {endpoint}")

    # Check for duplicate /v1/traces path
    if "/v1/traces" in parsed.path:
        issues.append("⚠️  WARNING: Path contains /v1/traces")
        print("\n🟡 PATH ALREADY INCLUDES /v1/traces")
        print("   OTLPSpanExporter automatically appends /v1/traces")
        print("   This will cause 404 errors (double path)")
        print("\n💡 Fix: Remove /v1/traces from endpoint:")
        print(f"   CORRECT: http://{parsed.hostname}:{parsed.port}")
        print(f"   WRONG:   {endpoint}")
    elif parsed.port not in {4317, 4318}:
        issues.append(f"Unexpected port: {parsed.port} (expected 4317 or 4318)")

    if issues:
        print(f"\n⚠️  Issues found:")
        for issue in issues:
            print(f"   - {issue}")
        return None

    print(f"\n✅ Endpoint URL is valid")
    return parsed


def test_dns_resolution(hostname: str):
    """Test DNS resolution."""
    print_section("3. DNS Resolution Test")

    print(f"Testing DNS resolution for: {hostname}")

    try:
        # Resolve hostname to IP addresses
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)

        ips = set()
        for info in addr_info:
            ip = info[4][0]
            ips.add(ip)

        print(f"✅ DNS resolution successful!")
        print(f"   Resolved to {len(ips)} IP address(es):")
        for ip in sorted(ips):
            print(f"   - {ip}")

        return True

    except socket.gaierror as e:
        print(f"❌ DNS resolution failed: {e}")
        print("\n💡 Possible causes:")
        print("   1. Tempo service is not deployed on Railway")
        print("   2. Service name doesn't match (check Railway dashboard)")
        print("   3. Services are in different Railway projects")
        print("\n💡 Solutions:")
        print("   - Verify Tempo service is deployed: railway status")
        print("   - Check service name in Railway dashboard matches 'tempo'")
        print("   - If cross-project, use public URL instead of .railway.internal")
        return False


def test_tcp_connection(hostname: str, port: int, timeout: float = 5.0):
    """Test TCP connection."""
    print_section("4. TCP Connection Test")

    print(f"Testing TCP connection to: {hostname}:{port}")
    print(f"Timeout: {timeout}s")

    sock = None
    try:
        sock = socket.create_connection((hostname, port), timeout=timeout)
        print(f"✅ TCP connection successful!")
        print(f"   Connected to {hostname}:{port}")
        return True

    except ConnectionRefusedError:
        print(f"❌ Connection refused by {hostname}:{port}")
        print("\n💡 Possible causes:")
        print("   1. Tempo service is not running")
        print("   2. Tempo is not listening on this port")
        print("   3. Firewall blocking the connection")
        print("\n💡 Solutions:")
        print("   - Check Tempo logs in Railway dashboard")
        print("   - Verify Tempo Dockerfile exposes port 4318")
        print("   - Check tempo.yml has http endpoint on 0.0.0.0:4318")
        return False

    except TimeoutError:
        print(f"❌ Connection timed out after {timeout}s")
        print("\n💡 Possible causes:")
        print("   1. Network routing issue")
        print("   2. Tempo service is slow to respond")
        print("   3. Railway internal networking issue")
        return False

    except OSError as e:
        print(f"❌ Connection error: {e}")
        return False

    finally:
        if sock:
            sock.close()


def test_otlp_exporter():
    """Test OTLP exporter configuration."""
    print_section("5. OTLP Exporter Test")

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry import trace

        print("✅ OpenTelemetry packages are installed")

        endpoint = os.getenv("TEMPO_OTLP_HTTP_ENDPOINT", "http://localhost:4318")

        # Parse and validate endpoint
        parsed = urlparse(endpoint)
        if not parsed.port and ".railway.internal" in endpoint:
            # Auto-correct missing port
            endpoint = f"{parsed.scheme}://{parsed.hostname}:4318{parsed.path}"
            print(f"\n⚠️  Auto-corrected endpoint to: {endpoint}")

        full_endpoint = f"{endpoint}/v1/traces"
        print(f"\nCreating OTLP exporter with endpoint: {full_endpoint}")

        # Create exporter
        exporter = OTLPSpanExporter(
            endpoint=full_endpoint,
            timeout=10,
        )

        print("✅ OTLP exporter created successfully")

        # Create tracer provider
        resource = Resource.create({SERVICE_NAME: "test-service"})
        provider = TracerProvider(resource=resource)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        print("✅ TracerProvider configured")

        # Create a test span
        print("\nCreating test span...")
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test-span") as span:
            span.set_attribute("test", "value")
            print("✅ Test span created")

        # Force flush
        print("\nFlushing spans to Tempo...")
        provider.force_flush(timeout_millis=5000)
        print("✅ Spans flushed")

        # Shutdown
        provider.shutdown()
        print("✅ TracerProvider shutdown complete")

        print("\n✅ OTLP Exporter test PASSED!")
        print("   Traces should now appear in Grafana Tempo")
        return True

    except ImportError as e:
        print(f"❌ OpenTelemetry packages not installed: {e}")
        print("\n💡 Install with:")
        print("   pip install opentelemetry-api opentelemetry-sdk \\")
        print("               opentelemetry-exporter-otlp-proto-http")
        return False

    except Exception as e:
        print(f"❌ OTLP Exporter test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run all diagnostics."""
    print("=" * 80)
    print("  Tempo Connection Diagnostic Tool")
    print("  Gateway Z - OpenTelemetry Troubleshooting")
    print("=" * 80)

    # Step 1: Check environment variables
    if not check_environment_variables():
        print("\n❌ Environment check failed - fix issues above and try again")
        return 1

    # Step 2: Parse endpoint URL
    endpoint = os.getenv("TEMPO_OTLP_HTTP_ENDPOINT", "")
    parsed = parse_endpoint_url(endpoint)

    if not parsed:
        print("\n❌ Endpoint URL validation failed - fix issues above and try again")
        return 1

    # Step 3: Test DNS resolution
    if not test_dns_resolution(parsed.hostname):
        print("\n❌ DNS resolution failed - fix issues above and try again")
        return 1

    # Step 4: Test TCP connection
    port = parsed.port or 4318
    if not test_tcp_connection(parsed.hostname, port):
        print("\n❌ TCP connection failed - fix issues above and try again")
        return 1

    # Step 5: Test OTLP exporter
    if not test_otlp_exporter():
        print("\n⚠️  OTLP exporter test failed - but basic connectivity works")
        print("   Check application logs for more details")
        return 1

    # Success
    print("\n" + "=" * 80)
    print("  ✅ ALL DIAGNOSTICS PASSED!")
    print("  OpenTelemetry should now work correctly")
    print("=" * 80)

    print("\n📊 Next steps:")
    print("   1. Check Grafana for traces: http://localhost:3000/explore")
    print("   2. Query Tempo for recent traces")
    print("   3. Verify trace IDs appear in application logs")

    return 0


if __name__ == "__main__":
    sys.exit(main())
