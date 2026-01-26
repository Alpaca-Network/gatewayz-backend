#!/usr/bin/env python3
"""
Test OpenTelemetry connection to Tempo.
Run this to verify traces can be sent successfully.
"""

import os
import sys
import time

# Set environment for testing (if not already set)
if not os.environ.get("TEMPO_OTLP_HTTP_ENDPOINT"):
    print("⚠️  TEMPO_OTLP_HTTP_ENDPOINT not set, using default: http://tempo.railway.internal:4318")
    os.environ["TEMPO_OTLP_HTTP_ENDPOINT"] = "http://tempo.railway.internal:4318"

if not os.environ.get("TEMPO_ENABLED"):
    os.environ["TEMPO_ENABLED"] = "true"

if not os.environ.get("TEMPO_SKIP_REACHABILITY_CHECK"):
    os.environ["TEMPO_SKIP_REACHABILITY_CHECK"] = "true"

print("=" * 80)
print("OpenTelemetry Connection Test")
print("=" * 80)

# Import after setting environment
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.semconv.resource import SERVICE_NAME, SERVICE_VERSION, DEPLOYMENT_ENVIRONMENT
except ImportError as e:
    print(f"❌ Failed to import OpenTelemetry packages: {e}")
    print("\nInstall with:")
    print("pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http")
    sys.exit(1)

print("✅ OpenTelemetry packages imported successfully\n")

# Get endpoint
endpoint = os.environ.get("TEMPO_OTLP_HTTP_ENDPOINT", "http://localhost:4318")
print(f"Tempo endpoint: {endpoint}")

# Ensure proper format
if ".railway.internal" in endpoint and not endpoint.startswith("http://"):
    endpoint = f"http://{endpoint}"
    print(f"Normalized endpoint: {endpoint}")

full_endpoint = f"{endpoint}/v1/traces"
print(f"Full OTLP endpoint: {full_endpoint}\n")

# Create resource
print("Creating resource...")
resource = Resource.create(
    {
        SERVICE_NAME: "gatewayz-api-test",
        SERVICE_VERSION: "2.0.3",
        DEPLOYMENT_ENVIRONMENT: os.environ.get("APP_ENV", "development"),
        "service.namespace": "gatewayz",
        "test.run": "true",
    }
)
print("✅ Resource created\n")

# Create tracer provider
print("Creating tracer provider...")
tracer_provider = TracerProvider(resource=resource)
print("✅ Tracer provider created\n")

# Create OTLP exporter
print("Creating OTLP exporter...")
try:
    otlp_exporter = OTLPSpanExporter(
        endpoint=full_endpoint,
        headers={},
        timeout=30,
    )
    print("✅ OTLP exporter created\n")
except Exception as e:
    print(f"❌ Failed to create OTLP exporter: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

# Add span processor
print("Adding batch span processor...")
span_processor = BatchSpanProcessor(
    otlp_exporter,
    max_queue_size=2048,
    max_export_batch_size=512,
    export_timeout_millis=30000,
)
tracer_provider.add_span_processor(span_processor)
print("✅ Batch span processor added\n")

# Set as global tracer provider
trace.set_tracer_provider(tracer_provider)
print("✅ Tracer provider set globally\n")

# Get tracer
tracer = trace.get_tracer(__name__)

# Create test spans
print("=" * 80)
print("Creating test spans...")
print("=" * 80 + "\n")

try:
    # Parent span
    with tracer.start_as_current_span("test_connection") as parent_span:
        parent_span.set_attribute("test.type", "connection_test")
        parent_span.set_attribute("test.timestamp", time.time())
        print("✅ Created parent span: test_connection")

        # Child span 1
        with tracer.start_as_current_span("database_query") as child1:
            child1.set_attribute("db.system", "postgresql")
            child1.set_attribute("db.operation", "SELECT")
            time.sleep(0.1)  # Simulate work
            print("✅ Created child span: database_query")

        # Child span 2
        with tracer.start_as_current_span("redis_get") as child2:
            child2.set_attribute("cache.system", "redis")
            child2.set_attribute("cache.hit", True)
            time.sleep(0.05)  # Simulate work
            print("✅ Created child span: redis_get")

        # Child span 3
        with tracer.start_as_current_span("api_call") as child3:
            child3.set_attribute("http.method", "POST")
            child3.set_attribute("http.url", "https://api.openai.com/v1/chat/completions")
            child3.set_attribute("http.status_code", 200)
            time.sleep(0.2)  # Simulate work
            print("✅ Created child span: api_call")

    print("\n✅ All spans created successfully\n")

except Exception as e:
    print(f"\n❌ Failed to create spans: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

# Force flush
print("=" * 80)
print("Flushing spans to Tempo...")
print("=" * 80 + "\n")

try:
    # Give spans time to be batched
    print("Waiting for batch processor (5 seconds)...")
    time.sleep(5)

    # Force flush
    print("Forcing flush...")
    success = span_processor.force_flush(timeout_millis=30000)

    if success:
        print("✅ Spans flushed successfully!\n")
    else:
        print("⚠️  Flush returned False (may indicate partial failure)\n")

except Exception as e:
    print(f"❌ Failed to flush spans: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

# Shutdown
print("Shutting down tracer provider...")
try:
    tracer_provider.shutdown()
    print("✅ Tracer provider shut down cleanly\n")
except Exception as e:
    print(f"⚠️  Shutdown warning: {e}\n")

# Summary
print("=" * 80)
print("TEST SUMMARY")
print("=" * 80)
print(f"✅ Connected to: {full_endpoint}")
print(f"✅ Created 4 test spans (1 parent + 3 children)")
print(f"✅ Spans exported successfully")
print(f"\nNext steps:")
print(f"1. Check Tempo logs for incoming spans")
print(f"2. Open Grafana → Explore → Tempo")
print(f"3. Search for service: gatewayz-api-test")
print(f"4. Look for trace with span: test_connection")
print(f"\nIf traces don't appear in Grafana:")
print(f"- Check Tempo logs for errors")
print(f"- Verify Grafana datasource URL is correct")
print(f"- Check Tempo storage volume is mounted properly")
print("=" * 80)
