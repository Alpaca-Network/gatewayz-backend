"""
Model Registry Configuration

This module defines configurations for common models available across multiple providers.
It extends beyond Google models to include popular models from OpenAI, Anthropic, Meta, and others.
"""

import logging
from typing import List, Dict, Any, Optional

from src.services.canonical_model_registry import (
    CanonicalModel,
    get_canonical_registry,
)
from src.services.multi_provider_registry import ProviderConfig

logger = logging.getLogger(__name__)


def get_openai_compatible_models() -> List[CanonicalModel]:
    """
    Get OpenAI models available across multiple providers.
    """

    models = []

    # GPT-4o variants
    gpt4o = CanonicalModel(
        id="gpt-4o",
        name="GPT-4o",
        description="OpenAI's most capable multimodal model",
    )
    gpt4o.add_provider(
        provider_name="openrouter",
        provider_model_id="openai/gpt-4o",
        context_length=128000,
        modalities=["text", "image"],
        features=["streaming", "function_calling", "multimodal"],
        input_cost=2.50,
        output_cost=10.00,
    )
    gpt4o.add_provider(
        provider_name="portkey",
        provider_model_id="gpt-4o",
        context_length=128000,
        modalities=["text", "image"],
        features=["streaming", "function_calling", "multimodal"],
        input_cost=2.50,
        output_cost=10.00,
    )
    gpt4o.add_provider(
        provider_name="together",
        provider_model_id="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",  # Similar capability
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.88,
        output_cost=0.88,
    )
    gpt4o.tags.add("flagship")
    gpt4o.tags.add("multimodal")
    models.append(gpt4o)

    # GPT-4o-mini
    gpt4o_mini = CanonicalModel(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        description="Affordable small model with GPT-4o capabilities",
    )
    gpt4o_mini.add_provider(
        provider_name="openrouter",
        provider_model_id="openai/gpt-4o-mini",
        context_length=128000,
        modalities=["text", "image"],
        features=["streaming", "function_calling", "multimodal"],
        input_cost=0.15,
        output_cost=0.60,
    )
    gpt4o_mini.add_provider(
        provider_name="portkey",
        provider_model_id="gpt-4o-mini",
        context_length=128000,
        modalities=["text", "image"],
        features=["streaming", "function_calling", "multimodal"],
        input_cost=0.15,
        output_cost=0.60,
    )
    gpt4o_mini.add_provider(
        provider_name="fireworks",
        provider_model_id="accounts/fireworks/models/gpt-4o-mini",
        context_length=128000,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.18,
        output_cost=0.72,
    )
    gpt4o_mini.tags.add("efficient")
    gpt4o_mini.tags.add("multimodal")
    models.append(gpt4o_mini)

    # GPT-3.5-turbo
    gpt35 = CanonicalModel(
        id="gpt-3.5-turbo",
        name="GPT-3.5 Turbo",
        description="Fast and efficient model for most tasks",
    )
    gpt35.add_provider(
        provider_name="openrouter",
        provider_model_id="openai/gpt-3.5-turbo",
        context_length=16385,
        modalities=["text"],
        features=["streaming", "function_calling"],
        input_cost=0.50,
        output_cost=1.50,
    )
    gpt35.add_provider(
        provider_name="portkey",
        provider_model_id="gpt-3.5-turbo",
        context_length=16385,
        modalities=["text"],
        features=["streaming", "function_calling"],
        input_cost=0.50,
        output_cost=1.50,
    )
    gpt35.tags.add("fast")
    gpt35.tags.add("affordable")
    models.append(gpt35)

    return models


