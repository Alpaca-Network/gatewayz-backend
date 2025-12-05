#!/usr/bin/env bash
set -euo pipefail

# Sync .claude folder from superpowers repository
# This script clones or updates the superpowers repo and syncs the .claude folder

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR="$PROJECT_ROOT/tmp"
SUPERPOWERS_DIR="$TMP_DIR/superpowers"
SUPERPOWERS_REPO="https://github.com/obra/superpowers"

echo "=== Superpowers .claude Sync ==="
echo "Project root: $PROJECT_ROOT"
echo "Temp directory: $TMP_DIR"
echo ""

# Create tmp directory if it doesn't exist
if [ ! -d "$TMP_DIR" ]; then
    echo "Creating tmp directory..."
    mkdir -p "$TMP_DIR"
fi

# Clone or update superpowers repository
if [ -d "$SUPERPOWERS_DIR" ]; then
    echo "Updating existing superpowers repository..."
    cd "$SUPERPOWERS_DIR"
    if ! git pull origin main; then
        echo "Error: Failed to update superpowers repository" >&2
        exit 1
    fi
else
    echo "Cloning superpowers repository..."
    if ! git clone "$SUPERPOWERS_REPO" "$SUPERPOWERS_DIR"; then
        echo "Error: Failed to clone superpowers repository" >&2
        exit 1
    fi
fi

# Create target .claude directory if it doesn't exist
if [ ! -d "$PROJECT_ROOT/.claude" ]; then
    echo "Creating .claude directory in project root..."
    mkdir -p "$PROJECT_ROOT/.claude"
fi

# Sync .claude folder using rsync
echo "Syncing .claude folder from superpowers..."
if ! rsync -av --delete \
    --exclude=".git" \
    --exclude="settings.local.json" \
    "$SUPERPOWERS_DIR/.claude/" "$PROJECT_ROOT/.claude/"; then
    echo "Error: Failed to sync .claude folder" >&2
    exit 1
fi

echo ""
echo "✅ Sync completed successfully!"
echo ""
echo "=== IMPORTANT TODO ==="
echo "Add the following to your GitHub Actions CI pipeline:"
echo ""
echo "  1. Add a merge conflict check after the .claude sync step"
echo "  2. Fail the pipeline if conflicts are detected"
echo "  3. Example check:"
echo "     - name: Check for merge conflicts"
echo "       run: |"
echo "         if git ls-files -u | grep -q '^'; then"
echo "           echo 'Error: Merge conflicts detected after .claude sync'"
echo "           git ls-files -u"
echo "           exit 1"
echo "         fi"
echo ""
echo "======================="
