#!/bin/bash

# Script to apply email search index migration to Supabase
# This script provides multiple options to apply the migration

set -e  # Exit on error

MIGRATION_FILE="supabase/migrations/20260105000000_add_email_search_index.sql"

echo "=================================================="
echo "Email Search Index Migration Application Script"
echo "=================================================="
echo ""

# Check if migration file exists
if [ ! -f "$MIGRATION_FILE" ]; then
    echo "❌ Error: Migration file not found: $MIGRATION_FILE"
    exit 1
fi

echo "Migration file found: $MIGRATION_FILE"
echo ""

# Display the migration SQL
echo "📄 Migration SQL:"
echo "--------------------------------------------------"
cat "$MIGRATION_FILE"
echo "--------------------------------------------------"
echo ""

# Provide options to the user
echo "Choose how you want to apply this migration:"
echo ""
echo "1. Copy SQL to clipboard (you paste in Supabase Dashboard)"
echo "2. Use Supabase CLI (requires supabase CLI installed)"
echo "3. Use direct PostgreSQL connection (requires psql and DATABASE_URL)"
echo "4. Just show instructions and exit"
echo ""
read -p "Enter your choice (1-4): " choice

case $choice in
    1)
        # Copy to clipboard
        echo ""
        echo "Attempting to copy SQL to clipboard..."

        if command -v pbcopy &> /dev/null; then
            # macOS
            cat "$MIGRATION_FILE" | pbcopy
            echo "✅ SQL copied to clipboard (macOS)"
        elif command -v xclip &> /dev/null; then
            # Linux with xclip
            cat "$MIGRATION_FILE" | xclip -selection clipboard
            echo "✅ SQL copied to clipboard (Linux - xclip)"
        elif command -v xsel &> /dev/null; then
            # Linux with xsel
            cat "$MIGRATION_FILE" | xsel --clipboard
            echo "✅ SQL copied to clipboard (Linux - xsel)"
        else
            echo "⚠️  Clipboard tool not found. Please copy the SQL manually."
        fi

        echo ""
        echo "📋 Next steps:"
        echo "1. Go to: https://app.supabase.com/project/_/sql"
        echo "2. Paste the SQL from your clipboard"
        echo "3. Click 'Run'"
        echo "4. Verify the index was created successfully"
        ;;

    2)
        # Use Supabase CLI
        echo ""

        if ! command -v supabase &> /dev/null; then
            echo "❌ Error: Supabase CLI not found"
            echo ""
            echo "Install it with:"
            echo "  macOS: brew install supabase/tap/supabase"
            echo "  Linux: https://supabase.com/docs/guides/cli"
            exit 1
        fi

        echo "Using Supabase CLI to apply migration..."
        echo ""

        # Check if supabase is linked
        if ! supabase status &> /dev/null; then
            echo "⚠️  Not linked to a Supabase project"
            echo ""
            read -p "Enter your Supabase project ref (from project URL): " project_ref
            echo "Linking to project..."
            supabase link --project-ref "$project_ref"
        fi

        echo "Applying migration..."
        supabase db push

        echo "✅ Migration applied via Supabase CLI"
        ;;

    3)
        # Use direct PostgreSQL connection
        echo ""

        if ! command -v psql &> /dev/null; then
            echo "❌ Error: psql not found"
            echo "Install PostgreSQL client tools first"
            exit 1
        fi

        # Check for DATABASE_URL environment variable
        if [ -z "$DATABASE_URL" ]; then
            echo "DATABASE_URL not set in environment"
            echo ""
            read -p "Enter your Supabase database connection string: " DATABASE_URL
            export DATABASE_URL
        fi

        echo "Applying migration via psql..."
        psql "$DATABASE_URL" -f "$MIGRATION_FILE"

        echo "✅ Migration applied via psql"
        ;;

    4)
        # Just show instructions
        echo ""
        echo "📚 Manual Application Instructions"
        echo "===================================="
        echo ""
        echo "Method 1: Supabase Dashboard (Easiest)"
        echo "--------------------------------------"
        echo "1. Go to: https://app.supabase.com"
        echo "2. Select your project"
        echo "3. Navigate to: SQL Editor"
        echo "4. Create a new query"
        echo "5. Paste the SQL from: $MIGRATION_FILE"
        echo "6. Click 'Run'"
        echo ""
        echo "Method 2: Supabase CLI"
        echo "---------------------"
        echo "1. Install Supabase CLI if not installed:"
        echo "   macOS: brew install supabase/tap/supabase"
        echo "   Other: https://supabase.com/docs/guides/cli"
        echo "2. Link to your project: supabase link"
        echo "3. Apply migration: supabase db push"
        echo ""
        echo "Method 3: Direct PostgreSQL"
        echo "--------------------------"
        echo "1. Get your connection string from Supabase Dashboard"
        echo "2. Run: psql 'your-connection-string' -f $MIGRATION_FILE"
        echo ""
        ;;

    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "=================================================="
echo "After applying the migration, verify it worked:"
echo "=================================================="
echo ""
echo "Run this SQL to verify:"
echo ""
echo "-- Check if extension is enabled"
echo "SELECT extname FROM pg_extension WHERE extname = 'pg_trgm';"
echo ""
echo "-- Check if index exists"
echo "SELECT indexname FROM pg_indexes"
echo "WHERE tablename = 'users' AND indexname = 'idx_users_email_trgm';"
echo ""
echo "-- Test query performance"
echo "EXPLAIN ANALYZE"
echo "SELECT * FROM users WHERE email ILIKE '%radar%' LIMIT 10;"
echo ""
echo "You should see: 'Bitmap Index Scan using idx_users_email_trgm'"
echo ""
