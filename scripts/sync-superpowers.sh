#!/bin/bash
set -euo pipefail

# Script to sync .claude folder from superpowers repository
# This syncs Claude Code configuration and best practices from the superpowers repo

echo "=================================================="
echo "Claude Superpowers Sync"
echo "=================================================="
echo ""

# Configuration - use absolute paths to work from any directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_URL="https://github.com/obra/superpowers"
TMP_DIR="$PROJECT_ROOT/tmp"
SUPERPOWERS_DIR="${TMP_DIR}/superpowers"
TARGET_CLAUDE_DIR="$PROJECT_ROOT/.claude"

# Step 1: Clone or update the superpowers repository
echo "[1/3] Cloning/updating superpowers repository..."
if [ ! -d "$TMP_DIR" ]; then
    echo "Creating tmp directory..."
    mkdir -p "$TMP_DIR"
fi

if [ -d "$SUPERPOWERS_DIR" ]; then
    echo "Superpowers repo already exists, updating with git pull..."
    cd "$SUPERPOWERS_DIR"
    # Detect the default branch dynamically
    DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")
    git pull origin "$DEFAULT_BRANCH" || {
        echo "ERROR: Failed to pull latest changes from superpowers"
        exit 1
    }
    cd - > /dev/null
else
    echo "Cloning superpowers repository..."
    git clone "$REPO_URL" "$SUPERPOWERS_DIR" || {
        echo "ERROR: Failed to clone superpowers repository"
        exit 1
    }
fi

echo "✓ Repository is up to date"
echo ""

# Step 2: Sync .claude folder using rsync
echo "[2/3] Syncing .claude folder..."

# Create target .claude directory if it doesn't exist
if [ ! -d "$TARGET_CLAUDE_DIR" ]; then
    echo "Creating .claude directory..."
    mkdir -p "$TARGET_CLAUDE_DIR"
fi

# Check if source .claude directory exists
if [ ! -d "${SUPERPOWERS_DIR}/.claude" ]; then
    echo "WARNING: No .claude directory found in superpowers repo"
    echo "Checking if repository was cloned correctly..."
    ls -la "$SUPERPOWERS_DIR" | head -10
    exit 1
fi

# Sync using rsync (preserves permissions, timestamps, and directory structure)
# Exclude settings.local.json to preserve local configuration
echo "Syncing files from ${SUPERPOWERS_DIR}/.claude/ to ${TARGET_CLAUDE_DIR}/"
rsync -av --delete \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='settings.local.json' \
    "${SUPERPOWERS_DIR}/.claude/" "${TARGET_CLAUDE_DIR}/" || {
    echo "ERROR: Failed to sync .claude folder"
    exit 1
}

echo "✓ .claude folder synced successfully"
echo ""

# Step 3: Completion
echo "[3/3] Sync complete!"
echo ""
echo "=================================================="
echo "Summary"
echo "=================================================="
echo "Source: ${SUPERPOWERS_DIR}/.claude/"
echo "Target: ${TARGET_CLAUDE_DIR}/"
echo ""

# List synced files
echo "Synced files:"
find "$TARGET_CLAUDE_DIR" -type f | sort | sed 's|^|  - |'
echo ""

echo "✓ Claude superpowers sync completed successfully"
echo ""

# TODO: Add a check for merge conflicts into the GitHub Actions CI pipeline
# This check should run after the .claude sync step and fail the pipeline
# if any merge conflicts are detected in the .claude directory.
#
# Suggested implementation:
# 1. Add a CI job that runs after this sync script
# 2. Check for conflict markers (<<<<<<, >>>>>>, ======) in .claude files
# 3. Fail the build if any conflicts are found
# 4. Example check:
#    if grep -r "<<<<<<< HEAD" .claude/; then
#      echo "ERROR: Merge conflicts detected in .claude directory"
#      exit 1
#    fi
