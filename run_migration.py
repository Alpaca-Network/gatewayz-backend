from src.config.supabase_config import get_supabase_client

# Read the migration SQL
with open('supabase/migrations/20260115000001_add_cost_tracking_to_chat_completion_requests.sql', 'r') as f:
    migration_sql = f.read()

print("Running migration SQL...")
print("="*80)

client = get_supabase_client()

# Split into individual statements (simple approach)
statements = [s.strip() for s in migration_sql.split(';') if s.strip() and not s.strip().startswith('--')]

for idx, statement in enumerate(statements, 1):
    if statement:
        print(f"\n{idx}. Executing statement...")
        # Show first 100 chars
        preview = statement[:100].replace('\n', ' ')
        print(f"   {preview}...")
        try:
            result = client.rpc('exec_sql', {'query': statement}).execute()
            print(f"   ✅ Success")
        except Exception as e:
            print(f"   ⚠️  Error: {e}")
            # Continue with other statements

print("\n" + "="*80)
print("Migration execution complete!")
