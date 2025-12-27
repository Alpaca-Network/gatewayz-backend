#!/usr/bin/env python3
"""Quick test of model sync"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import logging
logging.basicConfig(level=logging.INFO)

from src.services.models import fetch_models_from_openrouter
from src.config import Config

print(f"OpenRouter API Key configured: {bool(Config.OPENROUTER_API_KEY)}")
print(f"API Key (first 20 chars): {Config.OPENROUTER_API_KEY[:20] if Config.OPENROUTER_API_KEY else 'None'}")

print("\nFetching models from OpenRouter...")
models = fetch_models_from_openrouter()

if models:
    print(f"\n✅ Success! Fetched {len(models)} models")
    print(f"\nFirst 3 models:")
    for model in models[:3]:
        print(f"  - {model.get('id')} ({model.get('name')})")
else:
    print("\n❌ Failed to fetch models")
