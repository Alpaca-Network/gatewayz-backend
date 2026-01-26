#!/usr/bin/env python3
"""
Clear the stuck sync from our test run
"""

from datetime import datetime
from supabase import create_client, Client

STAGING_URL = "https://ynleroehyrmaafkgjgmr.supabase.co"
STAGING_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlubGVyb2VoeXJtYWFma2dqZ21yIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTY4Nzc3OSwiZXhwIjoyMDc1MjYzNzc5fQ.kIehmSJC9EX86rkhCbhzX6ZHiTfQO7k6ZM2wU4e6JNs"

supabase: Client = create_client(STAGING_URL, STAGING_KEY)

# Find all in_progress syncs
stuck_syncs = supabase.table('pricing_sync_log').select('*').eq('status', 'in_progress').execute()

if stuck_syncs.data:
    print(f"Found {len(stuck_syncs.data)} stuck syncs")
    for sync in stuck_syncs.data:
        print(f"\nSync ID {sync['id']}:")
        print(f"  Provider: {sync['provider_slug']}")
        print(f"  Started: {sync['sync_started_at']}")
        print(f"  Status: {sync['status']}")

        # Mark as failed
        result = supabase.table('pricing_sync_log').update({
            'status': 'failed',
            'sync_completed_at': datetime.now().isoformat(),
            'error_message': 'Sync exceeded timeout duration'
        }).eq('id', sync['id']).execute()

        print(f"  ✅ Marked as failed")
else:
    print("No stuck syncs found")
