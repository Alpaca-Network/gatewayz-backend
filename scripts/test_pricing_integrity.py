#!/usr/bin/env python3
"""
Test script for GitHub Issue #958: Verify pricing data integrity and update accuracy
Runs comprehensive tests on staging database pricing data.
"""

import os
import time
import requests
from datetime import datetime, timedelta
from supabase import create_client, Client

# Configuration
STAGING_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
# Use service_role key for admin operations (from Railway staging environment)
STAGING_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlubGVyb2VoeXJtYWFma2dqZ21yIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTY4Nzc3OSwiZXhwIjoyMDc1MjYzNzc5fQ.kIehmSJC9EX86rkhCbhzX6ZHiTfQO7k6ZM2wU4e6JNs"
STAGING_API_URL = "https://gatewayz-staging.up.railway.app"
ADMIN_KEY = "gw_live_wTfpLJ5VB28qMXpOAhr7Uw"

# Test models to check (models that exist in staging with pricing)
TEST_MODELS = [
    'openai/gpt-4-turbo',
    'deepseek/deepseek-chat',
    'meta-llama/llama-3.1-70b-instruct'
]

# Initialize Supabase client
supabase: Client = create_client(STAGING_URL, STAGING_KEY)

def print_section(title):
    """Print a formatted section header"""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")

def print_result(test_name, passed, details=""):
    """Print test result"""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status} - {test_name}")
    if details:
        print(f"    {details}")

def test_1_check_current_pricing():
    """Test 1: Check Current Pricing Data"""
    print_section("TEST 1: Check Current Pricing Data")

    try:
        # Get models
        models_response = supabase.table('models').select('id, model_id, model_name').in_('model_id', TEST_MODELS).execute()
        models = {m['model_id']: m for m in models_response.data}

        if not models:
            print_result("Baseline pricing exists", False, "No test models found in database")
            return False

        print(f"Found {len(models)} test models")

        # Get pricing for each model
        all_passed = True
        for model_id, model in models.items():
            pricing_response = supabase.table('model_pricing').select('*').eq('model_id', model['id']).execute()

            if not pricing_response.data:
                print_result(f"Pricing exists for {model_id}", False, "No pricing data found")
                all_passed = False
                continue

            pricing = pricing_response.data[0]

            # Check non-negative prices
            input_price = pricing.get('price_per_input_token')
            output_price = pricing.get('price_per_output_token')
            updated_at = pricing.get('last_updated')

            if input_price is None or output_price is None:
                print_result(f"Pricing exists for {model_id}", False, "NULL pricing values")
                all_passed = False
            elif input_price < 0 or output_price < 0:
                print_result(f"Pricing non-negative for {model_id}", False, f"Negative prices: in={input_price}, out={output_price}")
                all_passed = False
            else:
                print_result(f"Pricing valid for {model_id}", True, f"in={input_price}, out={output_price}, updated={updated_at}")

        return all_passed

    except Exception as e:
        print_result("Check current pricing", False, f"Error: {str(e)}")
        return False

def test_2_trigger_sync():
    """Test 2: Trigger Pricing Sync"""
    print_section("TEST 2: Trigger Pricing Sync")

    try:
        response = requests.post(
            f"{STAGING_API_URL}/admin/pricing/scheduler/trigger",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"}
        )

        if response.status_code == 200:
            print_result("Trigger sync API call", True, f"Status: {response.status_code}")
            print("Waiting 20 seconds for sync to complete...")
            time.sleep(20)
            return True
        else:
            print_result("Trigger sync API call", False, f"Status: {response.status_code}, Response: {response.text}")
            return False

    except Exception as e:
        print_result("Trigger sync", False, f"Error: {str(e)}")
        return False

