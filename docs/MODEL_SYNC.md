# Model Catalog Synchronization System

## Overview

The Model Catalog Synchronization system automatically fetches and updates AI models from provider APIs to the database. This ensures your model catalog is always up-to-date with the latest models from all supported providers.

## Features

- **Dynamic Model Discovery**: Automatically discovers new models as providers add them
- **Provider Management**: Automatically creates provider entries if they don't exist
- **Bulk Operations**: Efficiently syncs thousands of models using bulk upsert
- **Dry Run Mode**: Test sync without writing to database
- **Per-Provider or Bulk Sync**: Sync individual providers or all at once
- **Comprehensive Logging**: Detailed logs of sync operations
- **Error Handling**: Graceful error handling with detailed error reporting

## Architecture

### Components

1. **Fetch Functions** (`src/services/models.py`, `src/services/*_client.py`)
   - Provider-specific functions that fetch models from provider APIs
   - Already normalized to internal schema
   - Examples: `fetch_models_from_openrouter()`, `fetch_models_from_deepinfra()`

2. **Sync Service** (`src/services/model_catalog_sync.py`)
   - Orchestrates fetching and syncing
   - Transforms normalized models to database schema
   - Handles provider creation/updates
   - Performs bulk upserts

3. **Sync Routes** (`src/routes/model_sync.py`)
   - REST API endpoints for triggering syncs
   - Admin-only endpoints for security

4. **CLI Script** (`scripts/sync_models.py`)
   - Command-line tool for manual or scheduled syncs
   - Suitable for cron jobs or systemd timers

### Data Flow

```
Provider API
    ↓
Fetch Function (normalizes model data)
    ↓
Sync Service (transforms to DB schema)
    ↓
Database (bulk upsert)
```

## Supported Providers

The system supports 20+ providers with fetch functions:

- OpenRouter
- DeepInfra
- Featherless
- Fireworks AI
- Together AI
- HuggingFace
- Cerebras
- Google Vertex AI
- XAI (Grok)
- Groq
- Near AI
- Fal.ai
- Chutes
- AIMO
- Anannas
- AiHubMix
- Helicone
- Vercel AI Gateway
- Nebius
- Novita
- Alibaba

## Usage

### API Endpoints

All endpoints are under `/admin/model-sync` and require admin authentication.

#### 1. List Available Providers

```bash
GET /admin/model-sync/providers
```

Returns list of all providers that can be synced.

**Example:**
```bash
curl http://localhost:8000/admin/model-sync/providers
```

**Response:**
```json
{
  "providers": ["openrouter", "deepinfra", "fireworks", ...],
  "count": 21
}
```

#### 2. Sync Single Provider

```bash
POST /admin/model-sync/provider/{provider_slug}?dry_run=false
```

Syncs models from a specific provider.

**Parameters:**
- `provider_slug` (path): Provider identifier (e.g., "openrouter")
- `dry_run` (query): If true, fetches but doesn't write to DB (default: false)

**Examples:**
```bash
# Dry run to test
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter?dry_run=true"

# Actually sync
curl -X POST "http://localhost:8000/admin/model-sync/provider/openrouter"
```

**Response:**
```json
{
  "success": true,
  "message": "Synced 150 models from openrouter. Fetched: 150, Transformed: 148, Skipped: 2",
  "details": {
    "provider": "openrouter",
    "provider_id": 1,
    "models_fetched": 150,
    "models_transformed": 148,
    "models_skipped": 2,
    "models_synced": 148
  }
}
```

#### 3. Sync All Providers

```bash
POST /admin/model-sync/all?dry_run=false
```

Syncs models from all providers (or specified list).

**Parameters:**
- `providers` (query, multiple): Specific providers to sync (optional)
- `dry_run` (query): If true, fetches but doesn't write to DB (default: false)

**Examples:**
```bash
# Sync all providers
curl -X POST "http://localhost:8000/admin/model-sync/all"

# Sync specific providers
curl -X POST "http://localhost:8000/admin/model-sync/all?providers=openrouter&providers=deepinfra"

# Dry run
curl -X POST "http://localhost:8000/admin/model-sync/all?dry_run=true"
```

