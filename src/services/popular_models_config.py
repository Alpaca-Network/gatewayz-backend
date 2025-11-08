"""
Popular Models Multi-Provider Configuration

This module defines commonly used models (Claude, GPT-4, Llama, DeepSeek, etc.)
with support for multiple providers for automatic failover and cost optimization.
"""

import logging
from typing import List

from src.services.canonical_model_registry import CanonicalModel
from src.services.multi_provider_registry import ProviderConfig

logger = logging.getLogger(__name__)


def get_claude_models() -> List[CanonicalModel]:
    """
    Get Anthropic Claude models configured with multiple providers.

    Claude models can be accessed through:
    1. OpenRouter (priority 1) - most reliable for general access
    2. Portkey (priority 2) - alternative gateway
    """
    return [
        CanonicalModel(
            id="claude-sonnet-4.5",
            name="Claude Sonnet 4.5",
            description="Anthropic's most capable model with advanced reasoning",
            context_length=200000,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-sonnet-4.5",
                    priority=1,
                    cost_per_1k_input=3.00,
                    cost_per_1k_output=15.00,
                    max_tokens=8192,
                    features=["streaming", "function_calling", "tools"],
                ),
                ProviderConfig(
                    name="portkey",
                    model_id="@anthropic/claude-sonnet-4.5-20250929",
                    priority=2,
                    cost_per_1k_input=3.00,
                    cost_per_1k_output=15.00,
                    max_tokens=8192,
                    features=["streaming", "function_calling", "tools"],
                ),
            ],
        ),
        CanonicalModel(
            id="claude-3-opus",
            name="Claude 3 Opus",
            description="Most powerful Claude 3 model for complex tasks",
            context_length=200000,
            modalities=["text", "image"],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-3-opus-20240229",
                    priority=1,
                    cost_per_1k_input=15.00,
                    cost_per_1k_output=75.00,
                    max_tokens=4096,
                    features=["streaming", "vision", "function_calling"],
                ),
            ],
        ),
        CanonicalModel(
            id="claude-3-sonnet",
            name="Claude 3 Sonnet",
            description="Balanced Claude 3 model",
            context_length=200000,
            modalities=["text", "image"],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-3-sonnet-20240229",
                    priority=1,
                    cost_per_1k_input=3.00,
                    cost_per_1k_output=15.00,
                    max_tokens=4096,
                    features=["streaming", "vision", "function_calling"],
                ),
            ],
        ),
        CanonicalModel(
            id="claude-3-haiku",
            name="Claude 3 Haiku",
            description="Fast and cost-effective Claude 3 model",
            context_length=200000,
            modalities=["text", "image"],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="anthropic/claude-3-haiku-20240307",
                    priority=1,
                    cost_per_1k_input=0.25,
                    cost_per_1k_output=1.25,
                    max_tokens=4096,
                    features=["streaming", "vision", "function_calling"],
                ),
            ],
        ),
    ]


def get_gpt_models() -> List[CanonicalModel]:
    """
    Get OpenAI GPT models configured with multiple providers.

    GPT models can be accessed through:
    1. OpenRouter (priority 1) - reliable general access
    2. Vercel AI Gateway (priority 2) - alternative for Next.js projects
    """
    return [
        CanonicalModel(
            id="gpt-4o",
            name="GPT-4o",
            description="OpenAI's most advanced multimodal model",
            context_length=128000,
            modalities=["text", "image"],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="openai/gpt-4o",
                    priority=1,
                    cost_per_1k_input=2.50,
                    cost_per_1k_output=10.00,
                    max_tokens=16384,
                    features=["streaming", "vision", "function_calling", "tools"],
                ),
                ProviderConfig(
                    name="vercel-ai-gateway",
                    model_id="gpt-4o",
                    priority=2,
                    cost_per_1k_input=2.50,
                    cost_per_1k_output=10.00,
                    max_tokens=16384,
                    features=["streaming", "vision", "function_calling"],
                ),
            ],
        ),
        CanonicalModel(
            id="gpt-4-turbo",
            name="GPT-4 Turbo",
            description="Fast GPT-4 with 128K context",
            context_length=128000,
            modalities=["text", "image"],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="openai/gpt-4-turbo",
                    priority=1,
                    cost_per_1k_input=10.00,
                    cost_per_1k_output=30.00,
                    max_tokens=4096,
                    features=["streaming", "vision", "function_calling", "tools"],
                ),
            ],
        ),
        CanonicalModel(
            id="gpt-3.5-turbo",
            name="GPT-3.5 Turbo",
            description="Fast and cost-effective model",
            context_length=16385,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="openai/gpt-3.5-turbo",
                    priority=1,
                    cost_per_1k_input=0.50,
                    cost_per_1k_output=1.50,
                    max_tokens=4096,
                    features=["streaming", "function_calling", "tools"],
                ),
            ],
        ),
    ]


