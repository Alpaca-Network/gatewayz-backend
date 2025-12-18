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
    """Fetch all models from OneRouter API using the public display_models endpoint"""
    headers = {
        "Content-Type": "application/json",
    }

    try:
        # Use the public display_models endpoint (no auth required)
        print("📡 Fetching models from OneRouter API...")
        response = httpx.get(
            "https://app.onerouter.pro/api/display_models/",
            headers=headers,
            timeout=15.0,
            follow_redirects=True
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


def parse_token_limit(value):
    """Parse token limit from various formats"""
    if value is None:
        return 4096
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.replace(",", ""))
        except ValueError:
            return 4096
    return 4096


def parse_pricing(value):
    """Parse pricing value from various formats"""
    if value is None:
        return "0"
    if isinstance(value, str):
        return value.replace("$", "").replace(",", "").strip()
    return str(value)


def main():
    print("=" * 80)
    print("OneRouter Models Fetcher")
    print("=" * 80)

    # Fetch models from public display_models endpoint
    models = fetch_onerouter_models()

    if not models:
        print("\n❌ Could not fetch models from OneRouter API")
        return

    print("\n" + "=" * 80)
    print("Available Models")
    print("=" * 80)

    # Group models by provider/type
    model_groups = {}
    for model in models:
        # Use invoke_name as the model identifier
        model_id = model.get("invoke_name") or model.get("name", "")

        # Determine provider from model name
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
        elif "skylark" in model_id.lower():
            provider = "ByteDance"
        else:
            provider = "Other"

        if provider not in model_groups:
            model_groups[provider] = []
        model_groups[provider].append(model)

    # Display models by provider
    for provider in sorted(model_groups.keys()):
        print(f"\n{provider}:")
        print("-" * 80)
        for model in sorted(model_groups[provider], key=lambda x: x.get("invoke_name") or x.get("name", "")):
            model_id = model.get("invoke_name") or model.get("name", "")
            input_tokens = model.get("input_token_limit", "N/A")
            output_tokens = model.get("output_token_limit", "N/A")
            input_modalities = model.get("input_modalities", "Text")
            retail_input = model.get("retail_input_cost", "N/A")
            retail_output = model.get("retail_output_cost", "N/A")

            print(f"  • {model_id}")
            print(f"    Input: {input_tokens} tokens, Output: {output_tokens} tokens")
            print(f"    Modalities: {input_modalities}")
            print(f"    Pricing: Input {retail_input}/M, Output {retail_output}/M")

    print("\n" + "=" * 80)
    print("Pricing JSON Format")
    print("=" * 80)
    print("\nAdd this to src/data/manual_pricing.json under 'onerouter' key:")
    print("\n{")

    # Generate pricing JSON (sorted alphabetically)
    for model in sorted(models, key=lambda x: x.get("invoke_name") or x.get("name", "")):
        model_id = model.get("invoke_name") or model.get("name", "")
        if not model_id:
            continue

        input_tokens = parse_token_limit(model.get("input_token_limit"))
        output_tokens = parse_token_limit(model.get("output_token_limit"))

        # Use retail pricing (sale pricing is often $0 for free tier)
        prompt_price = parse_pricing(model.get("retail_input_cost"))
        completion_price = parse_pricing(model.get("retail_output_cost"))

        print(f'  "{model_id}": {{')
        print(f'    "prompt": "{prompt_price}",')
        print(f'    "completion": "{completion_price}",')
        print(f'    "request": "0",')
        print(f'    "image": "0",')
        print(f'    "context_length": {input_tokens},')
        print(f'    "max_completion_tokens": {output_tokens}')
        print('  },')

    print("}")

    print("\n" + "=" * 80)
    print(f"Total Models: {len(models)}")
    print("=" * 80)
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