**Response:**
```json
{
  "success": true,
  "message": "Processed 21 providers. Success: 19, Errors: 2. Total synced: 5432 models",
  "details": {
    "providers_processed": 21,
    "total_models_fetched": 5500,
    "total_models_transformed": 5450,
    "total_models_skipped": 50,
    "total_models_synced": 5432,
    "errors": [
      {"provider": "provider1", "error": "API key not configured"},
      {"provider": "provider2", "error": "Connection timeout"}
    ]
  }
}
```

#### 4. Get Sync Status

```bash
GET /admin/model-sync/status
```

Returns current sync status and database statistics.

**Example:**
```bash
curl http://localhost:8000/admin/model-sync/status
```

**Response:**
```json
{
  "providers": {
    "in_database": 17,
    "with_fetch_functions": 21,
    "fetchable_in_db": 15,
    "fetchable_not_in_db": 6,
    "stats": {
      "total": 17,
      "active": 15,
      "healthy": 12
    }
  },
  "models": {
    "stats": {
      "total": 5432,
      "active": 5200,
      "by_modality": {
        "text->text": 5000,
        "text->image": 200,
        "multimodal": 232
      }
    }
  }
}
```

### CLI Script

The CLI script provides a command-line interface for syncing models.

#### Installation

The script is located at `scripts/sync_models.py` and can be run directly:

```bash
# Make executable (optional)
chmod +x scripts/sync_models.py
```

#### Usage

```bash
# Show help
python scripts/sync_models.py --help

# Sync all providers (dry run)
python scripts/sync_models.py --dry-run

# Actually sync all providers
python scripts/sync_models.py

# Sync specific providers
python scripts/sync_models.py --providers openrouter deepinfra

# Verbose logging
python scripts/sync_models.py --providers openrouter --verbose
```

#### Options

- `--providers PROVIDER [PROVIDER ...]`: Specific providers to sync
- `--dry-run`: Fetch and transform but don't write to database
- `--verbose, -v`: Enable verbose (DEBUG) logging

#### Examples

```bash
# Test sync without database writes
python scripts/sync_models.py --dry-run

# Sync just OpenRouter with detailed logs
python scripts/sync_models.py --providers openrouter --verbose

# Sync multiple providers
python scripts/sync_models.py --providers openrouter deepinfra fireworks

# Full sync of all providers
python scripts/sync_models.py
```

## Scheduling Automatic Syncs

### Using Cron (Linux/Mac)

Add to crontab (`crontab -e`):

```bash
# Sync all providers daily at 2 AM
0 2 * * * cd /path/to/gatewayz-backend && /path/to/python scripts/sync_models.py >> /var/log/model-sync.log 2>&1

# Sync specific high-traffic providers every 6 hours
0 */6 * * * cd /path/to/gatewayz-backend && /path/to/python scripts/sync_models.py --providers openrouter deepinfra >> /var/log/model-sync.log 2>&1
```

### Using Systemd Timer (Linux)

Create service file `/etc/systemd/system/model-sync.service`:

```ini
[Unit]
Description=Sync AI model catalog
After=network.target

[Service]
Type=oneshot
User=your-user
WorkingDirectory=/path/to/gatewayz-backend
ExecStart=/path/to/python scripts/sync_models.py
StandardOutput=journal
StandardError=journal
```

Create timer file `/etc/systemd/system/model-sync.timer`:

```ini
[Unit]
Description=Run model sync daily

[Timer]
OnCalendar=daily
OnCalendar=02:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable model-sync.timer
sudo systemctl start model-sync.timer

# Check status
sudo systemctl status model-sync.timer
```

### Using GitHub Actions

Create `.github/workflows/sync-models.yml`:

```yaml
name: Sync Model Catalog

on:
  schedule:
    # Run daily at 2 AM UTC
    - cron: '0 2 * * *'
  workflow_dispatch:  # Allow manual trigger

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Sync models
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          # Add other provider API keys
        run: python scripts/sync_models.py
```

## How It Works

### 1. Provider Ensures Existence

Before syncing models, the system ensures the provider exists in the database:

