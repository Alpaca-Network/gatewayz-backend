"""
Curated Multi-Provider Model Configurations

This module defines popular open-source models that are available across multiple
providers with stable semantics and well-understood parity.

Models included:
- Meta Llama 3.x family (3.1, 3.3)
- DeepSeek V3 and R1
- Qwen 2.5 family
- Mistral/Mixtral family
- Gemma 2/3 family
- Microsoft Phi models
"""

import logging
from typing import List

from src.services.multi_provider_registry import (
    MultiProviderModel,
    ProviderConfig,
    get_registry,
)

logger = logging.getLogger(__name__)


def get_llama_models() -> List[MultiProviderModel]:
    """Meta Llama models available across multiple providers"""
    models = [
        MultiProviderModel(
            id="llama-3.3-70b-instruct",
            name="Llama 3.3 70B Instruct",
            description="Meta's Llama 3.3 70B instruction-tuned model",
            context_length=131072,
            modalities=["text"],
            aliases=[
                "meta-llama/llama-3.3-70b",
                "meta-llama/llama-3.3-70b-instruct",
                "meta-llama/Llama-3.3-70B-Instruct",
                "llama-3.3-70b",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="meta-llama/llama-3.3-70b-instruct",
                    priority=1,
                    cost_per_1k_input=0.18,
                    cost_per_1k_output=0.18,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/llama-v3p3-70b-instruct",
                    priority=2,
                    cost_per_1k_input=0.20,
                    cost_per_1k_output=0.20,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="together",
                    model_id="meta-llama/Llama-3.3-70B-Instruct",
                    priority=3,
                    cost_per_1k_input=0.18,
                    cost_per_1k_output=0.18,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="meta-llama/Llama-3.3-70B-Instruct",
                    priority=4,
                    cost_per_1k_input=0.35,
                    cost_per_1k_output=0.40,
                    features=["streaming"],
                ),
            ],
        ),
        MultiProviderModel(
            id="llama-3.1-70b-instruct",
            name="Llama 3.1 70B Instruct",
            description="Meta's Llama 3.1 70B instruction-tuned model",
            context_length=131072,
            modalities=["text"],
            aliases=[
                "meta-llama/llama-3.1-70b",
                "meta-llama/llama-3.1-70b-instruct",
                "meta-llama/Meta-Llama-3.1-70B-Instruct",
                "llama-3.1-70b",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="meta-llama/llama-3.1-70b-instruct",
                    priority=1,
                    cost_per_1k_input=0.18,
                    cost_per_1k_output=0.18,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/llama-v3p1-70b-instruct",
                    priority=2,
                    cost_per_1k_input=0.20,
                    cost_per_1k_output=0.20,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="together",
                    model_id="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
                    priority=3,
                    cost_per_1k_input=0.18,
                    cost_per_1k_output=0.18,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="meta-llama/Meta-Llama-3.1-70B-Instruct",
                    priority=4,
                    cost_per_1k_input=0.35,
                    cost_per_1k_output=0.40,
                    features=["streaming"],
                ),
            ],
        ),
        MultiProviderModel(
            id="llama-3.1-8b-instruct",
            name="Llama 3.1 8B Instruct",
            description="Meta's Llama 3.1 8B instruction-tuned model",
            context_length=131072,
            modalities=["text"],
            aliases=[
                "meta-llama/llama-3.1-8b",
                "meta-llama/llama-3.1-8b-instruct",
                "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "llama-3.1-8b",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="meta-llama/llama-3.1-8b-instruct",
                    priority=1,
                    cost_per_1k_input=0.06,
                    cost_per_1k_output=0.06,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/llama-v3p1-8b-instruct",
                    priority=2,
                    cost_per_1k_input=0.08,
                    cost_per_1k_output=0.08,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
                    priority=3,
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.12,
                    features=["streaming"],
                ),
            ],
        ),
    ]
    return models