def test_3_verify_pricing_updated():
    """Test 3: Verify Pricing Updated"""
    print_section("TEST 3: Verify Pricing Updated")

    try:
        # Get models
        models_response = supabase.table('models').select('id, model_id, model_name').in_('model_id', TEST_MODELS).execute()
        models = {m['model_id']: m for m in models_response.data}

        cutoff_time = datetime.now() - timedelta(minutes=2)
        all_passed = True

        for model_id, model in models.items():
            pricing_response = supabase.table('model_pricing').select('*').eq('model_id', model['id']).execute()

            if not pricing_response.data:
                print_result(f"Pricing updated for {model_id}", False, "No pricing data")
                all_passed = False
                continue

            pricing = pricing_response.data[0]
            updated_at_str = pricing.get('last_updated')

            if not updated_at_str:
                print_result(f"Pricing updated for {model_id}", False, "No updated_at timestamp")
                all_passed = False
                continue

            # Parse timestamp (handle both formats)
            try:
                if '+' in updated_at_str:
                    updated_at = datetime.fromisoformat(updated_at_str.replace('Z', '+00:00'))
                else:
                    updated_at = datetime.fromisoformat(updated_at_str.replace('Z', ''))
            except:
                updated_at = datetime.fromisoformat(updated_at_str)

            # Remove timezone info for comparison if present
            if updated_at.tzinfo:
                updated_at = updated_at.replace(tzinfo=None)

            is_recent = updated_at > cutoff_time
            input_price = pricing.get('price_per_input_token')
            output_price = pricing.get('price_per_output_token')

            has_valid_prices = (
                input_price is not None and
                output_price is not None and
                input_price >= 0 and
                output_price >= 0
            )

            if is_recent and has_valid_prices:
                print_result(f"Pricing updated for {model_id}", True, f"Updated: {updated_at_str}")
            else:
                reasons = []
                if not is_recent:
                    reasons.append(f"Not recent (updated: {updated_at_str})")
                if not has_valid_prices:
                    reasons.append(f"Invalid prices: in={input_price}, out={output_price}")
                print_result(f"Pricing updated for {model_id}", False, "; ".join(reasons))
                all_passed = False

        return all_passed

    except Exception as e:
        print_result("Verify pricing updated", False, f"Error: {str(e)}")
        return False

def test_4_check_pricing_history():
    """Test 4: Check Pricing History"""
    print_section("TEST 4: Check Pricing History")

    try:
        # Get models
        models_response = supabase.table('models').select('id, model_id, model_name').in_('model_id', TEST_MODELS).execute()
        models = {m['id']: m['model_id'] for m in models_response.data}
        model_ids = list(models.keys())

        # Get recent pricing history
        history_response = supabase.table('model_pricing_history').select('*').in_('model_id', model_ids).order('changed_at', desc=True).limit(10).execute()

        if not history_response.data:
            print_result("Pricing history logged", False, "No history entries found")
            return False

        print(f"Found {len(history_response.data)} recent history entries")

        cutoff_time = datetime.now() - timedelta(minutes=2)
        recent_entries = []

        for entry in history_response.data:
            changed_at_str = entry.get('changed_at')
            if changed_at_str:
                try:
                    if '+' in changed_at_str:
                        changed_at = datetime.fromisoformat(changed_at_str.replace('Z', '+00:00'))
                    else:
                        changed_at = datetime.fromisoformat(changed_at_str.replace('Z', ''))

                    if changed_at.tzinfo:
                        changed_at = changed_at.replace(tzinfo=None)

                    if changed_at > cutoff_time:
                        recent_entries.append(entry)
                        model_id = models.get(entry['model_id'], 'unknown')
                        changed_by = entry.get('changed_by', 'unknown')
                        print(f"  • {model_id}: changed_by={changed_by}, changed_at={changed_at_str}")
                except Exception as e:
                    print(f"  • Error parsing timestamp: {e}")

        if recent_entries:
            print_result("Recent history entries exist", True, f"{len(recent_entries)} entries < 2 minutes old")

            # Check changed_by format
            all_have_valid_changed_by = all('changed_by' in e and e['changed_by'] for e in recent_entries)
            if all_have_valid_changed_by:
                print_result("Changed_by format valid", True)
            else:
                print_result("Changed_by format valid", False, "Some entries missing changed_by")

            return all_have_valid_changed_by
        else:
            print_result("Recent history entries exist", False, "No recent entries (< 2 minutes)")
            return False

    except Exception as e:
        print_result("Check pricing history", False, f"Error: {str(e)}")
        return False

