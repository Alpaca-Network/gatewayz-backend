#!/usr/bin/env python3
"""
Test script to check /admin/users endpoint behavior and limits
"""
import requests
import json
import sys
import os

def test_admin_users_limit():
    """Test the admin users endpoint with different limits"""
    
    # Configuration - you'll need to set this
    api_base_url = os.getenv('API_BASE_URL', 'https://api.gatewayz.ai')
    admin_api_key = os.getenv('ADMIN_API_KEY')
    
    if not admin_api_key:
        print("ERROR: ADMIN_API_KEY environment variable not set")
        print("Please set it to a valid admin user's API key")
        return False
    
    headers = {
        'Authorization': f'Bearer {admin_api_key}',
        'Content-Type': 'application/json'
    }
    
    print(f"Testing API endpoint: {api_base_url}/admin/users")
    print("=" * 60)
    
    # Test different limit values
    test_cases = [
        {"limit": 100, "description": "Default limit"},
        {"limit": 1000, "description": "Old maximum"},
        {"limit": 1001, "description": "Above old max"},
        {"limit": 5000, "description": "Mid range"},
        {"limit": 10000, "description": "Current max"},
        {"limit": 10001, "description": "Above current max"},
    ]
    
    for test_case in test_cases:
        limit = test_case["limit"]
        description = test_case["description"]
        
        print(f"\nTest: {description} (limit={limit})")
        print("-" * 40)
        
        try:
            # Test with active users filter for consistency
            params = {
                'is_active': 'true',
                'limit': limit,
                'offset': 0
            }
            
            response = requests.get(
                f"{api_base_url}/admin/users",
                headers=headers,
                params=params,
                timeout=30
            )
            
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                total_users = data.get('total_users', 0)
                returned_users = len(data.get('users', []))
                has_more = data.get('has_more', False)
                
                print(f"Total matching users: {total_users}")
                print(f"Users returned: {returned_users}")
                print(f"Has more: {has_more}")
                print(f"Success: ✓")
                
            elif response.status_code == 422:
                error_data = response.json()
                print(f"Validation Error: {error_data}")
                print(f"Success: ✗ (Limit rejected)")
                
            else:
                print(f"Error: {response.text}")
                print(f"Success: ✗ (Unexpected error)")
                
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            print(f"Success: ✗ (Request exception)")
    
    # Test total count without limit to see actual database size
    print(f"\n\nTest: Get user count statistics")
    print("-" * 40)
    
    try:
        response = requests.get(
            f"{api_base_url}/admin/users/stats",
            headers=headers,
            params={'is_active': 'true'},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            stats = data.get('statistics', {})
            total_active = stats.get('active_users', 0)
            total_users = data.get('total_users', 0)
            
            print(f"Total active users: {total_active}")
            print(f"Total users matching filter: {total_users}")
            
        else:
            print(f"Stats endpoint failed: {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"Stats request failed: {e}")
    
    print("\n" + "=" * 60)
    print("Test completed!")
    
    return True

if __name__ == "__main__":
    test_admin_users_limit()