def get_deepseek_models() -> List[MultiProviderModel]:
    """DeepSeek models available across multiple providers"""
    models = [
        MultiProviderModel(
            id="deepseek-v3",
            name="DeepSeek V3",
            description="DeepSeek's V3 model with advanced reasoning capabilities",
            context_length=32768,
            modalities=["text"],
            aliases=[
                "deepseek-ai/deepseek-v3",
                "deepseek-ai/DeepSeek-V3",
                "deepseek/deepseek-chat",
                "deepseek-v3",
            ],
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/deepseek-v3p1",
                    priority=1,
                    cost_per_1k_input=0.27,
                    cost_per_1k_output=1.10,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="deepseek/deepseek-chat",
                    priority=2,
                    cost_per_1k_input=0.14,
                    cost_per_1k_output=0.28,
                    features=["streaming", "function_calling"],
                ),
                ProviderConfig(
                    name="featherless",
                    model_id="deepseek-ai/DeepSeek-V3",
                    priority=3,
                    cost_per_1k_input=0.30,
                    cost_per_1k_output=1.20,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="together",
                    model_id="deepseek-ai/DeepSeek-V3",
                    priority=4,
                    cost_per_1k_input=0.27,
                    cost_per_1k_output=1.10,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="deepseek-ai/DeepSeek-V3",
                    priority=5,
                    cost_per_1k_input=0.35,
                    cost_per_1k_output=1.30,
                    features=["streaming"],
                ),
            ],
        ),
        MultiProviderModel(
            id="deepseek-r1",
            name="DeepSeek R1",
            description="DeepSeek's reasoning-focused R1 model",
            context_length=32768,
            modalities=["text"],
            aliases=[
                "deepseek-ai/deepseek-r1",
                "deepseek-ai/DeepSeek-R1",
                "deepseek-r1",
            ],
            providers=[
                ProviderConfig(
                    name="fireworks",
                    model_id="accounts/fireworks/models/deepseek-r1-0528",
                    priority=1,
                    cost_per_1k_input=0.55,
                    cost_per_1k_output=2.19,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="deepseek/deepseek-r1",
                    priority=2,
                    cost_per_1k_input=0.55,
                    cost_per_1k_output=2.19,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="deepseek-ai/DeepSeek-R1",
                    priority=3,
                    cost_per_1k_input=0.60,
                    cost_per_1k_output=2.30,
                    features=["streaming"],
                ),
            ],
        ),
    ]
    return models


def get_qwen_models() -> List[MultiProviderModel]:
    """Qwen models available across multiple providers"""
    models = [
        MultiProviderModel(
            id="qwen-2.5-72b-instruct",
            name="Qwen 2.5 72B Instruct",
            description="Alibaba's Qwen 2.5 72B instruction-tuned model",
            context_length=32768,
            modalities=["text"],
            aliases=[
                "qwen/qwen-2.5-72b",
                "qwen/qwen-2.5-72b-instruct",
                "Qwen/Qwen2.5-72B-Instruct",
                "qwen-2.5-72b",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="qwen/qwen-2.5-72b-instruct",
                    priority=1,
                    cost_per_1k_input=0.35,
                    cost_per_1k_output=0.40,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="Qwen/Qwen2.5-72B-Instruct",
                    priority=2,
                    cost_per_1k_input=0.40,
                    cost_per_1k_output=0.45,
                    features=["streaming"],
                ),
            ],
        ),
        MultiProviderModel(
            id="qwen-2.5-7b-instruct",
            name="Qwen 2.5 7B Instruct",
            description="Alibaba's Qwen 2.5 7B instruction-tuned model",
            context_length=32768,
            modalities=["text"],
            aliases=[
                "qwen/qwen-2.5-7b",
                "qwen/qwen-2.5-7b-instruct",
                "Qwen/Qwen2.5-7B-Instruct",
                "qwen-2.5-7b",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="qwen/qwen-2.5-7b-instruct",
                    priority=1,
                    cost_per_1k_input=0.08,
                    cost_per_1k_output=0.08,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="Qwen/Qwen2.5-7B-Instruct",
                    priority=2,
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.10,
                    features=["streaming"],
                ),
            ],
        ),
    ]
    return models


