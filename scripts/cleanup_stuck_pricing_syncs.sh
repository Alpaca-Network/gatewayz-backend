#!/bin/bash
# Quick script to manually clean up stuck pricing syncs
# Run this anytime you need to clean up stuck syncs

set -e

echo "🧹 Cleaning up stuck pricing syncs..."
echo ""

python3 -c "
import asyncio
import sys
sys.path.insert(0, '.')

from src.services.pricing_sync_cleanup import cleanup_stuck_syncs

async def main():
    print('Looking for stuck syncs (timeout: 5 minutes)...')
    result = await cleanup_stuck_syncs(timeout_minutes=5)

    print('')
    print(f\"Found: {result['stuck_syncs_found']} stuck syncs\")
    print(f\"Cleaned: {result['syncs_cleaned']} syncs\")

    if result.get('error'):
        print(f\"Error: {result['error']}\")
        return 1

    print('')
    print('✅ Cleanup complete!')
    return 0

sys.exit(asyncio.run(main()))
"

echo ""
echo "Done!"
