#!/usr/bin/env python3
"""
Rate Limit Monitor & Auto-Start Script
Monitors API rate limit status and automatically starts comprehensive test when limits reset
"""

import asyncio
import time
import httpx
from datetime import datetime
import subprocess
import sys

# Configuration
API_BASE_URL = "https://api.gatewayz.ai"
API_KEY = "gw_live_keYT21TicJZzxObd8-6LJukxOg5p0CLo_3Yki83w3pU"
CHECK_INTERVAL = 60  # Check every 60 seconds
MAX_WAIT_TIME = 7200  # Maximum wait time: 2 hours


async def check_rate_limit_status():
    """Check if API is currently rate limited"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "test"}],
        "max_tokens": 1,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{API_BASE_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=10.0,
            )

            if response.status_code == 429:
                # Still rate limited
                try:
                    error_data = response.json()
                    error_detail = error_data.get("detail", "Rate limit exceeded")
                except:
                    error_detail = response.text[:100]

                return {
                    "rate_limited": True,
                    "status_code": 429,
                    "error": error_detail,
                }
            elif response.status_code == 200:
                # Rate limit has reset!
                return {
                    "rate_limited": False,
                    "status_code": 200,
                    "message": "Rate limit cleared - API is accessible",
                }
            else:
                # Other error
                return {
                    "rate_limited": False,
                    "status_code": response.status_code,
                    "error": f"Unexpected status: {response.status_code}",
                }

    except Exception as e:
        return {
            "rate_limited": None,
            "error": f"Check failed: {str(e)}",
        }


async def monitor_and_start():
    """Monitor rate limits and auto-start test when ready"""
    print("=" * 80)
    print("RATE LIMIT MONITOR & AUTO-START")
    print("=" * 80)
    print(f"API Endpoint: {API_BASE_URL}")
    print(f"Check Interval: {CHECK_INTERVAL} seconds")
    print(f"Max Wait Time: {MAX_WAIT_TIME / 3600:.1f} hours")
    print("=" * 80)
    print()

    start_time = time.time()
    check_count = 0

    print("🔍 Performing initial rate limit check...")
    initial_status = await check_rate_limit_status()

    if initial_status["rate_limited"] == False and initial_status["status_code"] == 200:
        print("✅ API is ready! Rate limits are clear.")
        print("🚀 Starting comprehensive performance test now...")
        print()
        return True
    elif initial_status["rate_limited"] == True:
        print(f"⏳ API is currently rate limited: {initial_status['error']}")
        print(f"⏰ Will check every {CHECK_INTERVAL} seconds until limits reset...")
        print()
    else:
        print(f"⚠️  Unexpected status: {initial_status.get('error', 'Unknown')}")
        print("⏰ Will continue monitoring...")
        print()

    # Monitoring loop
    while True:
        elapsed = time.time() - start_time

        if elapsed > MAX_WAIT_TIME:
            print(f"\n❌ Maximum wait time ({MAX_WAIT_TIME/3600:.1f} hours) exceeded!")
            print("Rate limits have not reset. Please check your API configuration.")
            return False

        # Wait before next check
        print(f"⏰ Waiting {CHECK_INTERVAL} seconds before next check... (elapsed: {elapsed/60:.1f}min)", end="\r")
        await asyncio.sleep(CHECK_INTERVAL)

        # Check status
        check_count += 1
        status = await check_rate_limit_status()

        timestamp = datetime.now().strftime("%H:%M:%S")

        if status["rate_limited"] == False and status["status_code"] == 200:
            print(f"\n\n✅ [{timestamp}] Rate limits cleared after {elapsed/60:.1f} minutes!")
            print(f"📊 Total checks performed: {check_count}")
            print()
            return True
        elif status["rate_limited"] == True:
            print(f"\n⏳ [{timestamp}] Still rate limited (check #{check_count}): {status['error'][:60]}")
            print(f"   Elapsed: {elapsed/60:.1f} min | Remaining: {(MAX_WAIT_TIME - elapsed)/60:.1f} min")
        else:
            print(f"\n⚠️  [{timestamp}] Check #{check_count} - Unexpected status: {status.get('error', 'Unknown')[:60]}")


def start_comprehensive_test():
    """Start the comprehensive performance test"""
    print("=" * 80)
    print("LAUNCHING COMPREHENSIVE PERFORMANCE TEST")
    print("=" * 80)
    print("⚙️  Mode: Sequential (1 model at a time)")
    print("⏱️  Delay: 30 seconds between requests (SAFE MODE)")
    print("📊 Expected duration: ~3-4 hours for 337 models")
    print("=" * 80)
    print()

    try:
        # Run the comprehensive test
        result = subprocess.run(
            [sys.executable, "-u", "comprehensive_performance_test.py"],
            check=False,
        )

        if result.returncode == 0:
            print("\n\n✅ Comprehensive test completed successfully!")
        else:
            print(f"\n\n⚠️  Test exited with code {result.returncode}")

        return result.returncode

    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user!")
        print("💾 Partial results have been saved.")
        return 130
    except Exception as e:
        print(f"\n\n❌ Error running test: {e}")
        return 1


async def main():
    """Main function"""
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║       GATEWAYZ RATE LIMIT MONITOR & AUTO-START SCRIPT           ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()

    # Monitor rate limits
    ready = await monitor_and_start()

    if ready:
        print("🚀 Rate limits cleared! Starting comprehensive test in 5 seconds...")
        print("    Press Ctrl+C to cancel...")

        try:
            await asyncio.sleep(5)
        except KeyboardInterrupt:
            print("\n\n⚠️  Auto-start cancelled by user.")
            print("You can manually run: python comprehensive_performance_test.py")
            return

        # Start the test
        exit_code = start_comprehensive_test()

        print()
        print("=" * 80)
        print("MONITOR & TEST COMPLETE")
        print("=" * 80)

        sys.exit(exit_code)
    else:
        print()
        print("=" * 80)
        print("MONITORING STOPPED")
        print("=" * 80)
        print("Rate limits did not reset within the maximum wait time.")
        print("Please check your API configuration or try again later.")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Monitoring interrupted by user.")
        print("You can restart this script anytime to resume monitoring.")
        sys.exit(130)
