# How Model Search Works

## The Basics

1. **Storage**: All models live in Supabase PostgreSQL (`models` table)
2. **Search**: API queries Supabase and returns matching models
3. **Flexible Matching**: Automatically handles "gpt 4" = "gpt-4" = "gpt4"

## Flow

```
User → FastAPI → Supabase Query → Results
```

## Search Process

When you search for **"gpt 4"**:

1. Creates variations: `["gpt 4", "gpt4", "gpt-4", "gpt_4"]`
2. Searches each variation in `model_name`, `model_id`, `description`
3. Returns all matches from Supabase
4. Removes duplicates

## Example

```bash
GET /catalog/models-db/search?q=gpt 4
```

Finds: gpt-4, gpt-4-turbo, gpt-4o, gpt-4-32k, etc.

## Database Schema (Simplified)

```sql
models
  ├── model_id           (e.g., "openai/gpt-4")
  ├── model_name         (e.g., "GPT-4 Turbo")
  ├── provider_id        (links to providers table)
  ├── pricing_prompt
  ├── context_length
  ├── health_status
  └── supports_streaming
```

## Key Files

- **Search Logic**: `src/db/models_catalog_db.py:393` (search_models function)
- **API Endpoint**: `src/routes/models_catalog_management.py:123` (search endpoint)
- **Database Schema**: `supabase/migrations/20251216024941_create_model_catalog_tables.sql`

## That's It!

Models → Supabase → Flexible Search → Results