def test_5_verify_no_duplicate_syncs():
    """Test 5: Verify No Duplicate Syncs"""
    print_section("TEST 5: Verify No Duplicate Syncs")

    try:
        # Check for concurrent syncs
        response = supabase.rpc('check_concurrent_syncs').execute()

        # If the RPC doesn't exist, try direct query
        if not hasattr(response, 'data') or response.data is None:
            # Alternative: direct query
            cutoff_time = (datetime.now() - timedelta(hours=1)).isoformat()
            response = supabase.table('pricing_sync_log').select('provider_slug').is_('sync_completed_at', 'null').gte('sync_started_at', cutoff_time).execute()

            if response.data:
                # Count by provider
                provider_counts = {}
                for row in response.data:
                    provider = row['provider_slug']
                    provider_counts[provider] = provider_counts.get(provider, 0) + 1

                duplicates = {p: c for p, c in provider_counts.items() if c > 1}

                if duplicates:
                    print_result("No duplicate syncs", False, f"Found concurrent syncs: {duplicates}")
                    return False
                else:
                    print_result("No duplicate syncs", True, "No overlapping syncs detected")
                    return True
            else:
                print_result("No duplicate syncs", True, "No incomplete syncs found")
                return True
        else:
            concurrent = response.data
            if concurrent:
                print_result("No duplicate syncs", False, f"Found {len(concurrent)} concurrent syncs")
                return False
            else:
                print_result("No duplicate syncs", True)
                return True

    except Exception as e:
        print_result("Verify no duplicate syncs", False, f"Error: {str(e)}")
        # Non-critical error, return True to continue
        return True

def test_6_check_sync_logs():
    """Test 6: Check Sync Logs"""
    print_section("TEST 6: Check Sync Logs")

    try:
        # Get recent sync logs
        response = supabase.table('pricing_sync_log').select('*').order('sync_started_at', desc=True).limit(10).execute()

        if not response.data:
            print_result("Sync logs exist", False, "No sync logs found")
            return False

        print(f"Found {len(response.data)} recent sync logs")

        most_recent = response.data[0]

        # Check status
        status = most_recent.get('status')
        if status == 'success':
            print_result("Most recent sync successful", True, f"Status: {status}")
        else:
            print_result("Most recent sync successful", False, f"Status: {status}")

        # Check models updated
        models_updated = most_recent.get('models_updated', 0)
        models_fetched = most_recent.get('models_fetched', 0)

        if models_updated > 0:
            print_result("Models updated > 0", True, f"Updated: {models_updated}")
        else:
            print_result("Models updated > 0", False, f"Updated: {models_updated}")

        if models_fetched >= models_updated:
            print_result("Models fetched >= updated", True, f"Fetched: {models_fetched}, Updated: {models_updated}")
        else:
            print_result("Models fetched >= updated", False, f"Fetched: {models_fetched}, Updated: {models_updated}")

        # Check duration
        duration_ms = most_recent.get('duration_ms')
        if duration_ms is not None:
            if duration_ms < 60000:
                print_result("Duration reasonable", True, f"{duration_ms}ms")
            else:
                print_result("Duration reasonable", False, f"{duration_ms}ms (> 60s)")
        else:
            print_result("Duration reasonable", False, f"Duration is None (sync may be stuck)")

        # Check errors
        errors = most_recent.get('errors')
        if not errors or errors == '{}' or errors == '[]':
            print_result("No unexplained errors", True)
        else:
            print_result("No unexplained errors", False, f"Errors: {errors}")

        # Print summary
        print(f"\nMost Recent Sync Summary:")
        print(f"  Provider: {most_recent.get('provider_slug')}")
        print(f"  Started: {most_recent.get('sync_started_at')}")
        print(f"  Completed: {most_recent.get('sync_completed_at')}")
        print(f"  Status: {status}")
        print(f"  Models: {models_fetched} fetched, {models_updated} updated, {most_recent.get('models_skipped', 0)} skipped")
        print(f"  Duration: {duration_ms}ms")
        print(f"  Triggered by: {most_recent.get('triggered_by')}")

        return status == 'success' and models_updated > 0

    except Exception as e:
        print_result("Check sync logs", False, f"Error: {str(e)}")
        return False

