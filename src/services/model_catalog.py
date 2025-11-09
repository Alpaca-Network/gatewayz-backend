"""
Model Catalog Service

This service provides a canonical view of all available models across providers,
aggregating metadata and enabling intelligent model discovery and selection.

The catalog integrates with the multi-provider registry to maintain a unified
view of logical models and their provider implementations.
"""

import logging
from typing import List, Optional, Dict, Any, Set
from collections import defaultdict

from src.services.multi_provider_registry import get_registry, MultiProviderModel
from src.services.models import get_cached_models

logger = logging.getLogger(__name__)


class ModelCatalog:
    """
    Service for managing and querying a catalog of models across all providers.
    
    This service aggregates model information from all available providers and
    maintains a canonical view of logical models with their provider implementations.
    It serves as the source of truth for model discovery and selection.
    """

    def __init__(self):
        self.registry = get_registry()
        logger.info("Initialized ModelCatalog service")

    def refresh_catalog(self) -> Dict[str, Any]:
        """
        Refresh the model catalog by fetching models from all providers.
        
        Returns:
            Dictionary with refresh statistics
        """
        logger.info("Refreshing model catalog from all providers")
        
        # Get all cached models from each provider
        providers = [
            "openrouter", "portkey", "featherless", "deepinfra", "cerebras",
            "nebius", "xai", "novita", "hug", "chutes", "groq", "fireworks",
            "together", "aimo", "near", "fal", "anannas", "google-vertex"
        ]
        
        stats = {
            "providers_queried": 0,
            "models_found": 0,
            "models_registered": 0,
            "providers_with_errors": []
        }
        
        for provider in providers:
            try:
                models = get_cached_models(provider)
                if models:
                    stats["providers_queried"] += 1
                    stats["models_found"] += len(models)
                    logger.debug(f"Found {len(models)} models from {provider}")
                else:
                    logger.debug(f"No models found from {provider}")
            except Exception as e:
                logger.warning(f"Error fetching models from {provider}: {e}")
                stats["providers_with_errors"].append(provider)
                
        logger.info(
            f"Catalog refresh complete: {stats['models_found']} models from "
            f"{stats['providers_queried']} providers"
        )
        return stats

    def get_all_models(self) -> List[MultiProviderModel]:
        """
        Get all models from the multi-provider registry.
        
        Returns:
            List of all registered multi-provider models
        """
        return self.registry.get_all_models()

    def search_models(
        self, 
        query: Optional[str] = None,
        provider: Optional[str] = None,
        category: Optional[str] = None,
        capability: Optional[str] = None,
        tag: Optional[str] = None
    ) -> List[MultiProviderModel]:
        """
        Search for models based on various criteria.
        
        Args:
            query: Text to search in model ID, name, or description
            provider: Filter by specific provider
            category: Filter by model category
            capability: Filter by model capability
            tag: Filter by model tag
            
        Returns:
            List of matching models
        """
        models = self.registry.get_all_models()
        
        if provider:
            models = self.registry.get_models_by_provider(provider)
            
        if category:
            models = [m for m in models if category in m.categories]
            
        if capability:
            models = [m for m in models if capability in m.capabilities]
            
        if tag:
            models = [m for m in models if tag in m.tags]
            
        if query:
            query = query.lower()
            models = [
                m for m in models
                if query in m.id.lower() or 
                   query in m.name.lower() or 
                   (m.description and query in m.description.lower())
            ]
            
        return models

    def get_model_details(self, model_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific model.
        
        Args:
            model_id: The model ID to get details for
            
        Returns:
            Dictionary with model details or None if not found
        """
        model = self.registry.get_model(model_id)
        if not model:
            return None
            
        return {
            "id": model.id,
            "name": model.name,
            "description": model.description,
            "context_length": model.context_length,
            "modalities": model.modalities,
            "categories": model.categories,
            "capabilities": model.capabilities,
            "tags": model.tags,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
            "providers": [
                {
                    "name": p.name,
                    "model_id": p.model_id,
                    "priority": p.priority,
                    "cost_per_1k_input": p.cost_per_1k_input,
                    "cost_per_1k_output": p.cost_per_1k_output,
                    "enabled": p.enabled,
                    "availability": p.availability,
                    "max_tokens": p.max_tokens,
                    "features": p.features,
                    "response_time_ms": p.response_time_ms,
                    "last_checked": p.last_checked,
                }
                for p in model.providers
            ]
        }

    def get_provider_summary(self) -> Dict[str, Dict[str, Any]]:
        """
        Get a summary of all providers and their model counts.
        
        Returns:
            Dictionary mapping provider names to summary statistics
        """
        all_models = self.registry.get_all_models()
        provider_stats = defaultdict(lambda: {
            "model_count": 0,
            "total_priority": 0,
            "available_models": 0,
            "enabled_models": 0,
        })
        
        for model in all_models:
            for provider in model.providers:
                stats = provider_stats[provider.name]
                stats["model_count"] += 1
                stats["total_priority"] += provider.priority
                if provider.availability:
                    stats["available_models"] += 1
                if provider.enabled:
                    stats["enabled_models"] += 1
                    
        # Calculate average priority
        for provider_name, stats in provider_stats.items():
            if stats["model_count"] > 0:
                stats["avg_priority"] = stats["total_priority"] / stats["model_count"]
            else:
                stats["avg_priority"] = 0
            del stats["total_priority"]  # Remove temporary field
            
        return dict(provider_stats)

    def get_model_categories(self) -> List[str]:
        """
        Get all unique model categories.
        
        Returns:
            List of all unique categories across all models
        """
        categories: Set[str] = set()
        for model in self.registry.get_all_models():
            categories.update(model.categories)
        return sorted(list(categories))

    def get_model_capabilities(self) -> List[str]:
        """
        Get all unique model capabilities.
        
        Returns:
            List of all unique capabilities across all models
        """
        capabilities: Set[str] = set()
        for model in self.registry.get_all_models():
            capabilities.update(model.capabilities)
        return sorted(list(capabilities))

    def get_model_tags(self) -> List[str]:
        """
        Get all unique model tags.
        
        Returns:
            List of all unique tags across all models
        """
        tags: Set[str] = set()
        for model in self.registry.get_all_models():
            tags.update(model.tags)
        return sorted(list(tags))


# Global catalog instance
_catalog = ModelCatalog()


def get_catalog() -> ModelCatalog:
    """Get the global model catalog instance"""
    return _catalog