def get_anthropic_models() -> List[CanonicalModel]:
    """
    Get Anthropic Claude models available across multiple providers.
    """

    models = []

    # Claude 3.5 Sonnet
    claude_35_sonnet = CanonicalModel(
        id="claude-3.5-sonnet",
        name="Claude 3.5 Sonnet",
        description="Anthropic's most intelligent model",
    )
    claude_35_sonnet.add_provider(
        provider_name="openrouter",
        provider_model_id="anthropic/claude-3.5-sonnet",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=3.00,
        output_cost=15.00,
    )
    claude_35_sonnet.add_provider(
        provider_name="portkey",
        provider_model_id="claude-3-5-sonnet-20241022",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=3.00,
        output_cost=15.00,
    )
    claude_35_sonnet.add_provider(
        provider_name="google-vertex",
        provider_model_id="claude-3-5-sonnet@20241022",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=3.00,
        output_cost=15.00,
    )
    claude_35_sonnet.tags.add("flagship")
    claude_35_sonnet.tags.add("coding")
    models.append(claude_35_sonnet)

    # Claude 3 Haiku
    claude_3_haiku = CanonicalModel(
        id="claude-3-haiku",
        name="Claude 3 Haiku",
        description="Fast and affordable Claude model",
    )
    claude_3_haiku.add_provider(
        provider_name="openrouter",
        provider_model_id="anthropic/claude-3-haiku",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=0.25,
        output_cost=1.25,
    )
    claude_3_haiku.add_provider(
        provider_name="portkey",
        provider_model_id="claude-3-haiku-20240307",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=0.25,
        output_cost=1.25,
    )
    claude_3_haiku.add_provider(
        provider_name="google-vertex",
        provider_model_id="claude-3-haiku@20240307",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=0.25,
        output_cost=1.25,
    )
    claude_3_haiku.tags.add("fast")
    claude_3_haiku.tags.add("affordable")
    models.append(claude_3_haiku)

    # Claude 3 Opus
    claude_3_opus = CanonicalModel(
        id="claude-3-opus",
        name="Claude 3 Opus",
        description="Powerful model for complex tasks",
    )
    claude_3_opus.add_provider(
        provider_name="openrouter",
        provider_model_id="anthropic/claude-3-opus",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=15.00,
        output_cost=75.00,
    )
    claude_3_opus.add_provider(
        provider_name="portkey",
        provider_model_id="claude-3-opus-20240229",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=15.00,
        output_cost=75.00,
    )
    claude_3_opus.add_provider(
        provider_name="google-vertex",
        provider_model_id="claude-3-opus@20240229",
        context_length=200000,
        modalities=["text", "image"],
        features=["streaming", "multimodal"],
        input_cost=15.00,
        output_cost=75.00,
    )
    claude_3_opus.tags.add("powerful")
    claude_3_opus.tags.add("reasoning")
    models.append(claude_3_opus)

    return models


def get_llama_models() -> List[CanonicalModel]:
    """
    Get Meta Llama models available across multiple providers.
    """

    models = []

    # Llama 3.1 405B
    llama_31_405b = CanonicalModel(
        id="llama-3.1-405b-instruct",
        name="Llama 3.1 405B Instruct",
        description="Meta's largest open model",
    )
    llama_31_405b.add_provider(
        provider_name="openrouter",
        provider_model_id="meta-llama/llama-3.1-405b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=2.70,
        output_cost=2.70,
    )
    llama_31_405b.add_provider(
        provider_name="together",
        provider_model_id="meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=3.50,
        output_cost=3.50,
    )
    llama_31_405b.add_provider(
        provider_name="fireworks",
        provider_model_id="accounts/fireworks/models/llama-v3p1-405b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=3.00,
        output_cost=3.00,
    )
    llama_31_405b.add_provider(
        provider_name="deepinfra",
        provider_model_id="meta-llama/Meta-Llama-3.1-405B-Instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=2.70,
        output_cost=2.70,
    )
    llama_31_405b.tags.add("open-source")
    llama_31_405b.tags.add("large")
    models.append(llama_31_405b)

    # Llama 3.1 70B
    llama_31_70b = CanonicalModel(
        id="llama-3.1-70b-instruct",
        name="Llama 3.1 70B Instruct",
        description="Powerful open model with great performance",
    )
    llama_31_70b.add_provider(
        provider_name="openrouter",
        provider_model_id="meta-llama/llama-3.1-70b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.52,
        output_cost=0.75,
    )
    llama_31_70b.add_provider(
        provider_name="together",
        provider_model_id="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.88,
        output_cost=0.88,
    )
    llama_31_70b.add_provider(
        provider_name="fireworks",
        provider_model_id="accounts/fireworks/models/llama-v3p1-70b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.90,
        output_cost=0.90,
    )
    llama_31_70b.add_provider(
        provider_name="deepinfra",
        provider_model_id="meta-llama/Meta-Llama-3.1-70B-Instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.52,
        output_cost=0.75,
    )
    llama_31_70b.add_provider(
        provider_name="featherless",
        provider_model_id="meta-llama/Meta-Llama-3.1-70B-Instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.60,
        output_cost=0.80,
    )
    llama_31_70b.tags.add("open-source")
    llama_31_70b.tags.add("popular")
    models.append(llama_31_70b)

    # Llama 3.1 8B
    llama_31_8b = CanonicalModel(
        id="llama-3.1-8b-instruct",
        name="Llama 3.1 8B Instruct",
        description="Efficient open model for edge deployment",
    )
    llama_31_8b.add_provider(
        provider_name="openrouter",
        provider_model_id="meta-llama/llama-3.1-8b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.06,
        output_cost=0.06,
    )
    llama_31_8b.add_provider(
        provider_name="together",
        provider_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.18,
        output_cost=0.18,
    )
    llama_31_8b.add_provider(
        provider_name="fireworks",
        provider_model_id="accounts/fireworks/models/llama-v3p1-8b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.20,
        output_cost=0.20,
    )
    llama_31_8b.add_provider(
        provider_name="deepinfra",
        provider_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.06,
        output_cost=0.06,
    )
    llama_31_8b.add_provider(
        provider_name="featherless",
        provider_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.08,
        output_cost=0.08,
    )
    llama_31_8b.add_provider(
        provider_name="huggingface",
        provider_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.10,
        output_cost=0.10,
    )
    llama_31_8b.tags.add("open-source")
    llama_31_8b.tags.add("efficient")
    llama_31_8b.tags.add("edge")
    models.append(llama_31_8b)

    return models


