#!/usr/bin/env python3
"""Direct test of sync_provider_models"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from src.services.model_catalog_sync import sync_provider_models

print("="*60)
print("Testing sync_provider_models for OpenRouter")
print("="*60)

result = sync_provider_models("openrouter", dry_run=False)  # Actually sync to DB

print("\n" + "="*60)
print("RESULT:")
print("="*60)
print(f"Success: {result['success']}")
print(f"Models Fetched: {result.get('models_fetched', 0)}")
print(f"Models Transformed: {result.get('models_transformed', 0)}")
print(f"Models Synced: {result.get('models_synced', 0)}")

if not result['success']:
    print(f"Error: {result.get('error')}")
