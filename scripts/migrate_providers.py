#!/usr/bin/env python3
"""
Script to migrate provider fetch functions from models.py to dedicated client files.
This automates the tedious refactoring work for issue #1015.
"""

import re
from pathlib import Path

# Define the providers and their target files
PROVIDERS = {
    "deepinfra": {
        "client_file": "src/services/deepinfra_client.py",
        "fetch_function": "fetch_models_from_deepinfra",
        "normalize_function": "normalize_deepinfra_model",
    },
    "featherless": {
        "client_file": "src/services/featherless_client.py",
        "fetch_function": "fetch_models_from_featherless",
        "normalize_function": "normalize_featherless_model",
    },
    "chutes": {
        "client_file": "src/services/chutes_client.py",
        "fetch_function": "fetch_models_from_chutes",
        "normalize_function": "normalize_chutes_model",
        "extra_functions": ["fetch_models_from_chutes_api"],
    },
    "fireworks": {
        "client_file": "src/services/fireworks_client.py",
        "fetch_function": "fetch_models_from_fireworks",
        "normalize_function": "normalize_fireworks_model",
    },
    "together": {
        "client_file": "src/services/together_client.py",
        "fetch_function": "fetch_models_from_together",
        "normalize_function": "normalize_together_model",
    },
    "aimo": {
        "client_file": "src/services/aimo_client.py",
        "fetch_function": "fetch_models_from_aimo",
        "normalize_function": "normalize_aimo_model",
    },
    "near": {
        "client_file": "src/services/near_client.py",
        "fetch_function": "fetch_models_from_near",
        "normalize_function": "normalize_near_model",
    },
    "fal": {
        "client_file": "src/services/fal_image_client.py",
        "fetch_function": "fetch_models_from_fal",
        "normalize_function": "normalize_fal_model",
    },
    "vercel": {
        "client_file": "src/services/vercel_ai_gateway_client.py",
        "fetch_function": "fetch_models_from_vercel_ai_gateway",
        "normalize_function": "normalize_vercel_model",
    },
    "aihubmix": {
        "client_file": "src/services/aihubmix_client.py",
        "fetch_function": "fetch_models_from_aihubmix",
        "normalize_function": "normalize_aihubmix_model",
    },
    "helicone": {
        "client_file": "src/services/helicone_client.py",
        "fetch_function": "fetch_models_from_helicone",
        "normalize_function": "normalize_helicone_model",
    },
    "anannas": {
        "client_file": "src/services/anannas_client.py",
        "fetch_function": "fetch_models_from_anannas",
        "normalize_function": "normalize_anannas_model",
    },
    "alibaba": {
        "client_file": "src/services/alibaba_cloud_client.py",
        "fetch_function": "fetch_models_from_alibaba",
        "normalize_function": "normalize_alibaba_model",
    },
    "openai": {
        "client_file": "src/services/openai_client.py",
        "fetch_function": "fetch_models_from_openai",
        "normalize_function": "normalize_openai_model",
    },
    "anthropic": {
        "client_file": "src/services/anthropic_client.py",
        "fetch_function": "fetch_models_from_anthropic",
        "normalize_function": "normalize_anthropic_model",
    },
    "zai": {
        "client_file": "src/services/zai_client.py",
        "fetch_function": "fetch_models_from_zai",
        "normalize_function": "normalize_zai_model",
    },
}


def extract_function_from_file(file_path: Path, function_name: str) -> str | None:
    """Extract a complete function definition from a Python file."""
    content = file_path.read_text()

    # Pattern to match function definition
    pattern = rf"^def {re.escape(function_name)}\([^)]*\).*?(?=\n(?:def |class |\Z))"

    match = re.search(pattern, content, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(0).rstrip()
    return None


def main():
    repo_root = Path(__file__).parent.parent
    models_file = repo_root / "src" / "services" / "models.py"

    if not models_file.exists():
        print(f"Error: models.py not found at {models_file}")
        return

    print("Provider Migration Script")
    print("=" * 60)
    print(f"Reading from: {models_file}")
    print()

    models_content = models_file.read_text()

    for provider_name, config in PROVIDERS.items():
        print(f"\n Processing {provider_name}...")
        print(f"  Target: {config['client_file']}")

        client_file = repo_root / config['client_file']

        if not client_file.exists():
            print(f"  ⚠️  Client file doesn't exist: {client_file}")
            continue

        # Extract fetch function
        fetch_func = extract_function_from_file(models_file, config['fetch_function'])
        if not fetch_func:
            print(f"  ⚠️  Could not find fetch function: {config['fetch_function']}")
            continue

        # Extract normalize function if specified
        normalize_func = None
        if "normalize_function" in config:
            normalize_func = extract_function_from_file(models_file, config['normalize_function'])
            if not normalize_func:
                print(f"  ⚠️  Could not find normalize function: {config['normalize_function']}")

        print(f"  ✓ Extracted fetch function ({len(fetch_func)} chars)")
        if normalize_func:
            print(f"  ✓ Extracted normalize function ({len(normalize_func)} chars)")

        # Here you would append to the client file
        # For safety, we'll just report what would be done
        print(f"  → Would append {1 if not normalize_func else 2} function(s) to {client_file.name}")


if __name__ == "__main__":
    main()