def get_mistral_models() -> List[CanonicalModel]:
    """
    Get Mistral AI models available across multiple providers.
    """

    models = []

    # Mistral Large
    mistral_large = CanonicalModel(
        id="mistral-large",
        name="Mistral Large",
        description="Mistral's flagship model with 128k context",
    )
    mistral_large.add_provider(
        provider_name="openrouter",
        provider_model_id="mistralai/mistral-large",
        context_length=128000,
        modalities=["text"],
        features=["streaming", "function_calling"],
        input_cost=3.00,
        output_cost=9.00,
    )
    mistral_large.add_provider(
        provider_name="portkey",
        provider_model_id="mistral-large-latest",
        context_length=128000,
        modalities=["text"],
        features=["streaming", "function_calling"],
        input_cost=3.00,
        output_cost=9.00,
    )
    mistral_large.tags.add("flagship")
    models.append(mistral_large)

    # Mixtral 8x22B
    mixtral_8x22b = CanonicalModel(
        id="mixtral-8x22b-instruct",
        name="Mixtral 8x22B Instruct",
        description="Large MoE model with excellent performance",
    )
    mixtral_8x22b.add_provider(
        provider_name="openrouter",
        provider_model_id="mistralai/mixtral-8x22b-instruct",
        context_length=65536,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.65,
        output_cost=0.65,
    )
    mixtral_8x22b.add_provider(
        provider_name="together",
        provider_model_id="mistralai/Mixtral-8x22B-Instruct-v0.1",
        context_length=65536,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.90,
        output_cost=0.90,
    )
    mixtral_8x22b.add_provider(
        provider_name="deepinfra",
        provider_model_id="mistralai/Mixtral-8x22B-Instruct-v0.1",
        context_length=65536,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.65,
        output_cost=0.65,
    )
    mixtral_8x22b.tags.add("moe")
    mixtral_8x22b.tags.add("efficient")
    models.append(mixtral_8x22b)

    # Mixtral 8x7B
    mixtral_8x7b = CanonicalModel(
        id="mixtral-8x7b-instruct",
        name="Mixtral 8x7B Instruct",
        description="Efficient MoE model for various tasks",
    )
    mixtral_8x7b.add_provider(
        provider_name="openrouter",
        provider_model_id="mistralai/mixtral-8x7b-instruct",
        context_length=32768,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.24,
        output_cost=0.24,
    )
    mixtral_8x7b.add_provider(
        provider_name="together",
        provider_model_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
        context_length=32768,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.60,
        output_cost=0.60,
    )
    mixtral_8x7b.add_provider(
        provider_name="fireworks",
        provider_model_id="accounts/fireworks/models/mixtral-8x7b-instruct",
        context_length=32768,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.50,
        output_cost=0.50,
    )
    mixtral_8x7b.add_provider(
        provider_name="deepinfra",
        provider_model_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
        context_length=32768,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.24,
        output_cost=0.24,
    )
    mixtral_8x7b.add_provider(
        provider_name="featherless",
        provider_model_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
        context_length=32768,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.30,
        output_cost=0.30,
    )
    mixtral_8x7b.tags.add("moe")
    mixtral_8x7b.tags.add("popular")
    models.append(mixtral_8x7b)

    return models


