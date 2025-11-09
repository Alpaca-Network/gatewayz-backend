#!/usr/bin/env python3
"""
Script to migrate existing provider-specific models to the multi-provider registry.

This script demonstrates how to convert existing single-provider models 
to the new multi-provider format.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.services.multi_provider_registry import MultiProviderModel, ProviderConfig, get_registry
from src.services.models import get_cached_models


def migrate_gemini_models():
    """
    Example migration function for Google Gemini models.
    
    This shows how to convert existing provider-specific models
    to the multi-provider format.
    """
    print("Migrating Gemini models to multi-provider format...")
    
    # Create multi-provider models for Gemini
    gemini_models = [
        MultiProviderModel(
            id="gemini-1.5-flash",
            name="Gemini 1.5 Flash",
            description="Fast and efficient model for everyday tasks",
            context_length=1000000,
            modalities=["text", "image", "audio", "video"],
            categories=["chat", "multimodal"],
            capabilities=["streaming", "function_calling"],
            providers=[
                ProviderConfig(
                    name="google-vertex",
                    model_id="gemini-1.5-flash",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.075,
                    cost_per_1k_output=0.30,
                    max_tokens=8192,
                    features=["streaming", "multimodal", "function_calling"],
                    availability=True,
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="google/gemini-flash-1.5",
                    priority=2,
                    requires_credentials=False,
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.40,
                    max_tokens=8192,
                    features=["streaming", "multimodal"],
                    availability=True,
                ),
            ],
        ),
        MultiProviderModel(
            id="gemini-1.5-pro",
            name="Gemini 1.5 Pro",
            description="Most capable Gemini model for complex reasoning",
            context_length=1000000,
            modalities=["text", "image", "audio", "video"],
            categories=["chat", "reasoning", "multimodal"],
            capabilities=["streaming", "function_calling", "multimodal"],
            providers=[
                ProviderConfig(
                    name="google-vertex",
                    model_id="gemini-1.5-pro",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=1.25,
                    cost_per_1k_output=5.00,
                    max_tokens=8192,
                    features=["streaming", "multimodal", "function_calling"],
                    availability=True,
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="google/gemini-pro-1.5",
                    priority=2,
                    requires_credentials=False,
                    cost_per_1k_input=1.50,
                    cost_per_1k_output=6.00,
                    max_tokens=8192,
                    features=["streaming", "multimodal"],
                    availability=True,
                ),
            ],
        ),
    ]
    
    # Register the models
    registry = get_registry()
    for model in gemini_models:
        registry.register_model(model)
        print(f"✓ Registered {model.id} with {len(model.providers)} providers")
    
    return gemini_models


def migrate_gpt_models():
    """
    Example migration function for OpenAI GPT models.
    """
    print("Migrating GPT models to multi-provider format...")
    
    gpt_models = [
        MultiProviderModel(
            id="gpt-4-turbo",
            name="GPT-4 Turbo",
            description="High-intelligence model for complex tasks",
            context_length=128000,
            modalities=["text"],
            categories=["chat", "reasoning"],
            capabilities=["function_calling", "streaming"],
            providers=[
                ProviderConfig(
                    name="openai",
                    model_id="gpt-4-turbo",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.01,
                    cost_per_1k_output=0.03,
                    max_tokens=4096,
                    features=["streaming", "function_calling"],
                    availability=True,
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="openai/gpt-4-turbo",
                    priority=2,
                    requires_credentials=False,
                    cost_per_1k_input=0.015,
                    cost_per_1k_output=0.045,
                    max_tokens=4096,
                    features=["streaming", "function_calling"],
                    availability=True,
                ),
            ],
        ),
        MultiProviderModel(
            id="gpt-3.5-turbo",
            name="GPT-3.5 Turbo",
            description="Fast and affordable model for simple tasks",
            context_length=16385,
            modalities=["text"],
            categories=["chat"],
            capabilities=["function_calling", "streaming"],
            providers=[
                ProviderConfig(
                    name="openai",
                    model_id="gpt-3.5-turbo",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.0005,
                    cost_per_1k_output=0.0015,
                    max_tokens=4096,
                    features=["streaming", "function_calling"],
                    availability=True,
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="openai/gpt-3.5-turbo",
                    priority=2,
                    requires_credentials=False,
                    cost_per_1k_input=0.001,
                    cost_per_1k_output=0.002,
                    max_tokens=4096,
                    features=["streaming", "function_calling"],
                    availability=True,
                ),
            ],
        ),
    ]
    
    # Register the models
    registry = get_registry()
    for model in gpt_models:
        registry.register_model(model)
        print(f"✓ Registered {model.id} with {len(model.providers)} providers")
    
    return gpt_models


def migrate_claude_models():
    """
    Example migration function for Anthropic Claude models.
    """
    print("Migrating Claude models to multi-provider format...")
    
    claude_models = [
        MultiProviderModel(
            id="claude-3-opus",
            name="Claude 3 Opus",
            description="Most powerful Claude model for complex analysis",
            context_length=200000,
            modalities=["text", "image"],
            categories=["chat", "reasoning", "multimodal"],
            capabilities=["function_calling", "streaming", "multimodal"],
            providers=[
                ProviderConfig(
                    name="anthropic",
                    model_id="claude-3-opus-20240229",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.015,
                    cost_per_1k_output=0.075,
                    max_tokens=4096,
                    features=["streaming", "multimodal", "function_calling"],
                    availability=True,
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-3-opus",
                    priority=2,
                    requires_credentials=False,
                    cost_per_1k_input=0.02,
                    cost_per_1k_output=0.10,
                    max_tokens=4096,
                    features=["streaming", "multimodal"],
                    availability=True,
                ),
            ],
        ),
        MultiProviderModel(
            id="claude-3-sonnet",
            name="Claude 3 Sonnet",
            description="Balance of intelligence and speed",
            context_length=200000,
            modalities=["text", "image"],
            categories=["chat", "multimodal"],
            capabilities=["function_calling", "streaming", "multimodal"],
            providers=[
                ProviderConfig(
                    name="anthropic",
                    model_id="claude-3-sonnet-20240229",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.003,
                    cost_per_1k_output=0.015,
                    max_tokens=4096,
                    features=["streaming", "multimodal", "function_calling"],
                    availability=True,
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-3-sonnet",
                    priority=2,
                    requires_credentials=False,
                    cost_per_1k_input=0.005,
                    cost_per_1k_output=0.025,
                    max_tokens=4096,
                    features=["streaming", "multimodal"],
                    availability=True,
                ),
            ],
        ),
    ]
    
    # Register the models
    registry = get_registry()
    for model in claude_models:
        registry.register_model(model)
        print(f"✓ Registered {model.id} with {len(model.providers)} providers")
    
    return claude_models


def demonstrate_migration():
    """
    Demonstrate the migration process.
    """
    print("=== Multi-Provider Model Migration Demo ===\n")
    
    # Migrate different model families
    gemini_models = migrate_gemini_models()
    print()
    
    gpt_models = migrate_gpt_models()
    print()
    
    claude_models = migrate_claude_models()
    print()
    
    # Show the registry contents
    registry = get_registry()
    all_models = registry.get_all_models()
    
    print(f"Total models in registry: {len(all_models)}")
    print("\nModel Catalog:")
    print("-" * 50)
    
    for model in all_models:
        print(f"Model: {model.id}")
        print(f"  Name: {model.name}")
        print(f"  Description: {model.description}")
        print(f"  Context Length: {model.context_length}")
        print(f"  Modalities: {', '.join(model.modalities)}")
        print(f"  Providers ({len(model.providers)}):")
        for provider in model.providers:
            print(f"    - {provider.name} ({provider.model_id}) - Priority: {provider.priority}")
        print()
    
    # Demonstrate provider selection
    print("Provider Selection Examples:")
    print("-" * 30)
    
    for model_id in ["gemini-1.5-flash", "gpt-4-turbo", "claude-3-opus"]:
        provider = registry.select_provider(model_id)
        if provider:
            print(f"{model_id} -> {provider.name} ({provider.model_id})")
        else:
            print(f"{model_id} -> No provider available")
    
    print("\n✅ Migration demonstration completed!")


if __name__ == "__main__":
    demonstrate_migration()