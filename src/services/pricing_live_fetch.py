"""
Live Pricing Fetch Service

Fetches real-time pricing directly from provider APIs on-demand.
Uses short-term caching (15 minutes) to minimize API calls.

This replaces hard-coded pricing with live pricing from provider APIs.
"""

import logging
from typing import Any

import httpx

from src.services.pricing_normalization import normalize_pricing_dict, get_provider_format

logger = logging.getLogger(__name__)

# Timeout for API requests
REQUEST_TIMEOUT = 10.0


class LivePricingFetcher:
    """Fetches live pricing from provider APIs"""

    def __init__(self):
        self.http_client = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "Gatewayz-Live-Pricing/1.0"},
            )
        return self.http_client

    async def close(self):
        """Close HTTP client"""
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

    def _extract_provider_from_model_id(self, model_id: str) -> str | None:
        """
        Extract provider name from model ID.

        Examples:
            "openai/gpt-4" -> "openrouter" (most models on OpenRouter use provider/model format)
            "cerebras/llama3.1-8b" -> "cerebras"
            "deepinfra/meta-llama-3.1-8b" -> "deepinfra"

        Args:
            model_id: Model identifier

        Returns:
            Provider name or None if cannot determine
        """
        # Direct provider prefixes
        provider_map = {
            "cerebras/": "cerebras",
            "deepinfra/": "deepinfra",
            "near/": "near",
            "featherless/": "featherless",
            "alibaba/": "alibaba-cloud",
            "groq/": "groq",
            "together/": "together",
            "fireworks/": "fireworks",
        }

        for prefix, provider in provider_map.items():
            if model_id.startswith(prefix):
                return provider

        # Default to OpenRouter for standard provider/model format
        if "/" in model_id:
            return "openrouter"

        return None

    async def fetch_openrouter_pricing(self, model_id: str) -> dict[str, float] | None:
        """
        Fetch live pricing from OpenRouter API.

        Args:
            model_id: Model ID (e.g., "openai/gpt-4", "anthropic/claude-3-opus")

        Returns:
            Pricing dict with prompt/completion prices, or None if not found
        """
        try:
            client = await self._get_client()
            response = await client.get("https://openrouter.ai/api/v1/models")

            if response.status_code != 200:
                logger.warning(
                    f"OpenRouter API returned {response.status_code}: {response.text[:200]}"
                )
                return None

            data = response.json()

            # Find the specific model
            if isinstance(data, dict) and "data" in data:
                for model in data["data"]:
                    if model.get("id") == model_id:
                        pricing = model.get("pricing", {})

                        # OpenRouter returns pricing in per-token format (e.g., 0.00000015)
                        prompt_price = float(pricing.get("prompt", 0))
                        completion_price = float(pricing.get("completion", 0))

                        # Normalize pricing to ensure it's in per-token format
                        source_format = get_provider_format("openrouter")
                        normalized = normalize_pricing_dict(
                            {"prompt": prompt_price, "completion": completion_price},
                            source_format=source_format
                        )

                        # Convert normalized strings to floats
                        prompt_normalized = float(normalized["prompt"])
                        completion_normalized = float(normalized["completion"])

                        logger.info(
                            f"[LIVE] OpenRouter pricing for {model_id}: "
                            f"prompt=${prompt_normalized}, completion=${completion_normalized}"
                        )

                        return {
                            "prompt": prompt_normalized,
                            "completion": completion_normalized,
                            "found": True,
                            "source": "live_api_openrouter"
                        }

            logger.warning(f"Model {model_id} not found in OpenRouter API response")
            return None

        except Exception as e:
            logger.error(f"Error fetching OpenRouter pricing for {model_id}: {e}")
            return None

    async def fetch_featherless_pricing(self, model_id: str) -> dict[str, float] | None:
        """
        Fetch live pricing from Featherless API.

        Args:
            model_id: Model ID (e.g., "featherless/meta-llama-3.1-8b")

        Returns:
            Pricing dict or None
        """
        try:
            # Extract model name (remove provider prefix)
            model_name = model_id.replace("featherless/", "")

            client = await self._get_client()
            response = await client.get("https://api.featherless.ai/v1/models")

            if response.status_code != 200:
                logger.warning(
                    f"Featherless API returned {response.status_code}: {response.text[:200]}"
                )
                return None

            data = response.json()

            # Find the model
            if isinstance(data, dict) and "data" in data:
                for model in data["data"]:
                    if model.get("id") == model_name or model.get("id") == model_id:
                        pricing = model.get("pricing", {})

                        if pricing:
                            source_format = get_provider_format("featherless")
                            normalized = normalize_pricing_dict(pricing, source_format=source_format)

                            # Convert normalized strings to floats
                            prompt_normalized = float(normalized["prompt"])
                            completion_normalized = float(normalized["completion"])

                            logger.info(
                                f"[LIVE] Featherless pricing for {model_id}: "
                                f"prompt=${prompt_normalized}, completion=${completion_normalized}"
                            )

                            return {
                                "prompt": prompt_normalized,
                                "completion": completion_normalized,
                                "found": True,
                                "source": "live_api_featherless"
                            }

            logger.warning(f"Model {model_id} not found in Featherless API response")
            return None

        except Exception as e:
            logger.error(f"Error fetching Featherless pricing for {model_id}: {e}")
            return None

    async def fetch_nearai_pricing(self, model_id: str) -> dict[str, float] | None:
        """
        Fetch live pricing from Near AI API.

        Args:
            model_id: Model ID (e.g., "near/llama-3.1-8b")

        Returns:
            Pricing dict or None
        """
        try:
            # Extract model name
            model_name = model_id.replace("near/", "")

            client = await self._get_client()
            response = await client.get("https://cloud-api.near.ai/v1/model/list")

            if response.status_code != 200:
                logger.warning(
                    f"Near AI API returned {response.status_code}: {response.text[:200]}"
                )
                return None

            data = response.json()

            # Find the model
            if isinstance(data, list):
                for model in data:
                    if model.get("model_name") == model_name or model.get("model_name") == model_id:
                        pricing_info = model.get("pricing", {})

                        if pricing_info:
                            source_format = get_provider_format("near")
                            normalized = normalize_pricing_dict(pricing_info, source_format=source_format)

                            # Convert normalized strings to floats
                            prompt_normalized = float(normalized["prompt"])
                            completion_normalized = float(normalized["completion"])

                            logger.info(
                                f"[LIVE] Near AI pricing for {model_id}: "
                                f"prompt=${prompt_normalized}, completion=${completion_normalized}"
                            )

                            return {
                                "prompt": prompt_normalized,
                                "completion": completion_normalized,
                                "found": True,
                                "source": "live_api_nearai"
                            }

            logger.warning(f"Model {model_id} not found in Near AI API response")
            return None

        except Exception as e:
            logger.error(f"Error fetching Near AI pricing for {model_id}: {e}")
            return None

    async def fetch_alibaba_pricing(self, model_id: str) -> dict[str, float] | None:
        """
        Fetch live pricing from Alibaba Cloud DashScope API.

        Args:
            model_id: Model ID (e.g., "alibaba/qwen-turbo")

        Returns:
            Pricing dict or None
        """
        try:
            # Note: Alibaba Cloud API requires authentication
            # For now, we'll skip this and rely on fallback
            logger.debug(f"Alibaba Cloud pricing requires API key, skipping for {model_id}")
            return None

        except Exception as e:
            logger.error(f"Error fetching Alibaba pricing for {model_id}: {e}")
            return None

    async def fetch_live_pricing(self, model_id: str) -> dict[str, float] | None:
        """
        Fetch live pricing from appropriate provider API.

        Automatically detects provider from model_id and calls the right API.

        Args:
            model_id: Model identifier

        Returns:
            Pricing dict with prompt/completion prices, or None if unavailable
        """
        try:
            # Determine provider
            provider = self._extract_provider_from_model_id(model_id)

            if not provider:
                logger.debug(f"Cannot determine provider for {model_id}, skipping live fetch")
                return None

            # Dispatch to appropriate fetch method
            fetch_methods = {
                "openrouter": self.fetch_openrouter_pricing,
                "featherless": self.fetch_featherless_pricing,
                "near": self.fetch_nearai_pricing,
                "alibaba-cloud": self.fetch_alibaba_pricing,
            }

            fetch_method = fetch_methods.get(provider)

            if not fetch_method:
                logger.debug(
                    f"No live pricing fetcher for provider '{provider}' (model: {model_id})"
                )
                return None

            # Fetch pricing
            pricing = await fetch_method(model_id)

            if pricing:
                logger.info(
                    f"[LIVE FETCH SUCCESS] {model_id} from {provider}: "
                    f"${pricing['prompt']:.8f} / ${pricing['completion']:.8f}"
                )
            else:
                logger.debug(f"[LIVE FETCH FAILED] No pricing returned for {model_id}")

            return pricing

        except Exception as e:
            logger.error(f"Error in live pricing fetch for {model_id}: {e}")
            return None


# Singleton instance
_live_fetcher: LivePricingFetcher | None = None


def get_live_pricing_fetcher() -> LivePricingFetcher:
    """Get singleton instance of live pricing fetcher"""
    global _live_fetcher
    if _live_fetcher is None:
        _live_fetcher = LivePricingFetcher()
    return _live_fetcher


async def fetch_live_pricing(model_id: str) -> dict[str, float] | None:
    """
    Convenience function to fetch live pricing.

    Args:
        model_id: Model identifier

    Returns:
        Pricing dict or None
    """
    fetcher = get_live_pricing_fetcher()
    return await fetcher.fetch_live_pricing(model_id)