def get_qwen_models() -> List[CanonicalModel]:
    """
    Get Qwen (Alibaba) models available across multiple providers.
    """

    models = []

    # Qwen 2.5 72B
    qwen_25_72b = CanonicalModel(
        id="qwen-2.5-72b-instruct",
        name="Qwen 2.5 72B Instruct",
        description="Alibaba's powerful multilingual model",
    )
    qwen_25_72b.add_provider(
        provider_name="openrouter",
        provider_model_id="qwen/qwen-2.5-72b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.35,
        output_cost=0.40,
    )
    qwen_25_72b.add_provider(
        provider_name="together",
        provider_model_id="Qwen/Qwen2.5-72B-Instruct-Turbo",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.54,
        output_cost=0.54,
    )
    qwen_25_72b.add_provider(
        provider_name="deepinfra",
        provider_model_id="Qwen/Qwen2.5-72B-Instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.35,
        output_cost=0.40,
    )
    qwen_25_72b.add_provider(
        provider_name="fireworks",
        provider_model_id="accounts/fireworks/models/qwen2p5-72b-instruct",
        context_length=131072,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.60,
        output_cost=0.60,
    )
    qwen_25_72b.tags.add("multilingual")
    qwen_25_72b.tags.add("coding")
    models.append(qwen_25_72b)

    # QwQ 32B
    qwq_32b = CanonicalModel(
        id="qwq-32b-preview",
        name="QwQ 32B Preview",
        description="Reasoning-focused model from Alibaba",
    )
    qwq_32b.add_provider(
        provider_name="openrouter",
        provider_model_id="qwen/qwq-32b-preview",
        context_length=32768,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.12,
        output_cost=0.12,
    )
    qwq_32b.add_provider(
        provider_name="deepinfra",
        provider_model_id="Qwen/QwQ-32B-Preview",
        context_length=32768,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.12,
        output_cost=0.12,
    )
    qwq_32b.tags.add("reasoning")
    models.append(qwq_32b)

    return models


def get_deepseek_models() -> List[CanonicalModel]:
    """
    Get DeepSeek models available across multiple providers.
    """

    models = []

    # DeepSeek V2.5
    deepseek_v25 = CanonicalModel(
        id="deepseek-v2.5",
        name="DeepSeek V2.5",
        description="Efficient MoE model with strong performance",
    )
    deepseek_v25.add_provider(
        provider_name="openrouter",
        provider_model_id="deepseek/deepseek-chat",
        context_length=128000,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.14,
        output_cost=0.28,
    )
    deepseek_v25.add_provider(
        provider_name="deepinfra",
        provider_model_id="deepseek-ai/DeepSeek-V2.5",
        context_length=128000,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.14,
        output_cost=0.28,
    )
    deepseek_v25.add_provider(
        provider_name="fireworks",
        provider_model_id="accounts/fireworks/models/deepseek-v2p5",
        context_length=128000,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.30,
        output_cost=0.30,
    )
    deepseek_v25.tags.add("moe")
    deepseek_v25.tags.add("coding")
    models.append(deepseek_v25)

    # DeepSeek R1 Lite
    deepseek_r1 = CanonicalModel(
        id="deepseek-r1-lite-preview",
        name="DeepSeek R1 Lite Preview",
        description="Reasoning-optimized model from DeepSeek",
    )
    deepseek_r1.add_provider(
        provider_name="openrouter",
        provider_model_id="deepseek/deepseek-r1-lite-preview",
        context_length=128000,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.14,
        output_cost=0.28,
    )
    deepseek_r1.add_provider(
        provider_name="deepinfra",
        provider_model_id="deepseek-ai/DeepSeek-R1-Lite-Preview",
        context_length=128000,
        modalities=["text"],
        features=["streaming"],
        input_cost=0.14,
        output_cost=0.28,
    )
    deepseek_r1.tags.add("reasoning")
    models.append(deepseek_r1)

    return models


