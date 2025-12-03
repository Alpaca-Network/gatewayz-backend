#!/usr/bin/env python3
"""
Diagnostic script to check OpenRouter authentication configuration
"""

import os
import sys

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def diagnose_openrouter_auth():
    """Diagnose OpenRouter authentication issues"""

    print("=" * 80)
    print("DIAGNOSING OPENROUTER AUTHENTICATION")
    print("=" * 80)

    # Check 1: Environment variable
    print("\n[CHECK 1] Environment Variable")
    api_key_raw = os.environ.get("OPENROUTER_API_KEY")
    if api_key_raw:
        print(f"✅ OPENROUTER_API_KEY is set in environment")
        print(f"   Length: {len(api_key_raw)}")
        print(f"   Preview: {api_key_raw[:10]}...{api_key_raw[-5:]}")

        # Check for whitespace issues
        if api_key_raw != api_key_raw.strip():
            print("   ⚠️  WARNING: API key has leading/trailing whitespace!")
            print(f"   Stripped length: {len(api_key_raw.strip())}")
    else:
        print("❌ OPENROUTER_API_KEY is NOT set in environment")
        return False

    # Check 2: Config module
    print("\n[CHECK 2] Config Module")
    try:
        from src.config import Config

        if Config.OPENROUTER_API_KEY:
            print(f"✅ Config.OPENROUTER_API_KEY is set")
            print(f"   Length: {len(Config.OPENROUTER_API_KEY)}")
            print(f"   Preview: {Config.OPENROUTER_API_KEY[:10]}...{Config.OPENROUTER_API_KEY[-5:]}")

            # Check if they match
            if Config.OPENROUTER_API_KEY == api_key_raw:
                print("✅ Matches environment variable")
            elif Config.OPENROUTER_API_KEY == api_key_raw.strip():
                print("⚠️  Matches after stripping whitespace (good - config strips it)")
            else:
                print("❌ Does NOT match environment variable!")
                print(f"   Env length: {len(api_key_raw)}")
                print(f"   Config length: {len(Config.OPENROUTER_API_KEY)}")
        else:
            print("❌ Config.OPENROUTER_API_KEY is None or empty")
            return False
    except Exception as e:
        print(f"❌ Failed to import Config: {e}")
        return False

    # Check 3: OpenRouter client initialization
    print("\n[CHECK 3] OpenRouter Client Initialization")
    try:
        from src.services.openrouter_client import get_openrouter_client

        client = get_openrouter_client()
        print("✅ OpenRouter client initialized successfully")
        print(f"   Client type: {type(client)}")
        print(f"   Base URL: {client.base_url}")

        # Check API key in client
        if hasattr(client, 'api_key'):
            print(f"   API key set: {'Yes' if client.api_key else 'No'}")
            if client.api_key:
                print(f"   API key length: {len(client.api_key)}")
                print(f"   API key preview: {client.api_key[:10]}...{client.api_key[-5:]}")
    except ValueError as e:
        print(f"❌ Client initialization failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Check 4: Test API request
    print("\n[CHECK 4] Test API Request")
    print("Attempting a simple request to OpenRouter...")

    try:
        response = client.chat.completions.create(
            model="openrouter/auto",
            messages=[{"role": "user", "content": "Say 'test' and nothing else"}],
            max_tokens=5
        )
        print("✅ Request successful!")
        print(f"   Response ID: {response.id}")
        print(f"   Model used: {response.model}")
        if response.choices:
            print(f"   Response: {response.choices[0].message.content}")
    except Exception as e:
        print(f"❌ Request failed: {e}")
        print(f"   Error type: {type(e).__name__}")

        # Check if it's an authentication error
        error_str = str(e).lower()
        if 'auth' in error_str or '401' in error_str or '403' in error_str or 'api key' in error_str:
            print("\n   🔍 This appears to be an authentication error.")
            print("   Possible causes:")
            print("   1. API key is invalid or expired")
            print("   2. API key format is incorrect")
            print("   3. API key has been revoked")
            print("   4. OpenRouter account has issues")
            print("\n   Solutions:")
            print("   1. Get a new API key from: https://openrouter.ai/keys")
            print("   2. Ensure the key starts with 'sk-or-v1-'")
            print("   3. Check your OpenRouter account status")
            print("   4. Verify the key has proper permissions")

        return False

    print("\n" + "=" * 80)
    print("✅ ALL CHECKS PASSED - OPENROUTER AUTHENTICATION IS WORKING")
    print("=" * 80)
    return True

if __name__ == "__main__":
    try:
        success = diagnose_openrouter_auth()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nDiagnostic interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error during diagnostics: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
