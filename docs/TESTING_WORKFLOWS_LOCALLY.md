# Testing GitHub Actions Workflows Locally with `act`

Quick guide to testing GitHub Actions workflows on your machine before pushing to GitHub.

## What is `act`?

**`act`** runs GitHub Actions workflows locally using Docker, allowing you to test and debug workflows without pushing to GitHub.

**Benefits:**
- ⚡ Test workflows in seconds (not minutes)
- 🐛 Debug workflow issues locally
- 💰 Save GitHub Actions minutes
- 🔄 Iterate faster on CI/CD changes

---

## Installation

### macOS (Homebrew)
```bash
brew install act
```

### Linux
```bash
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
```

### Verify Installation
```bash
act --version
```

### Prerequisites
- **Docker Desktop** must be installed and running
- Verify: `docker ps` should work without errors

---

## Quick Start

### 1. Navigate to Your Project
```bash
cd /Users/arminrad/Desktop/Alpaca-Network/Gatewayz/gatewayz-backend
```

### 2. List Available Workflows
```bash
# List all workflows and jobs
act -l --container-architecture linux/amd64
```

**Note for M1/M2/M3 Macs:** Always add `--container-architecture linux/amd64` to avoid compatibility warnings.

### 3. Test a Specific Workflow
```bash
# Test the Supabase migrations workflow
act -l -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64
```

---

## Common Commands

### List Jobs
```bash
# List all jobs in all workflows
act -l --container-architecture linux/amd64

# List jobs in a specific workflow
act -l -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64
```

### Dry Run (Show What Would Happen)
```bash
# See what would run without executing
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64 --dryrun

# Shorter: use -n
act -j validate-migrations -n --container-architecture linux/amd64
```

### Run a Specific Job
```bash
# Run the validate-migrations job
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64
```

### Run Full Workflow
```bash
# Simulate a push event
act push -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64

# Simulate workflow_dispatch (manual trigger)
act workflow_dispatch -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64
```

### Verbose Output (Debugging)
```bash
# See detailed logs
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64 -v
```

---

## Working with Secrets

### Method 1: Create a `.secrets` File (Recommended)

```bash
# Create secrets file
cat > .secrets << 'EOF'
SUPABASE_ACCESS_TOKEN=your_access_token
SUPABASE_DB_PASSWORD=your_password
SUPABASE_PROJECT_REF=your_project_ref
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
EOF

# IMPORTANT: Add to .gitignore
echo ".secrets" >> .gitignore

# Use secrets file
act -j validate-migrations --secret-file .secrets --container-architecture linux/amd64
```

### Method 2: Pass Secrets via Command Line
```bash
act -j validate-migrations \
  -s SUPABASE_ACCESS_TOKEN=your_token \
  -s SUPABASE_DB_PASSWORD=your_password \
  --container-architecture linux/amd64
```

### Method 3: Environment Variables
```bash
export SUPABASE_ACCESS_TOKEN=your_token
act -j validate-migrations --container-architecture linux/amd64
```

---

## Testing Specific Workflows

### Test Supabase Migrations Workflow

```bash
# List jobs
act -l -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64

# Test validation (dry run)
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64 -n

# Run validation for real
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64

# Test with secrets (for apply-migrations job)
act -j apply-migrations -W .github/workflows/supabase-migrations.yml --secret-file .secrets --container-architecture linux/amd64
```

### Test CI Workflow

```bash
# List jobs
act -l -W .github/workflows/ci.yml --container-architecture linux/amd64

# Test linting
act -j lint -W .github/workflows/ci.yml --container-architecture linux/amd64

# Test specific test shard
act -j test -W .github/workflows/ci.yml --container-architecture linux/amd64
```

---

## Create Shortcuts

### Option 1: Shell Aliases

Add to `~/.zshrc` or `~/.bashrc`:

```bash
# Shortcuts for testing workflows
alias act-list='act -l --container-architecture linux/amd64'
alias act-supabase='act -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64'
alias act-ci='act -W .github/workflows/ci.yml --container-architecture linux/amd64'

# Reload shell
source ~/.zshrc  # or source ~/.bashrc
```

**Usage:**
```bash
act-list                              # List all jobs
act-supabase -l                       # List Supabase workflow jobs
act-supabase -j validate-migrations   # Test migrations validation
act-ci -j lint                        # Test CI linting
```

### Option 2: Helper Script

Create `test-workflow.sh` in project root:

```bash
#!/bin/bash
# test-workflow.sh - Quick workflow testing

set -e

PROJECT_DIR="/Users/arminrad/Desktop/Alpaca-Network/Gatewayz/gatewayz-backend"
cd "$PROJECT_DIR"

# Check Docker is running
if ! docker ps &> /dev/null; then
    echo "❌ Docker not running. Start Docker Desktop and try again."
    exit 1
fi

# Default to M-series chip architecture
ARCH="--container-architecture linux/amd64"

case "${1:-help}" in
    list)
        act -l $ARCH
        ;;
    supabase)
        act -j validate-migrations -W .github/workflows/supabase-migrations.yml $ARCH "${@:2}"
        ;;
    ci)
        act -j lint -W .github/workflows/ci.yml $ARCH "${@:2}"
        ;;
    help|*)
        echo "Usage: ./test-workflow.sh [command] [options]"
        echo ""
        echo "Commands:"
        echo "  list              List all workflows and jobs"
        echo "  supabase [opts]   Test Supabase migrations workflow"
        echo "  ci [opts]         Test CI workflow"
        echo ""
        echo "Examples:"
        echo "  ./test-workflow.sh list"
        echo "  ./test-workflow.sh supabase -n    # Dry run"
        echo "  ./test-workflow.sh supabase       # Run for real"
        echo "  ./test-workflow.sh ci -v          # Verbose output"
        ;;
esac
```

