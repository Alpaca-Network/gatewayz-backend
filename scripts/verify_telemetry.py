#!/usr/bin/env python3
"""
Verify OpenTelemetry/Tempo configuration for distributed tracing.

This script checks:
1. Environment variables are set correctly
2. Tempo endpoint is reachable
3. OpenTelemetry packages are installed
4. Configuration is consistent
"""

import os
import socket
import sys
from urllib.parse import urlparse


def check_env_vars():
    """Check required environment variables."""
    print("🔍 Checking environment variables...")

    required_vars = {
        "TEMPO_ENABLED": os.getenv("TEMPO_ENABLED", "false"),
        "TEMPO_OTLP_HTTP_ENDPOINT": os.getenv("TEMPO_OTLP_HTTP_ENDPOINT", "not set"),
        "OTEL_SERVICE_NAME": os.getenv("OTEL_SERVICE_NAME", "not set"),
        "TEMPO_SKIP_REACHABILITY_CHECK": os.getenv("TEMPO_SKIP_REACHABILITY_CHECK", "false"),
    }

    print("\nEnvironment Variables:")
    for key, value in required_vars.items():
        print(f"  ✓ {key}: {value}")

    # Check for endpoint consistency
    tempo_endpoint = os.getenv("TEMPO_OTLP_HTTP_ENDPOINT", "")
    otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

    if tempo_endpoint and otel_endpoint:
        # Remove /v1/traces from otel_endpoint for comparison
        otel_base = otel_endpoint.replace("/v1/traces", "")
        if tempo_endpoint != otel_base:
            print(f"\n⚠️  WARNING: Endpoint mismatch detected!")
            print(f"  TEMPO_OTLP_HTTP_ENDPOINT: {tempo_endpoint}")
            print(f"  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: {otel_endpoint}")
            print(f"  These should point to the same Tempo instance.")
        else:
            print(f"\n✅ Endpoint configuration is consistent")

    return required_vars


def check_tempo_connectivity(endpoint: str, timeout: float = 2.0):
    """Check if Tempo endpoint is reachable."""
    print(f"\n🌐 Checking Tempo endpoint connectivity...")

    if not endpoint or endpoint == "not set":
        print("  ❌ TEMPO_OTLP_HTTP_ENDPOINT not set")
        return False

    try:
        parsed = urlparse(endpoint)
        host = parsed.hostname
        port = parsed.port or 4318

        print(f"  Testing connection to {host}:{port}...")

        # DNS resolution
        try:
            socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            print(f"  ✓ DNS resolution successful for {host}")
        except socket.gaierror as e:
            print(f"  ❌ DNS resolution failed: {e}")
            return False

        # TCP connection
        sock = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            print(f"  ✓ TCP connection successful to {host}:{port}")
            return True
        except (TimeoutError, ConnectionRefusedError, OSError) as e:
            print(f"  ❌ Connection failed: {e}")
            print(f"\n💡 Troubleshooting:")
            print(f"  1. Ensure Tempo service is running on Railway")
            print(f"  2. Check Railway internal networking is enabled")
            print(f"  3. Verify the service name 'tempo' matches your Railway service")
            return False
        finally:
            if sock:
                sock.close()

    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        return False


def check_opentelemetry_packages():
    """Check if OpenTelemetry packages are installed."""
    print("\n📦 Checking OpenTelemetry packages...")

    required_packages = [
        "opentelemetry.api",
        "opentelemetry.sdk",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.instrumentation.fastapi",
    ]

    all_installed = True
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✓ {package}")
        except ImportError:
            print(f"  ❌ {package} not installed")
            all_installed = False

    if not all_installed:
        print("\n📥 To install missing packages, run:")
        print("  pip install opentelemetry-api opentelemetry-sdk \\")
        print("              opentelemetry-exporter-otlp \\")
        print("              opentelemetry-instrumentation-fastapi \\")
        print("              opentelemetry-instrumentation-httpx \\")
        print("              opentelemetry-instrumentation-requests")

    return all_installed


def check_config_file():
    """Check OpenTelemetry configuration file."""
    print("\n📄 Checking configuration file...")

    config_path = "src/config/opentelemetry_config.py"
    if os.path.exists(config_path):
        print(f"  ✓ {config_path} exists")

        # Check if initialization is being called
        main_path = "src/main.py"
        if os.path.exists(main_path):
            with open(main_path, "r") as f:
                content = f.read()
                if "OpenTelemetryConfig.initialize()" in content:
                    print(f"  ✓ OpenTelemetry initialization found in {main_path}")
                else:
                    print(f"  ⚠️  OpenTelemetry initialization not found in {main_path}")
        return True
    else:
        print(f"  ❌ {config_path} not found")
        return False


def main():
    """Run all verification checks."""
    print("=" * 60)
    print("Gateway Z - Tempo/OpenTelemetry Configuration Verification")
    print("=" * 60)

    # Load .env file if it exists
    try:
        from dotenv import load_dotenv

        load_dotenv()
        print("✓ Loaded .env file\n")
    except ImportError:
        print("⚠️  python-dotenv not installed, skipping .env file\n")

    # Run checks
    env_vars = check_env_vars()
    packages_ok = check_opentelemetry_packages()
    config_ok = check_config_file()

    # Only check connectivity if enabled
    connectivity_ok = True
    if env_vars.get("TEMPO_ENABLED", "false").lower() in ["true", "1", "yes"]:
        endpoint = env_vars.get("TEMPO_OTLP_HTTP_ENDPOINT", "")
        connectivity_ok = check_tempo_connectivity(endpoint)
    else:
        print("\n⏭️  Tempo is disabled, skipping connectivity check")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_ok = packages_ok and config_ok

    if env_vars.get("TEMPO_ENABLED", "false").lower() in ["true", "1", "yes"]:
        all_ok = all_ok and connectivity_ok

        if all_ok:
            print("✅ All checks passed! Telemetry should be working.")
            print("\n📊 To verify traces are being sent:")
            print("  1. Make some API requests to your backend")
            print("  2. Check Grafana/Tempo dashboard for traces")
            print("  3. Look for service: gatewayz-api")
        else:
            print("❌ Some checks failed. Please fix the issues above.")
            sys.exit(1)
    else:
        print("⏭️  Tempo/OpenTelemetry is disabled")
        print("   Set TEMPO_ENABLED=true to enable distributed tracing")


if __name__ == "__main__":
    main()