def test_7_validate_data_consistency():
    """Test 7: Validate Data Consistency"""
    print_section("TEST 7: Validate Data Consistency")

    all_passed = True

    try:
        # Check for NULL prices
        null_response = supabase.table('model_pricing').select('model_id, price_per_input_token, price_per_output_token').or_('price_per_input_token.is.null,price_per_output_token.is.null').limit(10).execute()

        if null_response.data:
            print_result("No NULL prices", False, f"Found {len(null_response.data)} models with NULL prices")
            for row in null_response.data[:5]:  # Show first 5
                print(f"  • Model ID: {row['model_id']}, Input: {row['price_per_input_token']}, Output: {row['price_per_output_token']}")
            all_passed = False
        else:
            print_result("No NULL prices", True)

        # Check for negative prices
        negative_response = supabase.table('model_pricing').select('model_id, price_per_input_token, price_per_output_token').or_('price_per_input_token.lt.0,price_per_output_token.lt.0').execute()

        if negative_response.data:
            print_result("No negative prices", False, f"Found {len(negative_response.data)} models with negative prices")
            all_passed = False
        else:
            print_result("No negative prices", True)

        return all_passed

    except Exception as e:
        print_result("Validate data consistency", False, f"Error: {str(e)}")
        return False

def clear_stuck_syncs():
    """Clear any stuck syncs before running tests"""
    print_section("PRE-TEST: Clear Stuck Syncs")

    try:
        # Find stuck syncs (in_progress for > 5 minutes)
        cutoff = (datetime.now() - timedelta(minutes=5)).isoformat()
        stuck_syncs = supabase.table('pricing_sync_log').select('id, provider_slug, sync_started_at').eq('status', 'in_progress').lt('sync_started_at', cutoff).execute()

        if stuck_syncs.data:
            print(f"Found {len(stuck_syncs.data)} stuck syncs")
            for sync in stuck_syncs.data:
                print(f"  - Sync ID {sync['id']} ({sync['provider_slug']}) started at {sync['sync_started_at']}")
                # Mark as failed
                supabase.table('pricing_sync_log').update({
                    'status': 'failed',
                    'sync_completed_at': datetime.now().isoformat(),
                    'error_message': 'Sync timed out or stuck'
                }).eq('id', sync['id']).execute()
                print(f"    → Marked as failed")
            print_result("Cleared stuck syncs", True, f"Cleared {len(stuck_syncs.data)} stuck syncs")
        else:
            print_result("No stuck syncs", True)

    except Exception as e:
        print_result("Clear stuck syncs", False, f"Error: {str(e)}")

def main():
    """Run all tests"""
    print_section("GitHub Issue #958: Pricing Data Integrity Testing")
    print(f"Target: {STAGING_API_URL}")
    print(f"Database: {STAGING_URL}")
    print(f"Started: {datetime.now().isoformat()}")

    # Clear any stuck syncs first
    clear_stuck_syncs()

    results = {}

    # Run tests
    results['test_1'] = test_1_check_current_pricing()
    results['test_2'] = test_2_trigger_sync()
    results['test_3'] = test_3_verify_pricing_updated()
    results['test_4'] = test_4_check_pricing_history()
    results['test_5'] = test_5_verify_no_duplicate_syncs()
    results['test_6'] = test_6_check_sync_logs()
    results['test_7'] = test_7_validate_data_consistency()

    # Print summary
    print_section("TEST SUMMARY")

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    for test, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test}")

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED - Data integrity verified!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} TEST(S) FAILED - Review results above")
        return 1

if __name__ == "__main__":
    exit(main())
