#!/usr/bin/env python3
"""Quick test to verify provider failover chain logic"""
import os

# Set test environment BEFORE any imports
os.environ['APP_ENV'] = 'testing'
os.environ['TESTING'] = 'true'

from src.services.provider_failover import build_provider_failover_chain, FALLBACK_PROVIDER_PRIORITY

# Test the logic
chain = build_provider_failover_chain("huggingface")

print(f"Priority list: {FALLBACK_PROVIDER_PRIORITY}")
print(f"Priority list length: {len(FALLBACK_PROVIDER_PRIORITY)}")
print(f"\nBuilt chain: {chain}")
print(f"Chain length: {len(chain)}")
print(f"\nTest passed: {len(chain) == len(FALLBACK_PROVIDER_PRIORITY)}")
print(f"First is huggingface: {chain[0] == 'huggingface'}")
print(f"All providers in chain: {all(p in chain for p in ['featherless', 'fireworks', 'together', 'openrouter'])}")
