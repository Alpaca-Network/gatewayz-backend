#!/usr/bin/env python3
"""
Verify that chat_completion_requests table exists in remote Supabase database
"""
import os
import sys

try:
    from supabase import create_client
except ImportError:
    print("❌ supabase-py not installed")
    print("Run: pip install supabase")
    sys.exit(1)

# Get credentials from environment
url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_KEY')

if not url or not key:
    print("❌ SUPABASE_URL or SUPABASE_KEY not set")
    print("Make sure these environment variables are configured")
    sys.exit(1)

try:
    # Create client
    client = create_client(url, key)

    # Try to query the table (even if empty)
    result = client.table('chat_completion_requests').select('id').limit(1).execute()

    print("✅ SUCCESS! Table 'chat_completion_requests' exists in your remote Supabase database!")
    print(f"   Database URL: {url[:30]}...")
    print(f"   Table query successful")

    # Get table info
    count_result = client.table('chat_completion_requests').select('id', count='exact').execute()
    record_count = count_result.count if hasattr(count_result, 'count') else 0
    print(f"   Current records: {record_count}")
    print()
    print("🎉 Your chat completion request tracking is now LIVE!")
    print("   All requests will be automatically logged to this table.")

except Exception as e:
    error_msg = str(e)
    if 'does not exist' in error_msg.lower() or 'relation' in error_msg.lower():
        print("❌ Table 'chat_completion_requests' does NOT exist yet")
        print(f"   Error: {error_msg}")
        print()
        print("   Please run the SQL manually in Supabase SQL Editor:")
        print("   https://supabase.com/dashboard/project/ynleroehyrmaafkgjgmr/sql")
    else:
        print(f"❌ Error checking table: {error_msg}")
    sys.exit(1)
