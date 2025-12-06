"""Nebius AI client for API integration.

This module provides integration with Nebius AI models.
"""

import logging

# Initialize logging
logger = logging.getLogger(__name__)


def fetch_models_from_nebius():
    """Fetch models from Nebius API

    Nebius does not provide a public API to list available models.
    Returns a hardcoded list of known Nebius AI Studio models instead.
    """
    logger.info("Nebius does not provide a public model listing API, returning known models")

    # Hardcoded list of known Nebius AI Studio models
    # Based on https://nebius.com/prices-ai-studio
    return [
        {
            "id": "gpt-oss-120b",
            "slug": "gpt-oss-120b",
            "canonical_slug": "gpt-oss-120b",
            "name": "GPT OSS 120B",
            "description": "Open-weight 117B-parameter Mixture-of-Experts model from OpenAI that activates 5.1B parameters per forward pass, optimized to run on a single H100 GPU",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.15",
                "completion": "0.60",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "gpt-oss-20b",
            "slug": "gpt-oss-20b",
            "canonical_slug": "gpt-oss-20b",
            "name": "GPT OSS 20B",
            "description": "Open-weight 21B parameter model from OpenAI using a Mixture-of-Experts architecture with 3.6B active parameters per forward pass",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.05",
                "completion": "0.20",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "meta-llama/Llama-3.1-405B-Instruct",
            "slug": "meta-llama-llama-3-1-405b-instruct",
            "canonical_slug": "llama-3.1-405b-instruct",
            "name": "Llama 3.1 405B Instruct",
            "description": "Meta's flagship 405B parameter instruction-tuned model with exceptional capabilities across reasoning, coding, and multilingual tasks",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "1.00",
                "completion": "3.00",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "NousResearch/Hermes-3-Llama-3.1-405B",
            "slug": "nousresearch-hermes-3-llama-3-1-405b",
            "canonical_slug": "hermes-3-llama-3.1-405b",
            "name": "Hermes 3 Llama 3.1 405B",
            "description": "Nous Research's Hermes 3 built on Llama 3.1 405B with enhanced instruction following and reasoning capabilities",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "1.00",
                "completion": "3.00",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "deepseek-ai/DeepSeek-V3",
            "slug": "deepseek-ai-deepseek-v3",
            "canonical_slug": "deepseek-v3",
            "name": "DeepSeek V3",
            "description": "DeepSeek's V3 model with strong reasoning and coding capabilities",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.50",
                "completion": "1.50",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "deepseek-ai/DeepSeek-R1-0528",
            "slug": "deepseek-ai-deepseek-r1-0528",
            "canonical_slug": "deepseek-r1",
            "name": "DeepSeek R1",
            "description": "DeepSeek's reasoning model with chain-of-thought capabilities",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.80",
                "completion": "2.40",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "Qwen/QwQ-32B",
            "slug": "qwen-qwq-32b",
            "canonical_slug": "qwq-32b",
            "name": "QwQ 32B",
            "description": "Qwen's reasoning-focused 32B model with strong analytical capabilities",
            "context_length": 32768,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.15",
                "completion": "0.45",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "meta-llama/Llama-3.1-8B-Instruct",
            "slug": "meta-llama-llama-3-1-8b-instruct",
            "canonical_slug": "llama-3.1-8b-instruct",
            "name": "Llama 3.1 8B Instruct",
            "description": "Meta's efficient 8B parameter instruction-tuned model",
            "context_length": 128000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.02",
                "completion": "0.06",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "slug": "qwen-qwen2-5-coder-7b-instruct",
            "canonical_slug": "qwen2.5-coder-7b",
            "name": "Qwen 2.5 Coder 7B",
            "description": "Qwen's specialized coding model with 7B parameters",
            "context_length": 32768,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.03",
                "completion": "0.09",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
        {
            "id": "google/gemma-2-2b-it",
            "slug": "google-gemma-2-2b-it",
            "canonical_slug": "gemma-2-2b-it",
            "name": "Gemma 2 2B Instruct",
            "description": "Google's efficient 2B parameter instruction-tuned model",
            "context_length": 8192,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {
                "prompt": "0.02",
                "completion": "0.06",
                "request": "0",
                "image": "0",
            },
            "provider_slug": "nebius",
            "source_gateway": "nebius",
        },
    ]