def get_llama_models() -> List[CanonicalModel]:
    """
    Get Meta Llama models configured with multiple providers.

    Llama models are open source and available through many providers:
    1. Fireworks (priority 1) - optimized inference
    2. Together (priority 2) - reliable alternative
    3. HuggingFace (priority 3) - open source hosting
    4. OpenRouter (priority 4) - general access
    """
    return [
        CanonicalModel(
            id="llama-3.3-70b",
            name="Llama 3.3 70B",
            description="Meta's latest 70B parameter model",
            context_length=128000,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/llama-v3p3-70b-instruct",
                    priority=1,
                    cost_per_1k_input=0.90,
                    cost_per_1k_output=0.90,
                    max_tokens=16384,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="together",
                    model_id="meta-llama/Llama-3.3-70B-Instruct",
                    priority=2,
                    cost_per_1k_input=0.88,
                    cost_per_1k_output=0.88,
                    max_tokens=8192,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="meta-llama/Llama-3.3-70B-Instruct",
                    priority=3,
                    cost_per_1k_input=0.70,
                    cost_per_1k_output=0.70,
                    max_tokens=8192,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="meta-llama/llama-3.3-70b-instruct",
                    priority=4,
                    cost_per_1k_input=0.88,
                    cost_per_1k_output=0.88,
                    max_tokens=8192,
                    features=["streaming"],
                ),
            ],
        ),
        CanonicalModel(
            id="llama-3.1-70b",
            name="Llama 3.1 70B",
            description="Meta's 70B parameter model with 128K context",
            context_length=128000,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/llama-v3p1-70b-instruct",
                    priority=1,
                    cost_per_1k_input=0.90,
                    cost_per_1k_output=0.90,
                    max_tokens=16384,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="together",
                    model_id="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
                    priority=2,
                    cost_per_1k_input=0.88,
                    cost_per_1k_output=0.88,
                    max_tokens=8192,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="meta-llama/Meta-Llama-3.1-70B-Instruct",
                    priority=3,
                    cost_per_1k_input=0.70,
                    cost_per_1k_output=0.70,
                    max_tokens=8192,
                    features=["streaming"],
                ),
            ],
        ),
        CanonicalModel(
            id="llama-3.1-8b",
            name="Llama 3.1 8B",
            description="Efficient 8B parameter model",
            context_length=128000,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/llama-v3p1-8b-instruct",
                    priority=1,
                    cost_per_1k_input=0.20,
                    cost_per_1k_output=0.20,
                    max_tokens=16384,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="together",
                    model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
                    priority=2,
                    cost_per_1k_input=0.18,
                    cost_per_1k_output=0.18,
                    max_tokens=8192,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
                    priority=3,
                    cost_per_1k_input=0.00,  # Often free on HF
                    cost_per_1k_output=0.00,
                    max_tokens=8192,
                    features=["streaming"],
                ),
            ],
        ),
    ]


def get_deepseek_models() -> List[CanonicalModel]:
    """
    Get DeepSeek models configured with multiple providers.

    DeepSeek models are available through:
    1. Fireworks (priority 1) - optimized inference
    2. OpenRouter (priority 2) - general access
    3. Together (priority 3) - alternative provider
    """
    return [
        CanonicalModel(
            id="deepseek-v3",
            name="DeepSeek V3",
            description="DeepSeek's latest and most capable model",
            context_length=64000,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/deepseek-v3p1",
                    priority=1,
                    cost_per_1k_input=0.55,
                    cost_per_1k_output=2.19,
                    max_tokens=8192,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="deepseek/deepseek-chat",
                    priority=2,
                    cost_per_1k_input=0.55,
                    cost_per_1k_output=2.19,
                    max_tokens=8192,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="together",
                    model_id="deepseek-ai/DeepSeek-V3",
                    priority=3,
                    cost_per_1k_input=0.55,
                    cost_per_1k_output=2.19,
                    max_tokens=8192,
                    features=["streaming"],
                ),
            ],
        ),
        CanonicalModel(
            id="deepseek-r1",
            name="DeepSeek R1",
            description="DeepSeek's reasoning-focused model",
            context_length=64000,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/deepseek-r1-0528",
                    priority=1,
                    cost_per_1k_input=0.55,
                    cost_per_1k_output=2.19,
                    max_tokens=8192,
                    features=["streaming", "reasoning"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="deepseek/deepseek-r1",
                    priority=2,
                    cost_per_1k_input=0.55,
                    cost_per_1k_output=2.19,
                    max_tokens=8192,
                    features=["streaming", "reasoning"],
                ),
            ],
        ),
    ]


def get_qwen_models() -> List[CanonicalModel]:
    """
    Get Qwen models configured with multiple providers.
    """
    return [
        CanonicalModel(
            id="qwen-2.5-72b",
            name="Qwen 2.5 72B",
            description="Alibaba's powerful 72B parameter model",
            context_length=32768,
            modalities=["text"],
            providers=[
                ProviderConfig(
                    name="huggingface",
                    model_id="Qwen/Qwen2.5-72B-Instruct",
                    priority=1,
                    cost_per_1k_input=0.40,
                    cost_per_1k_output=0.40,
                    max_tokens=8192,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="qwen/qwen-2.5-72b-instruct",
                    priority=2,
                    cost_per_1k_input=0.40,
                    cost_per_1k_output=0.40,
                    max_tokens=8192,
                    features=["streaming"],
                ),
            ],
        ),
    ]


def get_all_popular_models() -> List[CanonicalModel]:
    """Get all popular multi-provider models"""
    models = []
    models.extend(get_claude_models())
    models.extend(get_gpt_models())
    models.extend(get_llama_models())
    models.extend(get_deepseek_models())
    models.extend(get_qwen_models())
    return models


def initialize_popular_models(registry=None) -> None:
    """
    Initialize the canonical registry with popular multi-provider models.

    This should be called during application startup to register all
    popular models with their provider configurations.
    """
    if registry is None:
        from src.services.canonical_model_registry import get_canonical_registry
        registry = get_canonical_registry()

    models = get_all_popular_models()

    logger.info(f"Initializing {len(models)} popular models with multi-provider support")

    for model in models:
        registry.register_model(model)
        logger.debug(
            f"Registered {model.id} with providers: "
            f"{[p.name for p in model.providers]}"
        )

    logger.info(
        f"✓ Successfully initialized {len(models)} popular models in canonical registry"
    )
