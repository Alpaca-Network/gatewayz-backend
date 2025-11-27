#!/usr/bin/env python3
"""
Fetch all available models from OneRouter API and display pricing information.
This script helps populate the manual_pricing.json file with current OneRouter models.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from src.config import Config


def fetch_onerouter_models():
    """Fetch all models from OneRouter API"""
    if not Config.ONEROUTER_API_KEY:
        print("❌ ONEROUTER_API_KEY not set in environment")
        print("Please set: export ONEROUTER_API_KEY=your_api_key")
        return None

    headers = {
        "Authorization": f"Bearer {Config.ONEROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        # Try the OpenAI-compatible models endpoint
        print("📡 Fetching models from OneRouter API...")
        response = httpx.get(
            "https://llm.onerouter.pro/v1/models",
            headers=headers,
            timeout=15.0
        )
        response.raise_for_status()

        models_data = response.json()
        models = models_data.get("data", [])

        print(f"✅ Fetched {len(models)} models from OneRouter\n")
        return models

    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP error: {e}")
        print(f"Response: {e.response.text if hasattr(e, 'response') else 'N/A'}")
        return None
    except Exception as e:
        print(f"❌ Error fetching models: {e}")
        return None


def try_display_models_endpoint():
    """Try the alternative display_models endpoint"""
    if not Config.ONEROUTER_API_KEY:
        return None

    headers = {
        "Authorization": f"Bearer {Config.ONEROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        print("\n📡 Trying alternative endpoint: /api/display_models...")
        response = httpx.get(
            "https://app.onerouter.pro/api/display_models",
            headers=headers,
            timeout=15.0
        )
        response.raise_for_status()

        data = response.json()
        print(f"✅ Fetched data from display_models endpoint")
        return data

    except Exception as e:
        print(f"ℹ️  Alternative endpoint failed: {e}")
        return None


def format_pricing_entry(model):
    """Format a model into pricing JSON entry"""
    model_id = model.get("id", "")

    # Extract pricing if available
    pricing = model.get("pricing", {})

    entry = {
        "prompt": str(pricing.get("prompt", "0")),
        "completion": str(pricing.get("completion", "0")),
        "request": "0",
        "image": "0",
    }

    # Add context length if available
    context_length = model.get("context_length") or model.get("context_window")
    if context_length:
        entry["context_length"] = context_length

    return model_id, entry


def main():
    print("=" * 80)
    print("OneRouter Models Fetcher")
    print("=" * 80)

    # Try main endpoint
    models = fetch_onerouter_models()

    if not models:
        # Try alternative endpoint
        alt_data = try_display_models_endpoint()
        if alt_data:
            models = alt_data.get("models", []) or alt_data.get("data", [])

    if not models:
        print("\n❌ Could not fetch models from OneRouter API")
        print("Please check your API key and try again.")
        return

    print("\n" + "=" * 80)
    print("Available Models")
    print("=" * 80)

    # Group models by provider/type
    model_groups = {}
    for model in models:
        model_id = model.get("id", "")

        # Determine provider from model ID
        if "gpt" in model_id.lower():
            provider = "OpenAI"
        elif "claude" in model_id.lower():
            provider = "Anthropic"
        elif "gemini" in model_id.lower():
            provider = "Google"
        elif "llama" in model_id.lower():
            provider = "Meta"
        elif "mistral" in model_id.lower() or "mixtral" in model_id.lower():
            provider = "Mistral"
        elif "deepseek" in model_id.lower():
            provider = "DeepSeek"
        elif "qwen" in model_id.lower():
            provider = "Alibaba"
        else:
            provider = "Other"

        if provider not in model_groups:
            model_groups[provider] = []
        model_groups[provider].append(model)

    # Display models by provider
    for provider in sorted(model_groups.keys()):
        print(f"\n{provider}:")
        print("-" * 80)
        for model in sorted(model_groups[provider], key=lambda x: x.get("id", "")):
            model_id = model.get("id", "")
            context = model.get("context_length") or model.get("context_window", "N/A")
            owned_by = model.get("owned_by", "N/A")

            print(f"  • {model_id}")
            print(f"    Context: {context}, Owner: {owned_by}")

    print("\n" + "=" * 80)
    print("Pricing JSON Format")
    print("=" * 80)
    print("\nAdd this to src/data/manual_pricing.json under 'onerouter' key:")
    print("\n{")

    # Generate pricing JSON (sorted alphabetically)
    for model in sorted(models, key=lambda x: x.get("id", "")):
        model_id = model.get("id", "")
        context = model.get("context_length") or model.get("context_window", 4096)

        # Use placeholder pricing - needs to be filled with actual values
        print(f'  "{model_id}": {{')
        print(f'    "prompt": "0.00",')
        print(f'    "completion": "0.00",')
        print(f'    "request": "0",')
        print(f'    "image": "0",')
        print(f'    "context_length": {context}')
        print('  },')

    print("}")

    print("\n" + "=" * 80)
    print(f"Total Models: {len(models)}")
    print("=" * 80)
    print("\n⚠️  Note: Pricing values need to be manually updated from")
    print("   https://app.onerouter.pro/models (requires login)")
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
