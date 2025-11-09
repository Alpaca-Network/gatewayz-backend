"""
Canonical Model Registry

This module provides a centralized registry that aggregates model metadata
from all providers and serves as the source of truth for model routing,
catalog responses, and provider selection.

Key features:
- Aggregates model metadata from 17+ providers
- Canonical model IDs with provider-specific mappings
- Real-time sync with provider catalogs
- Intelligent provider selection and failover
- Unified model catalog responses
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import time

from src.services.models import get_cached_models
from src.services.multi_provider_registry import get_registry, MultiProviderModel
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)


@dataclass
class ProviderModelInfo:
    """Model information from a specific provider"""
    
    provider: str
    model_id: str  # Provider-specific model ID
    canonical_id: str  # Canonical model ID
    pricing: Optional[Dict[str, str]] = None
    context_length: Optional[int] = None
    description: Optional[str] = None
    features: List[str] = field(default_factory=list)
    enabled: bool = True
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalModel:
    """Canonical model entry aggregating information from all providers"""
    
    id: str  # Canonical model ID (what users specify)
    name: str  # Display name
    description: Optional[str] = None
    context_length: Optional[int] = None
    modalities: List[str] = field(default_factory=lambda: ["text"])
    providers: Dict[str, ProviderModelInfo] = field(default_factory=dict)  # provider -> info
    primary_provider: Optional[str] = None  # Default provider for routing
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def add_provider(self, provider_info: ProviderModelInfo) -> None:
        """Add or update provider information for this model"""
        self.providers[provider_info.provider] = provider_info
        self.updated_at = datetime.now(timezone.utc)
        
        # Set primary provider if not set or if this is higher priority
        if (self.primary_provider is None or 
            (provider_info.provider == "google-vertex" and self.primary_provider != "google-vertex") or
            (provider_info.provider == "openrouter" and self.primary_provider not in ["google-vertex", "openrouter"])):
            self.primary_provider = provider_info.provider
    
    def get_enabled_providers(self) -> List[ProviderModelInfo]:
        """Get list of enabled providers, sorted by priority"""
        enabled = [p for p in self.providers.values() if p.enabled]
        # Sort by provider priority (google-vertex > openrouter > others)
        priority_order = ["google-vertex", "openrouter", "fireworks", "together", "huggingface"]
        
        def get_priority(provider_info: ProviderModelInfo) -> int:
            try:
                return priority_order.index(provider_info.provider)
            except ValueError:
                return len(priority_order)
        
        return sorted(enabled, key=get_priority)
    
    def get_best_provider(self) -> Optional[ProviderModelInfo]:
        """Get the best available provider for this model"""
        enabled = self.get_enabled_providers()
        return enabled[0] if enabled else None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for API responses"""
        best_provider = self.get_best_provider()
        
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "context_length": self.context_length,
            "modalities": self.modalities,
            "providers": list(self.providers.keys()),
            "primary_provider": self.primary_provider,
            "best_provider": best_provider.provider if best_provider else None,
            "pricing": best_provider.pricing if best_provider else None,
            "features": best_provider.features if best_provider else [],
            "tags": self.tags,
            "provider_count": len(self.providers),
            "enabled_providers": len([p for p in self.providers.values() if p.enabled]),
            "updated_at": self.updated_at.isoformat(),
        }


