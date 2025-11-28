#!/usr/bin/env python3
"""
Statsig Integration Diagnostic Script
======================================

This script checks if Statsig is properly integrated and helps troubleshoot issues.

Run: python scripts/diagnostics/check_statsig_integration.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def check_environment_variables():
    """Check if required environment variables are set"""
    print("=" * 60)
    print("1. ENVIRONMENT VARIABLES CHECK")
    print("=" * 60)

    required_vars = {
        "STATSIG_SERVER_SECRET_KEY": os.getenv("STATSIG_SERVER_SECRET_KEY"),
        "APP_ENV": os.getenv("APP_ENV", "development"),
    }

    all_set = True
    for var_name, var_value in required_vars.items():
        if var_value:
            masked_value = var_value[:10] + "..." if len(var_value) > 10 else "[short]"
            print(f"  ✅ {var_name}: {masked_value}")
        else:
            print(f"  ❌ {var_name}: NOT SET")
            all_set = False

    print()
    if not all_set:
        print("⚠️  Missing required environment variables!")
        print("   To fix: Set STATSIG_SERVER_SECRET_KEY in your .env file")
        print("   Get key from: https://console.statsig.com")
        print("   Path: Project Settings → API Keys → Server Secret Key")
        print()

    return all_set


def check_package_installation():
    """Check if statsig-python-core is installed"""
    print("=" * 60)
    print("2. PACKAGE INSTALLATION CHECK")
    print("=" * 60)

    try:
        import statsig_python_core
        print(f"  ✅ statsig-python-core is installed")
        print(f"     Version: {statsig_python_core.__version__ if hasattr(statsig_python_core, '__version__') else 'unknown'}")
        print()
        return True
    except ImportError as e:
        print(f"  ❌ statsig-python-core is NOT installed")
        print(f"     Error: {e}")
        print()
        print("⚠️  Package not installed!")
        print("   To fix: pip install statsig-python-core")
        print()
        return False


async def check_service_initialization():
    """Check if Statsig service initializes correctly"""
    print("=" * 60)
    print("3. SERVICE INITIALIZATION CHECK")
    print("=" * 60)

    try:
        from src.services.statsig_service import statsig_service

        # Initialize the service
        await statsig_service.initialize()

        print(f"  Initialized: {statsig_service._initialized}")
        print(f"  Enabled: {statsig_service.enabled}")
        print(f"  Has SDK instance: {statsig_service.statsig is not None}")
        print(f"  Server key set: {statsig_service.server_secret_key is not None}")

        if statsig_service.enabled:
            print()
            print("  ✅ Statsig service initialized successfully!")
            print()
            return True
        else:
            print()
            print("  ⚠️  Statsig service is in fallback mode (not fully enabled)")
            print("     This means events will only be logged to console, not sent to Statsig")
            print()
            return False

    except Exception as e:
        print(f"  ❌ Failed to initialize Statsig service")
        print(f"     Error: {e}")
        import traceback
        print(f"\n{traceback.format_exc()}")
        print()
        return False


async def test_event_logging():
    """Test logging an event"""
    print("=" * 60)
    print("4. EVENT LOGGING TEST")
    print("=" * 60)

    try:
        from src.services.statsig_service import statsig_service

        # Ensure initialized
        if not statsig_service._initialized:
            await statsig_service.initialize()

        # Try logging a test event
        result = statsig_service.log_event(
            user_id="diagnostic_test_user",
            event_name="diagnostic_test_event",
            value="test_value",
            metadata={
                "source": "diagnostic_script",
                "test": True,
                "timestamp": "2025-11-28"
            }
        )

        if result:
            print(f"  ✅ Event logged successfully (enabled={statsig_service.enabled})")
            if statsig_service.enabled:
                print("     Event was sent to Statsig")
            else:
                print("     Event was logged to console only (fallback mode)")
            print()
            return True
        else:
            print(f"  ❌ Event logging failed")
            print()
            return False

    except Exception as e:
        print(f"  ❌ Failed to log test event")
        print(f"     Error: {e}")
        import traceback
        print(f"\n{traceback.format_exc()}")
        print()
        return False


def check_analytics_route():
    """Check if analytics route is properly registered"""
    print("=" * 60)
    print("5. ANALYTICS ROUTE CHECK")
    print("=" * 60)

    try:
        from src.routes import analytics

        print(f"  ✅ Analytics route module exists")
        print(f"     Router: {hasattr(analytics, 'router')}")
        print(f"     Endpoints: {[route.path for route in analytics.router.routes] if hasattr(analytics, 'router') else 'N/A'}")
        print()
        return True

    except ImportError as e:
        print(f"  ❌ Analytics route module not found")
        print(f"     Error: {e}")
        print()
        return False


async def check_main_app_integration():
    """Check if analytics is integrated in main.py"""
    print("=" * 60)
    print("6. MAIN APP INTEGRATION CHECK")
    print("=" * 60)

    main_py_path = project_root / "src" / "main.py"

    if not main_py_path.exists():
        print(f"  ❌ main.py not found at {main_py_path}")
        print()
        return False

    with open(main_py_path, 'r') as f:
        main_content = f.read()

    checks = {
        "Analytics route in routes_to_load": '"analytics"' in main_content or "'analytics'" in main_content,
        "Statsig service import on startup": "statsig_service" in main_content and "initialize()" in main_content,
        "Statsig shutdown on teardown": "statsig_service" in main_content and "shutdown()" in main_content,
    }

    all_passed = True
    for check_name, passed in checks.items():
        if passed:
            print(f"  ✅ {check_name}")
        else:
            print(f"  ❌ {check_name}")
            all_passed = False

    print()
    return all_passed


async def main():
    """Run all diagnostic checks"""
    print("\n" + "=" * 60)
    print("STATSIG INTEGRATION DIAGNOSTIC TOOL")
    print("=" * 60)
    print()

    results = []

    # 1. Check environment variables
    results.append(("Environment Variables", check_environment_variables()))

    # 2. Check package installation
    results.append(("Package Installation", check_package_installation()))

    # 3. Check service initialization
    results.append(("Service Initialization", await check_service_initialization()))

    # 4. Test event logging
    results.append(("Event Logging", await test_event_logging()))

    # 5. Check analytics route
    results.append(("Analytics Route", check_analytics_route()))

    # 6. Check main app integration
    results.append(("Main App Integration", await check_main_app_integration()))

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for check_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status} - {check_name}")

    print()

    all_passed = all(passed for _, passed in results)

    if all_passed:
        print("🎉 All checks passed! Statsig integration is working correctly.")
        print()
        print("Next steps:")
        print("  1. Test the analytics endpoint: POST /v1/analytics/events")
        print("  2. Check Statsig dashboard for events: https://console.statsig.com")
        print()
    else:
        print("⚠️  Some checks failed. Please review the errors above.")
        print()
        print("Common fixes:")
        print("  1. Set STATSIG_SERVER_SECRET_KEY in .env file")
        print("  2. Install package: pip install statsig-python-core")
        print("  3. Restart your application after setting env vars")
        print()

    # Shutdown
    try:
        from src.services.statsig_service import statsig_service
        await statsig_service.shutdown()
    except:
        pass

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