def get_mistral_models() -> List[MultiProviderModel]:
    """Mistral models available across multiple providers"""
    models = [
        MultiProviderModel(
            id="mixtral-8x7b-instruct",
            name="Mixtral 8x7B Instruct",
            description="Mistral's Mixtral 8x7B MoE instruction-tuned model",
            context_length=32768,
            modalities=["text"],
            aliases=[
                "mistralai/mixtral-8x7b",
                "mistralai/mixtral-8x7b-instruct",
                "mistralai/Mixtral-8x7B-Instruct-v0.1",
                "mixtral-8x7b",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="mistralai/mixtral-8x7b-instruct",
                    priority=1,
                    cost_per_1k_input=0.24,
                    cost_per_1k_output=0.24,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
                    priority=2,
                    cost_per_1k_input=0.30,
                    cost_per_1k_output=0.30,
                    features=["streaming"],
                ),
            ],
        ),
        MultiProviderModel(
            id="mistral-7b-instruct",
            name="Mistral 7B Instruct",
            description="Mistral's 7B instruction-tuned model",
            context_length=32768,
            modalities=["text"],
            aliases=[
                "mistralai/mistral-7b",
                "mistralai/mistral-7b-instruct",
                "mistralai/Mistral-7B-Instruct-v0.3",
                "mistral-7b",
            ],
            providers=[
                ProviderConfig(
                    name="openrouter",
                    model_id="mistralai/mistral-7b-instruct",
                    priority=1,
                    cost_per_1k_input=0.06,
                    cost_per_1k_output=0.06,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="huggingface",
                    model_id="mistralai/Mistral-7B-Instruct-v0.3",
                    priority=2,
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.10,
                    features=["streaming"],
                ),
            ],
        ),
    ]
    return models


def get_gemma_models() -> List[MultiProviderModel]:
    """Google Gemma models available across multiple providers"""
    models = [
        MultiProviderModel(
            id="gemma-2-27b-it",
            name="Gemma 2 27B Instruct",
            description="Google's Gemma 2 27B instruction-tuned model",
            context_length=8192,
            modalities=["text"],
            aliases=[
                "google/gemma-2-27b-it",
                "gemma-2-27b",
            ],
            providers=[
                ProviderConfig(
                    name="google-vertex",
                    model_id="gemma-2-27b-it",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.10,
                    cost_per_1k_output=0.20,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="google/gemma-2-27b-it",
                    priority=2,
                    cost_per_1k_input=0.15,
                    cost_per_1k_output=0.25,
                    features=["streaming"],
                ),
            ],
        ),
        MultiProviderModel(
            id="gemma-2-9b-it",
            name="Gemma 2 9B Instruct",
            description="Google's Gemma 2 9B instruction-tuned model",
            context_length=8192,
            modalities=["text"],
            aliases=[
                "google/gemma-2-9b-it",
                "gemma-2-9b",
            ],
            providers=[
                ProviderConfig(
                    name="google-vertex",
                    model_id="gemma-2-9b-it",
                    priority=1,
                    requires_credentials=True,
                    cost_per_1k_input=0.03,
                    cost_per_1k_output=0.06,
                    features=["streaming"],
                ),
                ProviderConfig(
                    name="openrouter",
                    model_id="google/gemma-2-9b-it:free",
                    priority=2,
                    cost_per_1k_input=0.00,
                    cost_per_1k_output=0.00,
                    features=["streaming"],
                ),
            ],
        ),
    ]
    return models


def get_all_curated_models() -> List[MultiProviderModel]:
    """Get all curated multi-provider models"""
    all_models = []
    all_models.extend(get_llama_models())
    all_models.extend(get_deepseek_models())
    all_models.extend(get_qwen_models())
    all_models.extend(get_mistral_models())
    all_models.extend(get_gemma_models())
    return all_models


def initialize_curated_models() -> None:
    """
    Initialize the multi-provider registry with curated models.
    
    This should be called during application startup to register all
    curated models with their provider configurations.
    """
    registry = get_registry()
    models = get_all_curated_models()
    
    logger.info(f"Initializing {len(models)} curated multi-provider models")
    
    for model in models:
        registry.register_model(model)
        logger.debug(
            f"Registered {model.id} with providers: "
            f"{[p.name for p in model.providers]}"
        )
    
    logger.info(
        f"✓ Successfully initialized {len(models)} curated models in multi-provider registry"
    )
