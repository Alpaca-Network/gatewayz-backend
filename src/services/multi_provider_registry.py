"""
Canonical Multi-Provider Model Registry
=======================================

This module aggregates provider catalogs into a single canonical registry so that
each logical model (e.g., "google/gemini-2.5-flash") knows every provider that can
serve it along with costs, features, and availability. The registry keeps provider
fetchers in sync, exposes helper APIs for routing, and powers provider selection
with circuit breaking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# Default provider ordering. Lower index == higher priority.
DEFAULT_PROVIDER_PRIORITY: Sequence[str] = (
    "google-vertex",
    "openrouter",
    "vercel-ai-gateway",
    "portkey",
    "featherless",
    "huggingface",
    "hug",
    "fireworks",
    "together",
    "aihubmix",
    "anannas",
    "fal",
    "near",
    "aimo",
    "cerebras",
    "deepinfra",
    "groq",
    "novita",
    "xai",
    "nebius",
    "chutes",
)


def _utcnow() -> datetime:
    """Return a timezone-aware utcnow."""
    return datetime.now(timezone.utc)


@dataclass
class ProviderConfig:
    """Configuration + runtime metadata for a single provider of a model."""

    name: str
    model_id: str
    priority: int = 1
    requires_credentials: bool = False
    cost_per_1k_input: Optional[float] = None
    cost_per_1k_output: Optional[float] = None
    enabled: bool = True
    max_tokens: Optional[int] = None
    features: List[str] = field(default_factory=list)
    availability: str = "unknown"
    last_synced: datetime = field(default_factory=_utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.priority < 1:
            raise ValueError(f"Priority must be >= 1, got {self.priority}")


@dataclass
class MultiProviderModel:
    """Canonical model representation shared across providers."""

    id: str
    name: str
    providers: List[ProviderConfig]
    description: Optional[str] = None
    context_length: Optional[int] = None
    modalities: List[str] = field(default_factory=lambda: ["text"])
    aliases: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)
    pricing: Dict[str, Any] = field(default_factory=dict)
    last_updated: datetime = field(default_factory=_utcnow)
    source_models: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.providers:
            self.providers.sort(key=lambda p: p.priority)
            logger.debug(
                "Model %s configured with providers: %s",
                self.id,
                [p.name for p in self.providers],
            )

    def get_enabled_providers(self) -> List[ProviderConfig]:
        return [p for p in self.providers if p.enabled]

    def get_primary_provider(self) -> Optional[ProviderConfig]:
        enabled = self.get_enabled_providers()
        return enabled[0] if enabled else None

    def get_provider_by_name(self, name: str) -> Optional[ProviderConfig]:
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None

    def supports_provider(self, provider_name: str) -> bool:
        return any(p.name == provider_name and p.enabled for p in self.providers)

    def register_aliases(self, *aliases: str) -> None:
        for alias in aliases:
            if alias:
                self.aliases.add(alias)

    def upsert_provider(self, provider: ProviderConfig) -> None:
        replaced = False
        for idx, existing in enumerate(self.providers):
            if existing.name == provider.name:
                self.providers[idx] = provider
                replaced = True
                break
        if not replaced:
            self.providers.append(provider)
        self.providers.sort(key=lambda p: p.priority)
        self.last_updated = _utcnow()

    def to_catalog_entry(self) -> Dict[str, Any]:
        primary = self.get_primary_provider()
        source_gateway = primary.name if primary else None
        providers_payload = [
            {
                "provider": p.name,
                "model_id": p.model_id,
                "priority": p.priority,
                "cost_per_1k_input": p.cost_per_1k_input,
                "cost_per_1k_output": p.cost_per_1k_output,
                "max_tokens": p.max_tokens,
                "features": p.features,
                "enabled": p.enabled,
                "availability": p.availability,
            }
            for p in self.providers
        ]
        return {
            "id": self.id,
             "slug": self.id,
             "canonical_slug": self.id,
            "name": self.name,
            "description": self.description,
            "context_length": self.context_length,
            "modalities": self.modalities,
            "aliases": sorted(self.aliases),
            "providers": providers_payload,
            "metadata": self.metadata,
            "pricing": self.pricing,
            "last_updated": self.last_updated.isoformat(),
            "source_gateway": source_gateway,
            "source_gateways": [p["provider"] for p in providers_payload],
        }


class MultiProviderRegistry:
    """
    Registry for multi-provider models with canonical aggregation, alias mapping,
    and helper utilities for provider selection + failover.
    """

    def __init__(self) -> None:
        self._models: Dict[str, MultiProviderModel] = {}
        self._alias_index: Dict[str, str] = {}
        logger.info("Initialized MultiProviderRegistry")

    # ------------------------------------------------------------------ #
    # Canonical registration and lookup
    # ------------------------------------------------------------------ #

    def register_model(self, model: MultiProviderModel) -> None:
        key = self._normalize_key(model.id)
        self._models[key] = model
        self._index_model(model)
        logger.info(
            "Registered multi-provider model %s with %d providers",
            model.id,
            len(model.providers),
        )

    def register_models(self, models: Iterable[MultiProviderModel]) -> None:
        for model in models:
            self.register_model(model)

    def get_model(self, model_id: str) -> Optional[MultiProviderModel]:
        if not model_id:
            return None
        key = self._normalize_key(model_id)
        model = self._models.get(key)
        if model:
            return model
        alias_target = self._alias_index.get(key)
        if alias_target:
            return self._models.get(alias_target)
        return None

    def has_model(self, model_id: str) -> bool:
        return self.get_model(model_id) is not None

    def get_all_models(self) -> List[MultiProviderModel]:
        return list(self._models.values())

    def resolve_canonical_id(self, candidate_id: str) -> Optional[str]:
        if not candidate_id:
            return None
        key = self._normalize_key(candidate_id)
        if key in self._models:
            return self._models[key].id
        alias_target = self._alias_index.get(key)
        if alias_target:
            model = self._models.get(alias_target)
            return model.id if model else None
        return None

    # ------------------------------------------------------------------ #
    # Provider catalog ingestion
    # ------------------------------------------------------------------ #

    def sync_provider_catalog(
        self,
        provider_name: str,
        models: Optional[List[Dict[str, Any]]],
    ) -> None:
        if models is None:
            logger.warning(
                "Skipping canonical sync for provider %s because payload is None",
                provider_name,
            )
            return

        ingested: Set[str] = set()
        for payload in models:
            canonical_id = self._derive_canonical_id(provider_name, payload)
            model = self._upsert_canonical_model(canonical_id, payload)
            provider_cfg = self._build_provider_config(provider_name, payload)
            model.upsert_provider(provider_cfg)
            model.source_models[provider_name] = payload
            self._index_aliases(model, provider_name, payload)
            ingested.add(self._normalize_key(model.id))

        logger.info(
            "Canonical registry ingested %d models for provider %s",
            len(ingested),
            provider_name,
        )

    def get_catalog_snapshot(self) -> List[Dict[str, Any]]:
        return [model.to_catalog_entry() for model in self._models.values()]

    # ------------------------------------------------------------------ #
    # Provider selection
    # ------------------------------------------------------------------ #

    def select_provider(
        self,
        model_id: str,
        preferred_provider: Optional[str] = None,
        required_features: Optional[List[str]] = None,
        max_cost: Optional[float] = None,
    ) -> Optional[ProviderConfig]:
        canonical_id = self.resolve_canonical_id(model_id) or model_id
        model = self.get_model(canonical_id)
        if not model:
            logger.warning("Model %s not found in multi-provider registry", model_id)
            return None

        candidates = model.get_enabled_providers()
        if not candidates:
            logger.error("No enabled providers remain for %s", model_id)
            return None

        if required_features:
            candidates = [
                p
                for p in candidates
                if all(feature in p.features for feature in required_features)
            ]
            if not candidates:
                logger.warning(
                    "No providers for %s satisfy required features %s",
                    model_id,
                    required_features,
                )
                return None

        if max_cost is not None:
            candidates = [
                p
                for p in candidates
                if (p.cost_per_1k_input is None or p.cost_per_1k_input <= max_cost)
            ]
            if not candidates:
                logger.warning(
                    "No providers for %s within cost limit %s",
                    model_id,
                    max_cost,
                )
                return None

        if preferred_provider:
            for provider in candidates:
                if provider.name == preferred_provider:
                    logger.info(
                        "Selected preferred provider %s for %s",
                        preferred_provider,
                        model_id,
                    )
                    return provider
            logger.warning(
                "Preferred provider %s unavailable for %s; falling back to priority order",
                preferred_provider,
                model_id,
            )

        selected = candidates[0]
        logger.info(
            "Selected provider %s (priority %d) for %s",
            selected.name,
            selected.priority,
            model_id,
        )
        return selected

    def get_fallback_providers(
        self,
        model_id: str,
        exclude_provider: Optional[str] = None,
    ) -> List[ProviderConfig]:
        canonical_id = self.resolve_canonical_id(model_id) or model_id
        model = self.get_model(canonical_id)
        if not model:
            return []

        providers = model.get_enabled_providers()
        if exclude_provider:
            providers = [p for p in providers if p.name != exclude_provider]
        return providers

    def disable_provider(self, model_id: str, provider_name: str) -> bool:
        model = self.get_model(model_id)
        if not model:
            return False

        provider = model.get_provider_by_name(provider_name)
        if provider:
            provider.enabled = False
            logger.warning("Disabled provider %s for %s", provider_name, model_id)
            return True
        return False

    def enable_provider(self, model_id: str, provider_name: str) -> bool:
        model = self.get_model(model_id)
        if not model:
            return False

        provider = model.get_provider_by_name(provider_name)
        if provider:
            provider.enabled = True
            logger.info("Enabled provider %s for %s", provider_name, model_id)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _normalize_key(self, value: str) -> str:
        return value.lower()

    def _derive_canonical_id(self, provider_name: str, payload: Dict[str, Any]) -> str:
        candidates = [
            payload.get("canonical_slug"),
            payload.get("slug"),
            payload.get("id"),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        fallback = f"{provider_name}:{payload.get('id', 'unknown-model')}"
        logger.debug(
            "Synthesized canonical id %s for provider %s because payload was missing slug metadata",
            fallback,
            provider_name,
        )
        return fallback

    def _upsert_canonical_model(
        self,
        canonical_id: str,
        payload: Dict[str, Any],
    ) -> MultiProviderModel:
        key = self._normalize_key(canonical_id)
        model = self._models.get(key)
        if not model:
            model = MultiProviderModel(
                id=canonical_id,
                name=payload.get("name")
                or payload.get("display_name")
                or canonical_id,
                description=payload.get("description"),
                context_length=payload.get("context_length")
                or payload.get("max_context_length")
                or payload.get("max_tokens"),
                modalities=_coerce_modalities(payload),
                providers=[],
            )
            self._models[key] = model
        else:
            model.name = payload.get("name") or model.name
            model.description = payload.get("description") or model.description
            model.context_length = payload.get("context_length") or model.context_length
            modalities = _coerce_modalities(payload)
            if modalities:
                model.modalities = modalities

        model.register_aliases(canonical_id, payload.get("id"), payload.get("slug"))
        pricing = payload.get("pricing")
        if pricing:
            model.pricing = pricing
        model.metadata.update({"provider_count": len(model.providers)})
        model.last_updated = _utcnow()
        return model

    def _build_provider_config(self, provider_name: str, payload: Dict[str, Any]) -> ProviderConfig:
        pricing = payload.get("pricing") or {}
        prompt_cost = _safe_float(pricing.get("prompt"))
        completion_cost = _safe_float(pricing.get("completion"))
        max_tokens = (
            payload.get("max_tokens")
            or payload.get("context_length")
            or payload.get("max_context_length")
        )
        features = _derive_features(payload)
        priority = self._infer_priority(provider_name)

        return ProviderConfig(
            name=provider_name,
            model_id=payload.get("id")
            or payload.get("slug")
            or payload.get("canonical_slug")
            or "",
            priority=priority,
            requires_credentials=payload.get("requires_api_key", False)
            or payload.get("requires_credentials", False),
            cost_per_1k_input=prompt_cost,
            cost_per_1k_output=completion_cost,
            enabled=payload.get("enabled", True),
            max_tokens=max_tokens,
            features=features,
            availability=payload.get("availability", "unknown"),
            metadata={
                "source_gateway": payload.get("source_gateway"),
                "provider_slug": payload.get("provider_slug"),
                "pricing": pricing,
            },
        )

    def _infer_priority(self, provider_name: str) -> int:
        provider_lower = provider_name.lower()
        if provider_lower in DEFAULT_PROVIDER_PRIORITY:
            return DEFAULT_PROVIDER_PRIORITY.index(provider_lower) + 1
        return len(DEFAULT_PROVIDER_PRIORITY) + 1

    def _index_aliases(
        self,
        model: MultiProviderModel,
        provider_name: str,
        payload: Dict[str, Any],
    ) -> None:
        aliases = {
            model.id,
            payload.get("id"),
            payload.get("slug"),
            payload.get("canonical_slug"),
            f"{provider_name}:{payload.get('id')}",
        }
        for alias in aliases:
            if not alias:
                continue
            key = self._normalize_key(alias)
            self._alias_index[key] = self._normalize_key(model.id)

    def _index_model(self, model: MultiProviderModel) -> None:
        canonical_key = self._normalize_key(model.id)
        self._alias_index[canonical_key] = canonical_key
        for alias in model.aliases:
            self._alias_index[self._normalize_key(alias)] = canonical_key
        for provider in model.providers:
            alias = provider.model_id
            if alias:
                self._alias_index[self._normalize_key(alias)] = canonical_key
            composite = f"{provider.name}:{alias}"
            self._alias_index[self._normalize_key(composite)] = canonical_key


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _derive_features(payload: Dict[str, Any]) -> List[str]:
    features: Set[str] = set()
    if payload.get("supports_streaming") or payload.get("streaming"):
        features.add("streaming")
    if payload.get("supports_function_calling"):
        features.add("function_calling")
    architecture = payload.get("architecture") or {}
    modality = architecture.get("modality")
    if modality and modality not in ("text", "unknown"):
        features.add(modality)
    for flag in ("multimodal", "vision", "audio", "video"):
        if payload.get(flag) or architecture.get(flag):
            features.add(flag)
    return sorted(features)


def _coerce_modalities(payload: Dict[str, Any]) -> List[str]:
    architecture = payload.get("architecture") or {}
    input_modalities = architecture.get("input_modalities")
    if isinstance(input_modalities, list) and input_modalities:
        return input_modalities
    if payload.get("modalities"):
        return payload["modalities"]
    modality = architecture.get("modality")
    if modality:
        return [modality]
    return ["text"]


# Global registry instance ------------------------------------------------- #
_registry = MultiProviderRegistry()


def get_registry() -> MultiProviderRegistry:
    """Get the global multi-provider registry instance"""
    return _registry