Make executable and use:

```bash
chmod +x test-workflow.sh

./test-workflow.sh list                  # List all jobs
./test-workflow.sh supabase -n           # Dry run Supabase workflow
./test-workflow.sh supabase              # Test Supabase workflow
./test-workflow.sh ci                    # Test CI workflow
```

---

## Troubleshooting

### Error: Docker Not Running

**Symptom:**
```
Cannot connect to the Docker daemon
```

**Fix:**
```bash
# Start Docker Desktop
open -a Docker

# Wait a few seconds, then try again
act -l --container-architecture linux/amd64
```

### Error: Workflow Not Valid

**Symptom:**
```
Error: workflow is not valid
```

**Cause:** `act` doesn't support all GitHub Actions features (like dynamic `environment` fields).

**Fix:** Test specific workflow files:
```bash
# Instead of testing all workflows
act -l --container-architecture linux/amd64

# Test a specific workflow
act -l -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64
```

### Warning: Apple M-series Chip

**Symptom:**
```
⚠ You are using Apple M-series chip...
```

**Fix:** Always add `--container-architecture linux/amd64`:
```bash
act -l --container-architecture linux/amd64
```

### Error: Secrets Not Found

**Symptom:**
```
unable to interpolate string ... secret not found
```

**Fix:**
```bash
# Create .secrets file with required values
cat .secrets

# Run with secrets file
act -j your-job --secret-file .secrets --container-architecture linux/amd64
```

### Slow Performance

**Problem:** Large Docker images take time to download.

**Fix:** Use smaller images:
```bash
# Use act-optimized image (smaller, faster)
act -j validate-migrations -P ubuntu-latest=catthehacker/ubuntu:act-latest --container-architecture linux/amd64
```

---

## Command Reference

### Basic Syntax
```bash
act [event] [options]
```

### Common Options

| Option | Description | Example |
|--------|-------------|---------|
| `-l` | List workflows and jobs | `act -l` |
| `-n` or `--dryrun` | Show what would run | `act -n` |
| `-j <job>` | Run specific job | `act -j lint` |
| `-W <file>` | Specify workflow file | `act -W .github/workflows/ci.yml` |
| `-s <KEY>=<value>` | Pass secret | `act -s API_KEY=abc123` |
| `--secret-file` | Load secrets from file | `act --secret-file .secrets` |
| `-v` | Verbose output | `act -v` |
| `-P` | Platform/image mapping | `act -P ubuntu-latest=image` |
| `--container-architecture` | Force architecture | `--container-architecture linux/amd64` |

### Common Events

| Event | Description | Usage |
|-------|-------------|-------|
| `push` | Simulate push to branch | `act push` |
| `pull_request` | Simulate PR | `act pull_request` |
| `workflow_dispatch` | Manual trigger | `act workflow_dispatch` |

---

## Best Practices

### 1. Always Use Dry Run First
```bash
# See what will happen before running
act -j validate-migrations -n --container-architecture linux/amd64
```

### 2. Test Specific Workflows
```bash
# Don't test all workflows at once (some may have issues with act)
act -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64
```

### 3. Keep Secrets Safe
```bash
# Never commit .secrets file
echo ".secrets" >> .gitignore

# Use restrictive permissions
chmod 600 .secrets
```

### 4. Use Aliases for M-series Macs
```bash
# Add to ~/.zshrc
alias act='act --container-architecture linux/amd64'
```

### 5. Start with Simple Jobs
```bash
# Test simple validation jobs before complex deployment jobs
act -j validate-migrations    # ✅ Good starting point
act -j deploy-railway          # ❌ May have issues with act
```

---

## Limitations of `act`

`act` doesn't support everything GitHub Actions does:

| Feature | Supported | Notes |
|---------|-----------|-------|
| Basic workflow steps | ✅ Yes | Works well |
| Docker actions | ✅ Yes | Fully supported |
| Secrets | ✅ Yes | Use `--secret-file` |
| Environment variables | ✅ Yes | Fully supported |
| Dynamic `environment` | ❌ Limited | May cause errors |
| GitHub API calls | ⚠️ Partial | May need tokens |
| macOS/Windows runners | ❌ No | Linux only |
| Reusable workflows | ⚠️ Limited | Basic support |

**Bottom Line:** Use `act` for quick validation, but always test on GitHub Actions for final verification.

---

## Quick Reference Card

```bash
# Setup
brew install act

# List workflows
act -l --container-architecture linux/amd64

# Test specific workflow
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64

# Dry run
act -j job-name -n --container-architecture linux/amd64

# With secrets
act -j job-name --secret-file .secrets --container-architecture linux/amd64

# Verbose output
act -j job-name -v --container-architecture linux/amd64
```

---

## Example: Complete Test Workflow

```bash
# 1. Install act
brew install act

# 2. Navigate to project
cd /Users/arminrad/Desktop/Alpaca-Network/Gatewayz/gatewayz-backend

# 3. Check Docker
docker ps

# 4. List available jobs
act -l -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64

# 5. Dry run
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64 -n

# 6. Run for real
act -j validate-migrations -W .github/workflows/supabase-migrations.yml --container-architecture linux/amd64

# 7. Check output
# ✅ Success! Workflow works locally
```

---

## Resources

- **act Documentation**: https://github.com/nektos/act
- **GitHub Actions Docs**: https://docs.github.com/en/actions
- **Docker Desktop**: https://www.docker.com/products/docker-desktop

---

**Last Updated**: 2025-11-26
**Project**: Gatewayz Backend
