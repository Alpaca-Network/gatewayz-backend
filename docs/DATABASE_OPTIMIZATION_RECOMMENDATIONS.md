# Models Table Performance Optimization

## TL;DR: **DO NOT SPLIT THE TABLE**

Splitting your `models` table into "unique" and "duplicate" tables would **hurt performance**, not improve it. Instead, use proper indexing strategies.

## Why Table Splitting Is Bad

### 1. Query Complexity
```sql
-- ❌ BAD: After splitting (slow UNION)
SELECT * FROM unique_models WHERE is_active = true
UNION ALL
SELECT * FROM duplicate_models WHERE is_active = true
ORDER BY model_name;

-- ✅ GOOD: Single table (fast index scan)
SELECT * FROM models WHERE is_active = true ORDER BY model_name;
```

### 2. Index Overhead
- Postgres can't optimize across multiple tables efficiently
- Each table needs its own set of indexes (2x storage)
- Query planner can't use statistics across tables

### 3. Application Complexity
- Need logic to determine which table to query
- JOIN complexity increases
- Data consistency issues

## Recommended Optimizations

### Option 1: Index Optimization (Apply Immediately)

Run the migration I created:
```bash
supabase migration up 20251220070000_optimize_models_indexes
```

This adds:
- **Partial indexes** for active models (smaller, faster)
- **Covering indexes** to avoid table lookups
- **Trigram indexes** for fast text search
- **Full-text search** for description searching

**Expected improvements:**
- 50-80% faster on filtered queries (is_active = true)
- 3-5x faster on search queries (LIKE/ILIKE)
- 2-3x faster on sorted results

### Option 2: Canonical Model Normalization (If Needed)

**Only use this if you need to:**
- Track the same model across multiple providers (e.g., GPT-4 on OpenRouter vs Portkey)
- Compare pricing across providers for the same model
- Show users "GPT-4 is available from 5 providers"

See `docs/CANONICAL_MODELS_SCHEMA.md` for the schema design.

### Option 3: Horizontal Partitioning (For Massive Scale)

**Only needed if you have:**
- 10M+ rows
- Queries timeout even with proper indexes
- Most queries filter by provider_id

```sql
-- Partition by provider_id
CREATE TABLE models (
  ...
) PARTITION BY HASH (provider_id);

CREATE TABLE models_p0 PARTITION OF models FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE models_p1 PARTITION OF models FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE models_p2 PARTITION OF models FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE models_p3 PARTITION OF models FOR VALUES WITH (MODULUS 4, REMAINDER 3);
```

## Performance Analysis Checklist

Before making any schema changes, run this analysis:

```bash
# 1. Check table size
psql -f scripts/analyze_models_performance.sql

# 2. Identify slow queries
# Enable pg_stat_statements in Supabase dashboard
# Then check query stats

# 3. Test with EXPLAIN ANALYZE
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM models WHERE is_active = true LIMIT 100;
```

### What to look for:
- **Sequential Scans** → Need an index
- **High execution time** → Need better indexes or query optimization
- **Many buffer reads** → Consider covering indexes
- **Bitmap Heap Scan** → Partial index might help

## Common Performance Issues & Solutions

### Issue: "Searching models is slow"
**Solution:** Use trigram indexes
```sql
CREATE INDEX idx_models_model_id_trgm ON models USING gin (model_id gin_trgm_ops);
```

### Issue: "Listing active models is slow"
**Solution:** Partial index
```sql
CREATE INDEX idx_models_active ON models (model_name) WHERE is_active = true;
```

### Issue: "Sorting by price is slow"
**Solution:** Composite index
```sql
CREATE INDEX idx_models_price ON models (pricing_prompt, pricing_completion)
WHERE is_active = true AND pricing_prompt IS NOT NULL;
```

### Issue: "Joins with providers table are slow"
**Solution:** Ensure FK index exists
```sql
CREATE INDEX idx_models_provider_id ON models (provider_id);
```

## Query Optimization Tips

### 1. Use Partial Indexes for Common Filters
```python
# Most queries filter for active models
# So create indexes on commonly queried fields with WHERE is_active = true
query = supabase.table("models").select("*").eq("is_active", True)
```

### 2. Use Covering Indexes to Avoid Table Lookups
```python
# If you always select the same fields, include them in the index
# This allows "index-only scans" which are 2-3x faster
query = supabase.table("models").select(
    "provider_id, model_id, model_name, pricing_prompt, pricing_completion"
)
```

### 3. Use Full-Text Search for Text Queries
```python
# Instead of ILIKE (slow)
query = supabase.table("models").select("*").ilike("description", f"%{search}%")

# Use full-text search (fast)
query = supabase.rpc("search_models", {"search_term": search})
```

### 4. Batch Upserts Properly
```python
# Your current bulk_upsert_models with batch_size=500 is good
# Don't increase batch_size too much (500-1000 is optimal)
bulk_upsert_models(models_data, batch_size=500)
```

## Monitoring Query Performance

### Enable Query Logging
```sql
-- In Supabase SQL editor
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Check slow queries
SELECT
  substring(query, 1, 100),
  calls,
  mean_exec_time,
  total_exec_time
FROM pg_stat_statements
WHERE query LIKE '%models%'
ORDER BY mean_exec_time DESC
LIMIT 10;
```

### Check Index Usage
```sql
-- Find unused indexes (candidates for removal)
SELECT
  schemaname,
  tablename,
  indexname,
  idx_scan,
  pg_size_pretty(pg_relation_size(indexrelid)) as size
FROM pg_stat_user_indexes
WHERE tablename = 'models'
  AND idx_scan = 0
ORDER BY pg_relation_size(indexrelid) DESC;
```

## When You Actually Need Table Splitting

The ONLY scenarios where splitting makes sense:

1. **Hot/Cold Data Partitioning**
   - Recent models (queried often) → hot table
   - Old/archived models → cold table
   - Use Postgres partitioning, not manual splitting

2. **Read/Write Separation**
   - Frequently updated models → write table
   - Stable catalog → read table
   - Use database replication instead

3. **Microservices Architecture**
   - Different services own different model subsets
   - Split at the service boundary, not within one database

## Summary

| Approach | Performance | Complexity | Recommended |
|----------|-------------|------------|-------------|
| **Split table** | ❌ Slower | ❌ High | ❌ No |
| **Add indexes** | ✅ 2-5x faster | ✅ Low | ✅ Yes |
| **Canonical normalization** | ✅ Same/Better | ⚠️ Medium | ⚠️ If needed |
| **Partitioning** | ✅ 2-3x faster | ⚠️ Medium | ⚠️ Only at scale (10M+ rows) |

## Next Steps

1. ✅ Run `scripts/analyze_models_performance.sql` to measure current performance
2. ✅ Apply `migrations/20251220070000_optimize_models_indexes.sql`
3. ✅ Monitor index usage for 1 week
4. ✅ Remove unused indexes
5. ⚠️ Only consider partitioning if you have 10M+ rows

## Questions?

- How many rows do you have? (Check with `SELECT count(*) FROM models`)
- What queries are slow? (Run the analysis script)
- What's your current response time? (Target: <100ms for most queries)
