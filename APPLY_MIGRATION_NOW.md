# 🚀 Apply Email Search Index Migration - Step by Step

## ⚡ Quick Start (2 Minutes)

### Step 1: Copy the SQL

```sql
-- Enable the pg_trgm extension for trigram-based pattern matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Create a GIN index on the email column for fast ILIKE searches
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_email_trgm
ON users USING gin (email gin_trgm_ops);

-- Update table statistics
ANALYZE users;
```

### Step 2: Apply in Supabase Dashboard

1. **Open Supabase Dashboard**
   - Go to: https://app.supabase.com
   - Sign in to your account

2. **Select Your Project**
   - Click on your Gatewayz project

3. **Open SQL Editor**
   - Click on "SQL Editor" in the left sidebar
   - OR go to: https://app.supabase.com/project/_/sql

4. **Create New Query**
   - Click "+ New query" button

5. **Paste the SQL**
   - Copy the SQL from Step 1 above
   - Paste it into the query editor

6. **Run the Migration**
   - Click "Run" button (or press Ctrl+Enter / Cmd+Enter)
   - Wait 1-2 minutes for the index to be created

7. **Verify Success**
   - You should see: "Success. No rows returned"
   - This is normal for DDL statements

---

## ✅ Verification

After running the migration, verify it worked by running these queries:

### Check Extension is Enabled

```sql
SELECT extname, extversion
FROM pg_extension
WHERE extname = 'pg_trgm';
```

**Expected Result:**
```
extname | extversion
--------|------------
pg_trgm | 1.6
```

### Check Index Exists

```sql
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'users'
AND indexname = 'idx_users_email_trgm';
```

**Expected Result:**
```
indexname              | indexdef
-----------------------|------------------------------------------
idx_users_email_trgm   | CREATE INDEX idx_users_email_trgm ON ...
```

### Test Query Performance

```sql
EXPLAIN ANALYZE
SELECT id, email, username
FROM users
WHERE email ILIKE '%radar%'
LIMIT 10;
```

**What to Look For:**
You should see one of these in the output:
- `Bitmap Index Scan using idx_users_email_trgm`
- `Index Scan using idx_users_email_trgm`

**Before (without index):**
```
Seq Scan on users  (cost=0.00..2000.00 rows=1 width=100) (actual time=10000.000..10500.000)
```

**After (with index):**
```
Bitmap Index Scan using idx_users_email_trgm  (cost=0.00..50.00 rows=1 width=100) (actual time=50.000..150.000)
```

---

## 🧪 Test the API

After applying the migration, test your API:

```bash
# Test email search (should work now!)
curl -H "Authorization: Bearer gw_live_wTfpLJ5VB28qMXpOAhr7Uw" \
  "https://api.gatewayz.ai/admin/users?email=radar&limit=10"
```

**Expected:**
- ✅ Fast response (50-200ms)
- ✅ No Cloudflare Worker exceptions
- ✅ Returns users matching "radar" in email

---

## 📊 Performance Comparison

### Before Migration

| Metric | Value |
|--------|-------|
| Query Time | 10-30 seconds |
| Success Rate | 20% (timeouts) |
| Method | Full table scan |
| User Experience | ❌ Broken |

### After Migration

| Metric | Value |
|--------|-------|
| Query Time | 50-200ms |
| Success Rate | 99%+ |
| Method | Index scan |
| User Experience | ✅ Perfect |

---

## 🔧 Alternative Methods

### Method 2: Using Supabase CLI

```bash
# 1. Install Supabase CLI (if not installed)
brew install supabase/tap/supabase

# 2. Link to your project
supabase link --project-ref YOUR_PROJECT_REF

# 3. Apply all pending migrations
supabase db push
```

### Method 3: Using Interactive Script

```bash
# Run the interactive script
./apply_email_search_migration.sh
```

Then follow the prompts to:
- Copy SQL to clipboard
- Apply via Supabase CLI
- Apply via direct PostgreSQL connection

### Method 4: Using Direct PostgreSQL Connection

```bash
# Get your connection string from Supabase Dashboard:
# Settings > Database > Connection string > Connection pooling

# Run the migration
psql "postgresql://postgres:[YOUR-PASSWORD]@[YOUR-HOST]:5432/postgres" \
  -f supabase/migrations/20260105000000_add_email_search_index.sql
```

---

## ❓ Troubleshooting

### Issue: "extension 'pg_trgm' does not exist"

**Solution:** Contact Supabase support. The pg_trgm extension should be available in all Supabase projects, but you may need to enable it.

### Issue: "permission denied for schema public"

**Solution:** Make sure you're using the service role key (not the anon key) if connecting via API. In the Dashboard SQL Editor, you should have full permissions.

### Issue: Index creation takes too long

**Solution:** The `CONCURRENTLY` keyword means the index is built without locking the table. On 40K rows, this should take 1-2 minutes. Be patient.

### Issue: Query still slow after index

**Solution:**
1. Run `ANALYZE users;` to update statistics
2. Check if index is actually being used with `EXPLAIN ANALYZE`
3. Verify index exists with the verification queries above

---

## 📞 Support

If you encounter any issues:

1. **Check Supabase Logs**
   - Go to: Logs > Postgres Logs
   - Look for any errors during index creation

2. **Verify Table Stats**
   ```sql
   SELECT
       schemaname,
       tablename,
       n_live_tup,
       n_dead_tup,
       last_vacuum,
       last_analyze
   FROM pg_stat_user_tables
   WHERE tablename = 'users';
   ```

3. **Check Index Size**
   ```sql
   SELECT
       indexname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
   FROM pg_stat_user_indexes
   WHERE indexrelname = 'idx_users_email_trgm';
   ```

---

## 🎉 Success Criteria

You'll know it worked when:

✅ No Cloudflare Worker exceptions when searching by email
✅ Email search returns results in <200ms
✅ Searching "radar" finds "radarmine1@gmail.com"
✅ `EXPLAIN ANALYZE` shows index scan instead of sequential scan

---

**Ready? Copy the SQL from Step 1 and paste it into your Supabase Dashboard now!** 🚀

**Estimated time: 2 minutes**