class CanonicalModelRegistry:
    """
    Canonical registry that aggregates model information from all providers.
    
    This registry serves as the single source of truth for:
    - Model catalog responses
    - Provider selection and routing
    - Model ID transformations
    - Availability monitoring
    """
    
    def __init__(self):
        self._models: Dict[str, CanonicalModel] = {}  # canonical_id -> CanonicalModel
        self._provider_index: Dict[str, Dict[str, str]] = {}  # provider -> provider_id -> canonical_id
        self._sync_lock = asyncio.Lock()
        self._last_sync: Optional[datetime] = None
        self._sync_interval = 300  # 5 minutes
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="registry-sync")
        
        logger.info("Initialized CanonicalModelRegistry")
    
    async def initialize(self) -> None:
        """Initialize the registry by syncing with all providers"""
        logger.info("Initializing canonical model registry...")
        await self.sync_all_providers()
        logger.info(f"Registry initialized with {len(self._models)} models")
    
    async def sync_all_providers(self) -> None:
        """Sync model data from all providers"""
        async with self._sync_lock:
            start_time = time.time()
            
            try:
                # Get all provider models in parallel
                providers = [
                    "openrouter", "portkey", "featherless", "deepinfra", 
                    "cerebras", "nebius", "xai", "novita", "huggingface",
                    "chutes", "groq", "fireworks", "together", "aimo",
                    "near", "fal", "vercel-ai-gateway", "anannas"
                ]
                
                tasks = []
                for provider in providers:
                    task = asyncio.create_task(self._sync_provider(provider))
                    tasks.append(task)
                
                # Wait for all provider syncs to complete
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Process results
                successful_syncs = 0
                for i, result in enumerate(results):
                    provider = providers[i]
                    if isinstance(result, Exception):
                        logger.error(f"Failed to sync {provider}: {result}")
                    else:
                        successful_syncs += 1
                        logger.debug(f"Synced {result} models from {provider}")
                
                # Merge multi-provider models
                await self._merge_multi_provider_models()
                
                self._last_sync = datetime.now(timezone.utc)
                elapsed = time.time() - start_time
                
                logger.info(
                    f"Provider sync completed: {successful_syncs}/{len(providers)} providers, "
                    f"{len(self._models)} total models, {elapsed:.2f}s"
                )
                
            except Exception as e:
                logger.error(f"Error during provider sync: {e}", exc_info=True)
                raise
    
    async def _sync_provider(self, provider: str) -> int:
        """Sync models from a specific provider"""
        try:
            # Run provider fetch in thread pool to avoid blocking
            models = await asyncio.get_event_loop().run_in_executor(
                self._executor, get_cached_models, provider
            )
            
            if not models:
                logger.debug(f"No models returned from {provider}")
                return 0
            
            synced_count = 0
            for model_data in models:
                try:
                    await self._process_provider_model(provider, model_data)
                    synced_count += 1
                except Exception as e:
                    logger.warning(f"Error processing model from {provider}: {e}")
            
            logger.debug(f"Synced {synced_count} models from {provider}")
            return synced_count
            
        except Exception as e:
            logger.error(f"Failed to sync {provider}: {e}")
            raise
    
    async def _process_provider_model(self, provider: str, model_data: Dict[str, Any]) -> None:
        """Process a single model from a provider"""
        provider_model_id = model_data.get("id")
        if not provider_model_id:
            return
        
        # Generate canonical ID
        canonical_id = self._generate_canonical_id(provider, provider_model_id, model_data)
        
        # Extract model information
        pricing = model_data.get("pricing", {})
        context_length = model_data.get("context_length")
        description = model_data.get("description")
        architecture = model_data.get("architecture", {})
        modalities = architecture.get("input_modalities", ["text"])
        
        # Determine features
        features = []
        if model_data.get("supported_parameters"):
            features.extend(model_data["supported_parameters"])
        if "streaming" in str(model_data).lower():
            features.append("streaming")
        if "function_calling" in str(model_data).lower():
            features.append("function_calling")
        if "multimodal" in str(model_data).lower() or len(modalities) > 1:
            features.append("multimodal")
        
        # Create provider info
        provider_info = ProviderModelInfo(
            provider=provider,
            model_id=provider_model_id,
            canonical_id=canonical_id,
            pricing=pricing,
            context_length=context_length,
            description=description,
            features=list(set(features)),  # Deduplicate
            metadata=model_data
        )
        
        # Add or update canonical model
        if canonical_id not in self._models:
            # Create new canonical model
            name = model_data.get("name", canonical_id)
            if "/" in name:
                name = name.split("/")[-1]  # Use last part as display name
            
            canonical_model = CanonicalModel(
                id=canonical_id,
                name=name,
                description=description,
                context_length=context_length,
                modalities=modalities
            )
            self._models[canonical_id] = canonical_model
        
        # Add provider info
        self._models[canonical_id].add_provider(provider_info)
        
        # Update provider index
        if provider not in self._provider_index:
            self._provider_index[provider] = {}
        self._provider_index[provider][provider_model_id] = canonical_id
    
    def _generate_canonical_id(self, provider: str, provider_model_id: str, model_data: Dict[str, Any]) -> str:
        """Generate a canonical model ID from provider-specific ID"""
        
        # Check if there's an existing multi-provider model for this
        multi_provider_registry = get_registry()
        
        # First check if this provider_model_id matches any registered multi-provider model
        for model in multi_provider_registry.get_all_models():
            for provider_config in model.providers:
                if provider_config.name == provider and provider_config.model_id == provider_model_id:
                    return model.id
        
        # Generate canonical ID based on provider patterns
        if provider == "openrouter":
            # OpenRouter uses org/model format, which is often canonical already
            return provider_model_id.lower()
        
        elif provider == "google-vertex":
            # Vertex AI uses simple names like "gemini-2.5-flash"
            return provider_model_id.lower()
        
        elif provider == "fireworks":
            # Convert "accounts/fireworks/models/xxx" to "org/xxx"
            if provider_model_id.startswith("accounts/fireworks/models/"):
                model_name = provider_model_id.replace("accounts/fireworks/models/", "")
                # Try to guess the org based on model name
                if "deepseek" in model_name.lower():
                    return f"deepseek-ai/{model_name}"
                elif "llama" in model_name.lower():
                    return f"meta-llama/{model_name}"
                elif "qwen" in model_name.lower():
                    return f"qwen/{model_name}"
                else:
                    return model_name
        
        elif provider == "huggingface":
            # HuggingFace uses org/model format
            return provider_model_id.lower()
        
        elif provider in ["together", "featherless"]:
            # These often use org/model format
            if "/" in provider_model_id:
                return provider_model_id.lower()
            else:
                # Try to extract from name or description
                name = model_data.get("name", "")
                if "/" in name:
                    return name.lower()
                return f"{provider}/{provider_model_id}".lower()
        
        # Fallback: use provider/model format
        return f"{provider}/{provider_model_id}".lower()
    
    async def _merge_multi_provider_models(self) -> None:
        """Merge models from the multi-provider registry"""
        try:
            multi_registry = get_registry()
            multi_models = multi_registry.get_all_models()
            
            merged_count = 0
            for multi_model in multi_models:
                if multi_model.id in self._models:
                    # Update existing model with multi-provider info
                    canonical_model = self._models[multi_model.id]
                    
                    # Update description and context length if better
                    if multi_model.description:
                        canonical_model.description = multi_model.description
                    if multi_model.context_length:
                        canonical_model.context_length = multi_model.context_length
                    
                    # Mark as multi-provider
                    if "multi-provider" not in canonical_model.tags:
                        canonical_model.tags.append("multi-provider")
                    
                    merged_count += 1
                else:
                    # Create new canonical model from multi-provider config
                    canonical_model = CanonicalModel(
                        id=multi_model.id,
                        name=multi_model.name,
                        description=multi_model.description,
                        context_length=multi_model.context_length,
                        modalities=multi_model.modalities,
                        tags=["multi-provider"]
                    )
                    
                    # Add all providers from multi-provider config
                    for provider_config in multi_model.providers:
                        provider_info = ProviderModelInfo(
                            provider=provider_config.name,
                            model_id=provider_config.model_id,
                            canonical_id=multi_model.id,
                            cost_per_1k_input=provider_config.cost_per_1k_input,
                            cost_per_1k_output=provider_config.cost_per_1k_output,
                            max_tokens=provider_config.max_tokens,
                            features=provider_config.features,
                            enabled=provider_config.enabled
                        )
                        canonical_model.add_provider(provider_info)
                        
                        # Update provider index
                        if provider_config.name not in self._provider_index:
                            self._provider_index[provider_config.name] = {}
                        self._provider_index[provider_config.name][provider_config.model_id] = multi_model.id
                    
                    self._models[multi_model.id] = canonical_model
                    merged_count += 1
            
            logger.debug(f"Merged {merged_count} multi-provider models")
            
        except Exception as e:
            logger.warning(f"Error merging multi-provider models: {e}")
    
    def get_model(self, canonical_id: str) -> Optional[CanonicalModel]:
        """Get a canonical model by ID"""
        return self._models.get(canonical_id)
    
    def find_model_by_provider_id(self, provider: str, provider_model_id: str) -> Optional[CanonicalModel]:
        """Find a canonical model by provider-specific ID"""
        if provider not in self._provider_index:
            return None
        
        canonical_id = self._provider_index[provider].get(provider_model_id)
        if canonical_id:
            return self._models.get(canonical_id)
        
        return None
    
    def search_models(self, query: str = None, provider: str = None, 
                     modality: str = None, limit: int = 100) -> List[CanonicalModel]:
        """Search for models with optional filters"""
        models = list(self._models.values())
        
        # Filter by provider
        if provider:
            models = [m for m in models if provider in m.providers]
        
        # Filter by modality
        if modality:
            models = [m for m in models if modality in m.modalities]
        
        # Filter by query (search in name, description, ID)
        if query:
            query_lower = query.lower()
            models = [m for m in models 
                     if query_lower in m.id.lower() or 
                     query_lower in m.name.lower() or
                     (m.description and query_lower in m.description.lower())]
        
        # Sort by name and limit
        models.sort(key=lambda m: m.name.lower())
        return models[:limit]
    
    def get_all_models(self) -> List[CanonicalModel]:
        """Get all canonical models"""
        return list(self._models.values())
    
    def get_providers_for_model(self, canonical_id: str) -> List[ProviderModelInfo]:
        """Get all available providers for a model"""
        model = self.get_model(canonical_id)
        return model.get_enabled_providers() if model else []
    
    def get_best_provider_for_model(self, canonical_id: str) -> Optional[ProviderModelInfo]:
        """Get the best available provider for a model"""
        model = self.get_model(canonical_id)
        return model.get_best_provider() if model else None
    
    def transform_to_provider_id(self, canonical_id: str, provider: str) -> Optional[str]:
        """Transform canonical ID to provider-specific ID"""
        model = self.get_model(canonical_id)
        if not model:
            return None
        
        provider_info = model.providers.get(provider)
        return provider_info.model_id if provider_info else None
    
    def get_catalog_response(self, provider: str = None, format: str = "simple") -> List[Dict[str, Any]]:
        """Get models in catalog API format"""
        models = self.get_all_models()
        
        if provider:
            models = [m for m in models if provider in m.providers]
        
        if format == "simple":
            # Simple format for basic catalog
            return [
                {
                    "id": model.id,
                    "name": model.name,
                    "description": model.description,
                    "context_length": model.context_length,
                    "modalities": model.modalities,
                    "providers": list(model.providers.keys()),
                    "best_provider": model.primary_provider,
                }
                for model in models
            ]
        else:
            # Full format with all details
            return [model.to_dict() for model in models]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics"""
        total_models = len(self._models)
        multi_provider_models = len([m for m in self._models.values() if len(m.providers) > 1])
        
        provider_counts = {}
        for model in self._models.values():
            for provider in model.providers.keys():
                provider_counts[provider] = provider_counts.get(provider, 0) + 1
        
        return {
            "total_models": total_models,
            "multi_provider_models": multi_provider_models,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "provider_counts": provider_counts,
            "providers": list(provider_counts.keys()),
        }
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check for the registry"""
        now = datetime.now(timezone.utc)
        sync_age = None
        
        if self._last_sync:
            sync_age = (now - self._last_sync).total_seconds()
        
        status = "healthy"
        if sync_age and sync_age > self._sync_interval * 2:
            status = "stale"
        elif not self._last_sync:
            status = "not_initialized"
        
        return {
            "status": status,
            "models_count": len(self._models),
            "providers_count": len(self._provider_index),
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "sync_age_seconds": sync_age,
            "sync_interval": self._sync_interval,
        }


# Global registry instance
_registry: Optional[CanonicalModelRegistry] = None


def get_canonical_registry() -> CanonicalModelRegistry:
    """Get the global canonical model registry instance"""
    global _registry
    if _registry is None:
        _registry = CanonicalModelRegistry()
    return _registry


async def initialize_canonical_registry() -> None:
    """Initialize the global canonical registry"""
    registry = get_canonical_registry()
    await registry.initialize()