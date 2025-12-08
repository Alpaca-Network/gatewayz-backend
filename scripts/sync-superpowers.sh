#!/usr/bin/env bash

# Superpowers .claude Sync Script
#
# This script synchronizes the .claude folder from the obra/superpowers repository
# into the current project's .claude folder. It ensures idempotent execution and
# provides clear logging for each major step.
#
# Usage: ./scripts/sync-superpowers.sh

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR="$PROJECT_ROOT/tmp"
SUPERPOWERS_DIR="$TMP_DIR/superpowers"
SUPERPOWERS_REPO="https://github.com/obra/superpowers"
TARGET_CLAUDE_DIR="$PROJECT_ROOT/.claude"

# Logging functions
log_info() {
    echo "[INFO] $*"
}

log_error() {
    echo "[ERROR] $*" >&2
}

log_success() {
    echo "[SUCCESS] $*"
}

# Main execution
log_info "Starting superpowers .claude folder sync..."
log_info "Project root: $PROJECT_ROOT"
log_info "Target .claude directory: $TARGET_CLAUDE_DIR"
echo ""

# Step 1: Create tmp directory if it doesn't exist
if [ ! -d "$TMP_DIR" ]; then
    log_info "Creating tmp directory at $TMP_DIR..."
    if ! mkdir -p "$TMP_DIR"; then
        log_error "Failed to create tmp directory"
        exit 1
    fi
    log_success "Created tmp directory"
else
    log_info "tmp directory already exists"
fi

# Step 2: Clone or update superpowers repository
if [ -d "$SUPERPOWERS_DIR" ]; then
    log_info "Updating existing superpowers repository..."
    cd "$SUPERPOWERS_DIR"
    if ! git pull; then
        log_error "Failed to update superpowers repository"
        exit 1
    fi
    log_success "Updated superpowers repository"
else
    log_info "Cloning superpowers repository from $SUPERPOWERS_REPO..."
    if ! git clone "$SUPERPOWERS_REPO" "$SUPERPOWERS_DIR"; then
        log_error "Failed to clone superpowers repository"
        exit 1
    fi
    log_success "Cloned superpowers repository"
fi

# Step 3: Create target .claude directory if it doesn't exist
if [ ! -d "$TARGET_CLAUDE_DIR" ]; then
    log_info "Creating .claude directory in project root..."
    if ! mkdir -p "$TARGET_CLAUDE_DIR"; then
        log_error "Failed to create .claude directory"
        exit 1
    fi
    log_success "Created .claude directory"
else
    log_info ".claude directory already exists"
fi

# Step 4: Sync .claude folder using rsync
log_info "Syncing .claude folder from superpowers to current project..."
log_info "Source: $SUPERPOWERS_DIR/.claude/"
log_info "Target: $TARGET_CLAUDE_DIR/"

if ! rsync -av --delete \
    --exclude=".git" \
    --exclude="settings.local.json" \
    "$SUPERPOWERS_DIR/.claude/" \
    "$TARGET_CLAUDE_DIR/"; then
    log_error "Failed to sync .claude folder"
    exit 1
fi

log_success "Synced .claude folder successfully"
echo ""
log_success "All operations completed successfully!"
echo ""

# TODO reminder for CI/CD integration
echo "=================================================================================="
echo "TODO: Add merge conflict check to GitHub Actions CI pipeline"
echo "=================================================================================="
echo ""
echo "The .claude sync step should be followed by a merge conflict detection check"
echo "that fails the pipeline if conflicts are detected."
echo ""
echo "Recommended GitHub Actions workflow step:"
echo ""
echo "  - name: Check for merge conflicts after .claude sync"
echo "    run: |"
echo "      if git ls-files -u | grep -q '.'; then"
echo "        echo 'Error: Merge conflicts detected in .claude folder'"
echo "        echo 'Conflicted files:'"
echo "        git ls-files -u"
echo "        exit 1"
echo "      fi"
echo ""
echo "This check ensures that any merge conflicts introduced by the sync are caught"
echo "before deployment, preventing broken configurations from reaching production."
echo ""
echo "=================================================================================="
