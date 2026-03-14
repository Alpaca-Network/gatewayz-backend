
import asyncio
import os
import sys

# Add src to path
sys.path.append(os.path.abspath('.'))

from src.db.models_catalog_db import get_models_stats

async def main():
    try:
        stats = get_models_stats()
        print(f"Total models in DB: {stats.get('total_models', 0)}")
        print(f"Active models in DB: {stats.get('active_models', 0)}")
    except Exception as e:
        print(f"Error checking stats: {e}")

if __name__ == "__main__":
    asyncio.run(main())