def create_model_aliases() -> Dict[str, str]:
    """
    Create common aliases for models.
    """

    aliases = {
        # GPT aliases
        "gpt4o": "gpt-4o",
        "gpt4-o": "gpt-4o",
        "gpt-4-o": "gpt-4o",
        "gpt4omini": "gpt-4o-mini",
        "gpt-4-o-mini": "gpt-4o-mini",
        "gpt35": "gpt-3.5-turbo",
        "gpt-35": "gpt-3.5-turbo",
        "gpt3.5": "gpt-3.5-turbo",

        # Claude aliases
        "claude-sonnet": "claude-3.5-sonnet",
        "claude-3-sonnet": "claude-3.5-sonnet",
        "claude-35-sonnet": "claude-3.5-sonnet",
        "claude-haiku": "claude-3-haiku",
        "claude-opus": "claude-3-opus",

        # Llama aliases
        "llama-405b": "llama-3.1-405b-instruct",
        "llama-3-405b": "llama-3.1-405b-instruct",
        "llama-70b": "llama-3.1-70b-instruct",
        "llama-3-70b": "llama-3.1-70b-instruct",
        "llama-8b": "llama-3.1-8b-instruct",
        "llama-3-8b": "llama-3.1-8b-instruct",

        # Gemini aliases (already in google_models_config)
        "gemini-flash": "gemini-2.5-flash",
        "gemini-pro": "gemini-2.5-pro",
        "gemini-2-flash": "gemini-2.0-flash",
        "gemini-15-pro": "gemini-1.5-pro",
        "gemini-15-flash": "gemini-1.5-flash",

        # Mixtral aliases
        "mixtral-large": "mixtral-8x22b-instruct",
        "mixtral": "mixtral-8x7b-instruct",
        "mixtral-8x7b": "mixtral-8x7b-instruct",
        "mixtral-8x22b": "mixtral-8x22b-instruct",

        # Qwen aliases
        "qwen-72b": "qwen-2.5-72b-instruct",
        "qwen2.5-72b": "qwen-2.5-72b-instruct",
        "qwq": "qwq-32b-preview",
        "qwq-32b": "qwq-32b-preview",

        # DeepSeek aliases
        "deepseek": "deepseek-v2.5",
        "deepseek-chat": "deepseek-v2.5",
        "deepseek-r1": "deepseek-r1-lite-preview",
        "deepseek-r1-lite": "deepseek-r1-lite-preview",
    }

    return aliases


def initialize_canonical_registry() -> None:
    """
    Initialize the canonical model registry with all configured models.

    This should be called during application startup to register all
    multi-provider models and their aliases.
    """

    registry = get_canonical_registry()

    # Register all model groups
    all_models = []
    all_models.extend(get_openai_compatible_models())
    all_models.extend(get_anthropic_models())
    all_models.extend(get_llama_models())
    all_models.extend(get_mistral_models())
    all_models.extend(get_qwen_models())
    all_models.extend(get_deepseek_models())

    logger.info(f"Initializing canonical registry with {len(all_models)} models")

    for model in all_models:
        registry.register_canonical_model(model)

    # Register aliases
    aliases = create_model_aliases()
    for alias, canonical_id in aliases.items():
        registry.add_alias(alias, canonical_id)

    logger.info(f"✓ Registered {len(aliases)} model aliases")

    # Import and register Google models
    try:
        from src.services.google_models_config import get_google_models

        google_models = get_google_models()
        for google_model in google_models:
            # Convert to CanonicalModel
            canonical = CanonicalModel(
                id=google_model.id,
                name=google_model.name,
                description=google_model.description,
            )

            for provider_config in google_model.providers:
                canonical.add_provider(
                    provider_name=provider_config.name,
                    provider_model_id=provider_config.model_id,
                    context_length=google_model.context_length,
                    modalities=google_model.modalities,
                    features=provider_config.features,
                    input_cost=provider_config.cost_per_1k_input,
                    output_cost=provider_config.cost_per_1k_output,
                )

            registry.register_canonical_model(canonical)

        logger.info(f"✓ Registered {len(google_models)} Google models")
    except ImportError:
        logger.warning("Google models config not found, skipping")

    logger.info(
        f"✓ Successfully initialized canonical registry with "
        f"{len(registry.get_all_canonical_models())} models"
    )