"""
HuggingFace Model Mapping Service

Manages model mappings between Gatewayz provider models and HuggingFace Hub models.
This enables proper discovery and routing on the HuggingFace Hub inference provider network.

Reference: https://huggingface.co/docs/inference-providers/
"""

import logging
from typing import Dict, List, Any, Optional
import json
from datetime import datetime

logger = logging.getLogger(__name__)

# In-memory cache for model mappings (in production, use database)
_model_mappings_cache: Dict[str, Dict[str, Any]] = {}


class ModelMappingService:
    """Service for managing HuggingFace model mappings"""

    @staticmethod
    async def register_mapping(
        provider_model_id: str,
        hub_model_id: str,
        task_type: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Register a model mapping.

        Args:
            provider_model_id: Model ID in provider's catalog
            hub_model_id: Corresponding HuggingFace Hub model ID
            task_type: Task type (e.g., 'text-generation', 'image-generation')
            parameters: Optional default parameters for this mapping

        Returns:
            Mapping record
        """
        try:
            mapping = {
                "provider_model_id": provider_model_id,
                "hub_model_id": hub_model_id,
                "task_type": task_type,
                "parameters": parameters or {},
                "registered_at": datetime.utcnow().isoformat(),
            }

            # Store in cache (in production, use database)
            _model_mappings_cache[provider_model_id] = mapping

            logger.info(
                f"Registered model mapping: {provider_model_id} -> "
                f"{hub_model_id} (task: {task_type})"
            )

            # In production, persist to database
            # await db_model_mappings.create_mapping(mapping)

            return mapping

        except Exception as e:
            logger.error(f"Error registering mapping: {str(e)}")
            raise


    @staticmethod
    async def get_mapping(
        provider_model_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a model mapping by provider model ID.

        Args:
            provider_model_id: Model ID in provider's catalog

        Returns:
            Mapping record or None if not found
        """
        try:
            # Check cache first
            if provider_model_id in _model_mappings_cache:
                return _model_mappings_cache[provider_model_id]

            # In production, query database
            # mapping = await db_model_mappings.get_mapping(provider_model_id)
            # if mapping:
            #     _model_mappings_cache[provider_model_id] = mapping
            #     return mapping

            return None

        except Exception as e:
            logger.error(f"Error getting mapping: {str(e)}")
            raise


    @staticmethod
    async def get_hub_model_id(
        provider_model_id: str,
    ) -> Optional[str]:
        """
        Get the HuggingFace Hub model ID for a provider model.

        Args:
            provider_model_id: Model ID in provider's catalog

        Returns:
            Hub model ID or None if not mapped
        """
        try:
            mapping = await ModelMappingService.get_mapping(provider_model_id)
            return mapping["hub_model_id"] if mapping else None

        except Exception as e:
            logger.error(f"Error getting hub model ID: {str(e)}")
            raise


    @staticmethod
    async def get_all_mappings(
        task_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all model mappings, optionally filtered by task type.

        Args:
            task_type: Optional task type filter

        Returns:
            List of mapping records
        """
        try:
            # In production, query database with optional filter
            mappings = list(_model_mappings_cache.values())

            if task_type:
                mappings = [m for m in mappings if m.get("task_type") == task_type]

            return mappings

        except Exception as e:
            logger.error(f"Error getting all mappings: {str(e)}")
            raise


    @staticmethod
    async def update_mapping(
        provider_model_id: str,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Update an existing model mapping.

        Args:
            provider_model_id: Model ID in provider's catalog
            updates: Dictionary of fields to update

        Returns:
            Updated mapping record or None if not found
        """
        try:
            mapping = await ModelMappingService.get_mapping(provider_model_id)
            if not mapping:
                return None

            # Update mapping
            mapping.update(updates)
            mapping["updated_at"] = datetime.utcnow().isoformat()

            # Store updated mapping
            _model_mappings_cache[provider_model_id] = mapping

            logger.info(f"Updated model mapping: {provider_model_id}")

            # In production, update in database
            # await db_model_mappings.update_mapping(provider_model_id, mapping)

            return mapping

        except Exception as e:
            logger.error(f"Error updating mapping: {str(e)}")
            raise


    @staticmethod
    async def delete_mapping(
        provider_model_id: str,
    ) -> bool:
        """
        Delete a model mapping.

        Args:
            provider_model_id: Model ID in provider's catalog

        Returns:
            True if deleted, False if not found
        """
        try:
            if provider_model_id not in _model_mappings_cache:
                return False

            del _model_mappings_cache[provider_model_id]

            logger.info(f"Deleted model mapping: {provider_model_id}")

            # In production, delete from database
            # await db_model_mappings.delete_mapping(provider_model_id)

            return True

        except Exception as e:
            logger.error(f"Error deleting mapping: {str(e)}")
            raise


    @staticmethod
    async def generate_mapping_registry() -> Dict[str, Any]:
        """
        Generate the complete model mapping registry for HuggingFace Hub submission.

        Returns:
            Registry object with all mappings organized by task type
        """
        try:
            mappings = await ModelMappingService.get_all_mappings()

            # Group by task type
            by_task = {}
            for mapping in mappings:
                task = mapping.get("task_type", "unknown")
                if task not in by_task:
                    by_task[task] = []
                by_task[task].append({
                    "provider_model_id": mapping["provider_model_id"],
                    "hub_model_id": mapping["hub_model_id"],
                    "parameters": mapping.get("parameters", {}),
                })

            registry = {
                "provider_name": "Gatewayz",
                "provider_url": "https://gatewayz.io",
                "generated_at": datetime.utcnow().isoformat(),
                "version": "1.0.0",
                "models_by_task": by_task,
                "total_models": len(mappings),
                "supported_tasks": list(by_task.keys()),
            }

            return registry

        except Exception as e:
            logger.error(f"Error generating registry: {str(e)}")
            raise


    @staticmethod
    async def bulk_register_mappings(
        mappings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Register multiple model mappings in bulk.

        Args:
            mappings: List of mapping objects

        Returns:
            List of registered mapping records
        """
        try:
            results = []

            for mapping in mappings:
                result = await ModelMappingService.register_mapping(
                    provider_model_id=mapping["provider_model_id"],
                    hub_model_id=mapping["hub_model_id"],
                    task_type=mapping["task_type"],
                    parameters=mapping.get("parameters"),
                )
                results.append(result)

            logger.info(f"Registered {len(results)} model mappings")

            return results

        except Exception as e:
            logger.error(f"Error in bulk registration: {str(e)}")
            raise


# Pre-defined mappings for common models
# These can be loaded at startup
DEFAULT_MAPPINGS = [
    {
        "provider_model_id": "gpt-3.5-turbo",
        "hub_model_id": "meta-llama/Llama-2-7b-chat",
        "task_type": "text-generation",
    },
    {
        "provider_model_id": "gpt-4",
        "hub_model_id": "meta-llama/Llama-2-70b-chat",
        "task_type": "text-generation",
    },
    {
        "provider_model_id": "claude-3-sonnet",
        "hub_model_id": "meta-llama/Llama-2-13b-chat",
        "task_type": "text-generation",
    },
]


async def load_default_mappings() -> None:
    """Load default model mappings at startup"""
    try:
        await ModelMappingService.bulk_register_mappings(DEFAULT_MAPPINGS)
        logger.info("Loaded default model mappings")
    except Exception as e:
        logger.warning(f"Failed to load default mappings: {str(e)}")
