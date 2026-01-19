import os
import sys

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

# Get Supabase credentials
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
    print("❌ SUPABASE_URL or SUPABASE_KEY not set in environment")
    sys.exit(1)

client = create_client(url, key)

print("Checking current table schema...")
print("="*80)

# Try to query with cost_usd column
try:
    result = client.table("chat_completion_requests").select("cost_usd").limit(1).execute()
    print("✅ Column 'cost_usd' EXISTS!")
    print(f"   Sample data: {result.data}")
except Exception as e:
    print(f"❌ Column 'cost_usd' DOES NOT EXIST")
    print(f"   Error: {str(e)[:200]}")
    
    print("\n⚠️  The migration needs to be applied manually.")
    print("\nTo apply the migration:")
    print("1. Go to your Supabase project dashboard")
    print("2. Navigate to SQL Editor")
    print("3. Run the migration file: supabase/migrations/20260115000001_add_cost_tracking_to_chat_completion_requests.sql")
    print("\nOr use the Supabase CLI with database connection details.")

print("\n" + "="*80)
