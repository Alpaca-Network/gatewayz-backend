#!/usr/bin/env bash
set -euo pipefail

# Superpowers .claude Folder Sync Script
# This script clones/updates the obra/superpowers repository and syncs the .claude folder
# into the current project while preserving file permissions and directory structure.

REPO_URL="https://github.com/obra/superpowers"
TMP_DIR="./tmp"
SUPERPOWERS_DIR="${TMP_DIR}/superpowers"
CLAUDE_DIR=".claude"

echo "======================================================================"
echo "Syncing .claude folder from obra/superpowers"
echo "======================================================================"
echo ""

# Step 1: Create tmp directory if it doesn't exist
if [ ! -d "${TMP_DIR}" ]; then
    echo "[INFO] Creating ${TMP_DIR} directory..."
    mkdir -p "${TMP_DIR}"
    echo "[SUCCESS] ${TMP_DIR} directory created"
else
    echo "[INFO] ${TMP_DIR} directory already exists"
fi
echo ""

# Step 2: Clone or update the superpowers repository
if [ -d "${SUPERPOWERS_DIR}" ]; then
    echo "[INFO] Superpowers repository already exists. Updating with git pull..."
    cd "${SUPERPOWERS_DIR}"

    if git pull origin main; then
        echo "[SUCCESS] Repository updated successfully"
    else
        echo "[ERROR] Failed to update repository" >&2
        exit 1
    fi

    cd - > /dev/null
else
    echo "[INFO] Cloning superpowers repository..."

    if git clone "${REPO_URL}" "${SUPERPOWERS_DIR}"; then
        echo "[SUCCESS] Repository cloned successfully"
    else
        echo "[ERROR] Failed to clone repository" >&2
        exit 1
    fi
fi
echo ""

# Step 3: Verify .claude folder exists in superpowers
if [ ! -d "${SUPERPOWERS_DIR}/${CLAUDE_DIR}" ]; then
    echo "[ERROR] .claude folder not found in ${SUPERPOWERS_DIR}" >&2
    exit 1
fi

echo "[INFO] Found .claude folder in superpowers repository"
echo ""

# Step 4: Create .claude folder in current project if it doesn't exist
if [ ! -d "${CLAUDE_DIR}" ]; then
    echo "[INFO] Creating ${CLAUDE_DIR} directory in current project..."
    mkdir -p "${CLAUDE_DIR}"
    echo "[SUCCESS] ${CLAUDE_DIR} directory created"
fi

# Step 5: Sync .claude folder using rsync
echo "[INFO] Syncing .claude folder..."
echo "[INFO] Source: ${SUPERPOWERS_DIR}/${CLAUDE_DIR}/"
echo "[INFO] Destination: ${CLAUDE_DIR}/"
echo ""

if rsync -av --progress \
    --delete \
    --exclude='.git' \
    --exclude='.DS_Store' \
    "${SUPERPOWERS_DIR}/${CLAUDE_DIR}/" \
    "${CLAUDE_DIR}/"; then
    echo ""
    echo "[SUCCESS] .claude folder synced successfully"
else
    echo "[ERROR] rsync failed" >&2
    exit 1
fi
echo ""

# Step 6: Display sync summary
echo "======================================================================"
echo "Sync Complete"
echo "======================================================================"
echo ""
echo "Summary:"
echo "  - Source: ${SUPERPOWERS_DIR}/${CLAUDE_DIR}/"
echo "  - Destination: ${CLAUDE_DIR}/"
echo "  - Files synced with preserved permissions and timestamps"
echo "  - Existing files overwritten with versions from superpowers"
echo ""

# Step 7: Show what was synced
if command -v tree &> /dev/null; then
    echo "Current .claude folder structure:"
    tree -L 2 "${CLAUDE_DIR}"
else
    echo "Current .claude folder contents:"
    ls -lAh "${CLAUDE_DIR}"
fi
echo ""

echo "======================================================================"
echo "Next Steps"
echo "======================================================================"
echo ""
echo "TODO: Add merge conflict check to CI pipeline"
echo "  1. Open .github/workflows/ci.yml (or your CI configuration file)"
echo "  2. Add a step AFTER the .claude sync step that checks for merge conflicts"
echo "  3. The check should fail the pipeline if conflicts are detected"
echo ""
echo "Example GitHub Actions step to add:"
echo ""
echo "  - name: Check for merge conflicts"
echo "    run: |"
echo "      if git diff --check; then"
echo "        echo 'No merge conflicts detected'"
echo "      else"
echo "        echo 'Merge conflicts detected after .claude sync!'"
echo "        exit 1"
echo "      fi"
echo ""
echo "======================================================================"
