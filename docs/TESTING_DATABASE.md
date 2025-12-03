# Supabase Test Instance Setup Guide

This guide explains how to set up a Supabase test instance with mock data for development and testing.

## Quick Start

### Option 1: Local Supabase (Recommended for Development)

```bash
# 1. Start local Supabase
./scripts/database/setup_test_instance.sh local

# 2. Seed with mock data (automatic with db reset)
# Or run manually:
python scripts/database/seed_test_data.py
```

### Option 2: Cloud Supabase

```bash
# 1. Create project at https://supabase.com/dashboard
# 2. Link and setup
./scripts/database/setup_test_instance.sh cloud

# 3. Seed with mock data
python scripts/database/seed_test_data.py --url YOUR_URL --key YOUR_SERVICE_KEY
```

## Prerequisites

### For Local Development

1. **Docker** - Required for local Supabase
   ```bash
   # Install Docker Desktop or Docker Engine
   # Verify: docker --version
   ```

2. **Supabase CLI**
   ```bash
   npm install -g supabase
   # Or: brew install supabase/tap/supabase
   ```

3. **Python 3.10+** with dependencies
   ```bash
   pip install supabase
   ```

### For Cloud Instance

1. Supabase account at https://supabase.com
2. Supabase CLI (for migrations)
3. Python 3.10+ with `supabase` package

## Setup Scripts

### `setup_test_instance.sh`

Main setup script that handles:
- Starting local Supabase
- Running migrations
- Generating `.env.test` file
- Linking cloud projects

```bash
# Local setup (default)
./scripts/database/setup_test_instance.sh local

# Cloud setup
./scripts/database/setup_test_instance.sh cloud
```

### `seed_test_data.py`

Python script that populates the database with realistic mock data:

```bash
# Basic usage (uses environment variables)
python scripts/database/seed_test_data.py

# Custom connection
python scripts/database/seed_test_data.py \
  --url http://localhost:54321 \
  --key YOUR_SERVICE_KEY

# Clear existing data first
python scripts/database/seed_test_data.py --clear

# Custom amounts
python scripts/database/seed_test_data.py \
  --users 50 \
  --api-keys 3 \
  --activities 500
```

### `seed.sql`

SQL seed file that runs automatically with `supabase db reset`:
- Creates providers and plans
- Creates sample users with various states
- Creates API keys
- Creates payments and transactions
- Creates chat sessions and messages
- Creates coupons

## Test Data Overview

### Users Created

| Username | Email | Role | Status | Credits |
|----------|-------|------|--------|---------|
| admin_user | admin@test.example.com | admin | active | 10,000 |
| dev_alice | alice@test.example.com | developer | active | 500 |
| dev_bob | bob@test.example.com | developer | active | 250 |
| user_charlie | charlie@test.example.com | user | active | 150 |
| user_diana | diana@test.example.com | user | active | 75 |
| trial_frank | frank@test.example.com | user | trial | 25 |
| user_lowbal | lowbal@test.example.com | user | active | 0.50 |
| user_cancelled | cancelled@test.example.com | user | cancelled | 0 |

### Sample API Keys

| User | API Key | Environment |
|------|---------|-------------|
| admin_user | `gw_live_admin_key_00000001` | live |
| dev_alice | `gw_live_alice_key_00000001` | live |
| dev_alice | `gw_test_alice_key_00000002` | test |
| dev_bob | `gw_live_bob_key_000000001` | live |
| user_charlie | `gw_live_charlie_key_0001` | live |
| trial_frank | `gw_test_frank_key_00001` | test |

### Sample Coupons

| Code | Value | Type | Status |
|------|-------|------|--------|
| WELCOME10 | $10 | promotional | active |
| TESTCODE50 | $50 | promotional | active |
| DEVBONUS25 | $25 | promotional | active |
| PARTNER100 | $100 | partnership | active |
| EXPIRED5 | $5 | promotional | expired |

## Using the Test Instance

### Environment Configuration

After setup, use the generated `.env.test` file:

```bash
# Load test environment
export $(cat .env.test | xargs)

# Start the API server
python src/main.py
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with test database
TESTING=true pytest tests/integration/ -v

# Run specific test
pytest tests/integration/test_chat.py -v
```

### Accessing Supabase Studio

For local instances:
- **Studio URL**: http://localhost:54323
- **API URL**: http://localhost:54321
- **Database**: postgresql://postgres:postgres@localhost:54322/postgres

### Making API Requests

```bash
# Using test API key
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer gw_live_alice_key_00000001" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Database Reset

To completely reset the test database:

```bash
# Local: Reset and re-seed
supabase db reset

# This will:
# 1. Drop all tables
# 2. Run all migrations
# 3. Run seed.sql
```

## Customizing Test Data

### Adding More Users

Edit `seed.sql` or use the Python script:

```python
from scripts.database.seed_test_data import TestDataSeeder

seeder = TestDataSeeder(url, key)
seeder.seed_users(count=100)  # Create 100 users
```

### Creating Custom Scenarios

```python
# Create a user with specific conditions
seeder.client.table("users").insert({
    "username": "test_scenario_user",
    "email": "scenario@test.com",
    "credits": 0.01,  # Almost zero balance
    "subscription_status": "trial",
    "trial_expires_at": "2024-01-01T00:00:00Z"  # Expired trial
}).execute()
```

## Troubleshooting

### Common Issues

1. **Docker not running**
   ```bash
   # Start Docker
   # macOS: open -a Docker
   # Linux: sudo systemctl start docker
   ```

2. **Port conflicts**
   ```bash
   # Check what's using the ports
   lsof -i :54321
   lsof -i :54322

   # Stop conflicting services or change ports in supabase/config.toml
   ```

3. **Migration failures**
   ```bash
   # Check migration status
   supabase migration list

   # Repair migrations
   supabase db reset --debug
   ```

4. **Seed script errors**
   ```bash
   # Check Supabase is running
   supabase status

   # Verify connection
   python -c "from supabase import create_client; print('OK')"
   ```

### Logs

```bash
# View Supabase logs
supabase logs

# View specific service logs
supabase logs --service postgres
supabase logs --service rest
```

## CI/CD Integration

For GitHub Actions:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      # Use Supabase test instance
    steps:
      - uses: actions/checkout@v4

      - name: Setup Supabase
        run: |
          npm install -g supabase
          supabase start

      - name: Run migrations
        run: supabase db reset

      - name: Run tests
        env:
          SUPABASE_URL: http://localhost:54321
          SUPABASE_KEY: ${{ secrets.SUPABASE_ANON_KEY }}
        run: pytest tests/ -v
```

## Security Notes

- Test API keys are **not secure** and should never be used in production
- The `.env.test` file contains test-only secrets
- Never commit real API keys or secrets to the repository
- The seed data is designed for testing only

## Files Reference

| File | Purpose |
|------|---------|
| `scripts/database/setup_test_instance.sh` | Main setup script |
| `scripts/database/seed_test_data.py` | Python seeding script |
| `supabase/seed.sql` | SQL seed file |
| `supabase/config.toml` | Supabase configuration |
| `supabase/migrations/` | Database migrations |
| `.env.test` | Test environment variables (generated) |
