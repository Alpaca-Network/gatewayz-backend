#!/bin/bash
set -euo pipefail

# Superpowers Sync Script
# Synchronizes .claude folder from the obra/superpowers repository

echo "=================================================="
echo "Superpowers .claude Folder Sync"
echo "=================================================="
echo ""

# Save original directory to ensure we can return to it
ORIGINAL_DIR="$(pwd)"

# Configuration
REPO_URL="https://github.com/obra/superpowers"
TMP_DIR="${ORIGINAL_DIR}/tmp"
REPO_DIR="${TMP_DIR}/superpowers"
TARGET_CLAUDE_DIR="${ORIGINAL_DIR}/.claude"

# Superpowers folders to sync (these become .claude subdirectories)
SYNC_FOLDERS=("commands" "hooks" "skills" "agents" "lib")

# Step 1: Ensure tmp directory exists
echo "Step 1: Ensuring tmp directory exists..."
if [ ! -d "$TMP_DIR" ]; then
    echo "  ✓ Creating tmp directory: $TMP_DIR"
    mkdir -p "$TMP_DIR"
else
    echo "  ✓ tmp directory already exists"
fi
echo ""

# Step 2: Clone or update superpowers repository
echo "Step 2: Cloning/updating superpowers repository..."
if [ -d "$REPO_DIR" ]; then
    echo "  ✓ Repository already exists, updating with git pull..."

    # Check if it's a valid git repository
    if [ -d "$REPO_DIR/.git" ]; then
        # Fetch and pull latest changes (from original directory)
        (cd "$REPO_DIR" && git fetch origin --quiet && git pull origin main --quiet) || {
            echo "  ✗ ERROR: Failed to update repository"
            exit 1
        }
        echo "  ✓ Repository updated successfully"
    else
        echo "  ✗ ERROR: Directory exists but is not a git repository"
        echo "  Removing corrupted directory and re-cloning..."
        rm -rf "$REPO_DIR"
        git clone --quiet "$REPO_URL" "$REPO_DIR" || {
            echo "  ✗ ERROR: Failed to clone repository"
            exit 1
        }
        echo "  ✓ Repository cloned successfully"
    fi
else
    echo "  ✓ Cloning repository: $REPO_URL"
    git clone --quiet "$REPO_URL" "$REPO_DIR" || {
        echo "  ✗ ERROR: Failed to clone repository"
        exit 1
    }
    echo "  ✓ Repository cloned successfully"
fi
echo ""

# Step 3: Verify superpowers folders exist
echo "Step 3: Verifying superpowers folders..."
missing_folders=0
for folder in "${SYNC_FOLDERS[@]}"; do
    if [ ! -d "$REPO_DIR/$folder" ]; then
        echo "  ✗ WARNING: $folder directory not found in superpowers repository"
        missing_folders=$((missing_folders + 1))
    else
        echo "  ✓ Found $folder directory"
    fi
done

if [ $missing_folders -eq ${#SYNC_FOLDERS[@]} ]; then
    echo "  ✗ ERROR: None of the expected folders found in superpowers repository"
    exit 1
fi
echo ""

# Step 4: Create target .claude directory if needed
echo "Step 4: Ensuring target .claude directory exists..."
if [ ! -d "$TARGET_CLAUDE_DIR" ]; then
    echo "  ✓ Creating target .claude directory: $TARGET_CLAUDE_DIR"
    mkdir -p "$TARGET_CLAUDE_DIR"
else
    echo "  ✓ Target .claude directory already exists"
fi
echo ""

# Step 5: Sync folders using rsync
echo "Step 5: Synchronizing superpowers folders to .claude..."
echo ""

# Check if rsync is available
if ! command -v rsync &> /dev/null; then
    echo "  ✗ ERROR: rsync command not found"
    echo "  Please install rsync: apt-get install rsync (Debian/Ubuntu) or yum install rsync (RHEL/CentOS)"
    exit 1
fi

# Sync each folder individually
for folder in "${SYNC_FOLDERS[@]}"; do
    SOURCE_DIR="${REPO_DIR}/${folder}"
    TARGET_DIR="${TARGET_CLAUDE_DIR}/${folder}"

    if [ ! -d "$SOURCE_DIR" ]; then
        echo "  ⊘ Skipping $folder (not found in source)"
        continue
    fi

    echo "  → Syncing $folder..."
    echo "    Source: $SOURCE_DIR/"
    echo "    Target: $TARGET_DIR/"

    # Perform sync with rsync
    # Options:
    #   -a : archive mode (preserves permissions, timestamps, etc.)
    #   -h : human-readable
    #   -h : human-readable
    #   --delete : delete files in target that don't exist in source
    #   --exclude : exclude certain patterns
    rsync -ah \
        --delete \
        --exclude='.DS_Store' \
        --exclude='*.swp' \
        --exclude='*.tmp' \
        --exclude='.git' \
        "${SOURCE_DIR}/" "${TARGET_DIR}/" || {
        echo "    ✗ ERROR: rsync failed for $folder"
        exit 1
    }

    echo "    ✓ Synced $folder"
    echo ""
done

echo "  ✓ All folders synchronized successfully"
echo ""

# Step 6: Summary
echo "=================================================="
echo "Sync Complete!"
echo "=================================================="
echo ""
echo "Files synchronized from superpowers/.claude to ./.claude"
echo "Permissions, timestamps, and directory structure preserved"
echo "Existing files overwritten with versions from superpowers"
echo ""

# Step 7: Display what was synced
echo "Files in .claude directory:"
ls -lah "$TARGET_CLAUDE_DIR" | tail -n +4 || true
echo ""

# TODO reminder for GitHub Actions CI
echo "=================================================="
echo "TODO: GitHub Actions CI Integration"
echo "=================================================="
echo ""
echo "REMINDER: Add merge conflict detection to CI pipeline"
echo ""
echo "Required actions:"
echo "1. Update .github/workflows/ci.yml to include a job that:"
echo "   - Runs after the .claude sync step"
echo "   - Checks for git merge conflicts using 'git diff --check'"
echo "   - Fails the pipeline if conflicts are detected"
echo ""
echo "2. Example job configuration:"
echo ""
cat << 'EOF'
  check-merge-conflicts:
    name: Check for Merge Conflicts
    runs-on: ubuntu-latest
    needs: [sync-superpowers]  # Run after sync step
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Check for merge conflicts
        run: |
          if git diff --check; then
            echo "✓ No merge conflicts detected"
          else
            echo "✗ Merge conflicts detected!"
            echo "Please resolve conflicts before merging"
            exit 1
          fi
EOF
echo ""
echo "3. Ensure this check runs after .claude sync and fails CI if conflicts exist"
echo ""
echo "=================================================="
echo ""

exit 0