```python
# If provider doesn't exist, create it
provider = ensure_provider_exists("openrouter")
```

This automatically creates provider entries with metadata like:
- Name, slug, description
- Base URL, API key environment variable
- Supported capabilities (streaming, function calling, vision, etc.)

### 2. Fetch Models from Provider API

Uses existing fetch functions to get models:

```python
# These functions already normalize to internal schema
models = fetch_models_from_openrouter()
```

### 3. Transform to Database Schema

Converts normalized model structure to database schema:

```python
db_model = {
    "provider_id": 1,
    "model_id": "gpt-4-turbo",
    "model_name": "GPT-4 Turbo",
    "provider_model_id": "gpt-4-turbo",
    "description": "...",
    "context_length": 128000,
    "modality": "text->text",
    "pricing_prompt": Decimal("0.00001"),
    "pricing_completion": Decimal("0.00003"),
    "supports_streaming": True,
    # ... more fields
}
```

### 4. Bulk Upsert to Database

Uses `bulk_upsert_models()` for efficient database operations:

```python
# Upsert = INSERT or UPDATE if exists
# Uses unique constraint: (provider_id, provider_model_id)
synced_models = bulk_upsert_models(db_models)
```

### 5. Result Reporting

Returns comprehensive results:

```python
{
    "success": True,
    "models_fetched": 150,
    "models_transformed": 148,
    "models_skipped": 2,
    "models_synced": 148,
    "errors": []
}
```

## Error Handling

The system handles various error scenarios gracefully:

1. **Missing API Keys**: Skips provider with warning
2. **API Timeouts**: Logs error, continues with other providers
3. **Invalid Model Data**: Skips individual models, logs warnings
4. **Database Errors**: Logs detailed error, returns failure status

All errors are:
- Logged with full context
- Included in sync results
- Non-blocking (one provider failure doesn't stop others)

## Best Practices

1. **Always Test with Dry Run First**
   ```bash
   python scripts/sync_models.py --dry-run
   ```

2. **Sync High-Traffic Providers More Frequently**
   - OpenRouter, DeepInfra: Every 6 hours
   - Others: Daily

3. **Monitor Logs**
   - Check for API errors
   - Monitor skipped models
   - Review transformation errors

4. **Use Verbose Mode for Debugging**
   ```bash
   python scripts/sync_models.py --providers openrouter --verbose
   ```

5. **Schedule During Low-Traffic Hours**
   - Typically 2-4 AM in your timezone

## Troubleshooting

### No Models Fetched

**Cause**: API key not configured or invalid

**Solution**:
```bash
# Check environment variables
echo $OPENROUTER_API_KEY

# Set if missing
export OPENROUTER_API_KEY="your-key-here"
```

### Models Skipped During Transformation

**Cause**: Missing required fields (e.g., model ID)

**Solution**: Check logs for specific errors:
```bash
python scripts/sync_models.py --providers openrouter --verbose 2>&1 | grep "Skipping"
```

### Database Upsert Fails

**Cause**: Database connection issues or constraint violations

**Solution**:
1. Check database connectivity
2. Verify Supabase credentials
3. Check database logs for constraint violations

### Provider Not Found

**Cause**: Provider slug misspelled or no fetch function

**Solution**: List available providers:
```bash
curl http://localhost:8000/admin/model-sync/providers
```

## Monitoring

### Check Sync Status

```bash
curl http://localhost:8000/admin/model-sync/status
```

### View Model Statistics

```bash
curl http://localhost:8000/models/stats
```

### View Provider Statistics

```bash
curl http://localhost:8000/providers/stats
```

## Future Enhancements

Potential improvements:

1. **Incremental Sync**: Only fetch models changed since last sync
2. **Webhook Support**: Trigger sync when providers publish new models
3. **Parallel Fetching**: Fetch from multiple providers concurrently
4. **Smart Scheduling**: Auto-adjust sync frequency based on provider update patterns
5. **Conflict Resolution**: Advanced handling of model updates
6. **Sync History**: Track sync operations over time

## Related Documentation

- [Provider Management API](./providers_management.md)
- [Models Catalog API](./models_catalog_management.md)
- [Database Schema](./database_schema.md)
