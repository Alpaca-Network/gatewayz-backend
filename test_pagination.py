#!/usr/bin/env python3
"""
Test script to verify pagination is working correctly for /models endpoint
"""
import requests
import json

BASE_URL = "https://api.gatewayz.ai"

def test_pagination():
    """Test pagination with different parameters"""

    print("=" * 80)
    print("Testing /models endpoint pagination")
    print("=" * 80)

    # Test 1: Get first page with default limit (100)
    print("\n1. First page (default limit=100, offset=0):")
    response = requests.get(f"{BASE_URL}/models?gateway=all")
    data = response.json()
    print(f"   Total models available: {data['total']}")
    print(f"   Returned in this page: {data['returned']}")
    print(f"   Offset: {data['offset']}")
    print(f"   Limit: {data['limit']}")
    print(f"   Has more pages: {data['has_more']}")
    print(f"   Next offset: {data['next_offset']}")

    # Test 2: Get second page with offset=100
    print("\n2. Second page (limit=100, offset=100):")
    response = requests.get(f"{BASE_URL}/models?gateway=all&limit=100&offset=100")
    data = response.json()
    print(f"   Total models available: {data['total']}")
    print(f"   Returned in this page: {data['returned']}")
    print(f"   Offset: {data['offset']}")
    print(f"   Limit: {data['limit']}")
    print(f"   Has more pages: {data['has_more']}")
    print(f"   Next offset: {data['next_offset']}")

    # Test 3: Get larger page (500 models)
    print("\n3. Larger page (limit=500, offset=0):")
    response = requests.get(f"{BASE_URL}/models?gateway=all&limit=500&offset=0")
    data = response.json()
    print(f"   Total models available: {data['total']}")
    print(f"   Returned in this page: {data['returned']}")
    print(f"   Offset: {data['offset']}")
    print(f"   Limit: {data['limit']}")
    print(f"   Has more pages: {data['has_more']}")
    print(f"   Next offset: {data['next_offset']}")

    # Test 4: Get specific gateway (openrouter)
    print("\n4. OpenRouter only (limit=50, offset=0):")
    response = requests.get(f"{BASE_URL}/models?gateway=openrouter&limit=50&offset=0")
    data = response.json()
    print(f"   Total models available: {data['total']}")
    print(f"   Returned in this page: {data['returned']}")
    print(f"   Offset: {data['offset']}")
    print(f"   Limit: {data['limit']}")
    print(f"   Has more pages: {data['has_more']}")
    print(f"   Next offset: {data['next_offset']}")

    # Test 5: Calculate total pages needed to fetch all models
    print("\n5. Pagination calculation:")
    response = requests.get(f"{BASE_URL}/models?gateway=all&limit=100&offset=0")
    data = response.json()
    total = data['total']
    page_size = 100
    total_pages = (total + page_size - 1) // page_size  # Ceiling division
    print(f"   Total models: {total}")
    print(f"   Page size: {page_size}")
    print(f"   Total pages needed: {total_pages}")
    print(f"   To fetch all models, make {total_pages} requests with:")
    print(f"   - offset=0, limit={page_size}")
    print(f"   - offset={page_size}, limit={page_size}")
    print(f"   - offset={page_size*2}, limit={page_size}")
    print(f"   - ... and so on")

    print("\n" + "=" * 80)
    print("✅ Pagination test complete!")
    print("=" * 80)

if __name__ == "__main__":
    test_pagination()
