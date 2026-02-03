"""
Pricing Provider Auditor

Verifies pricing data from various provider APIs against our stored pricing.
Detects when providers have updated pricing and generates discrepancy reports.

Features:
- Fetch pricing from provider APIs
- Compare against manual_pricing.json
- Detect pricing mismatches
- Generate audit reports
- Calculate impact of pricing differences
"""

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Timeout for API requests
REQUEST_TIMEOUT = 30.0


@dataclass
class ProviderPricingData:
    """Provider pricing data from API"""

    provider_name: str
    models: dict[str, Any]
    fetched_at: str
    status: str  # "success", "error", "partial"
    error_message: str | None = None


@dataclass
class PricingDiscrepancy:
    """Pricing mismatch between stored and API data"""

    gateway: str
    model_id: str
    field: str
    stored_price: float
    api_price: float
    difference: float
    difference_pct: float
    impact_severity: str  # "minor", "moderate", "major", "critical"


class PricingProviderAuditor:
    """Audits pricing data from provider APIs"""

    def __init__(self):
        self.audit_results = []

    async def audit_featherless(self) -> ProviderPricingData:
        """
        Audit Featherless pricing from their API.

        Note: Featherless API endpoint may require authentication.
        """
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    "https://api.featherless.ai/v1/models",
                    headers={
                        "User-Agent": "Gatewayz-Pricing-Auditor/1.0",
                    },
                )

                if response.status_code != 200:
                    return ProviderPricingData(
                        provider_name="featherless",
                        models={},
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        status="error",
                        error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    )

                data = response.json()
                models = {}

                # Parse response (structure may vary)
                if isinstance(data, dict) and "data" in data:
                    for model in data["data"]:
                        model_id = model.get("id", "")
                        pricing = model.get("pricing", {})
                        if model_id and pricing:
                            models[model_id] = pricing

                return ProviderPricingData(
                    provider_name="featherless",
                    models=models,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="success" if models else "partial",
                )
        except Exception as e:
            logger.error(f"Error auditing Featherless: {e}")
            return ProviderPricingData(
                provider_name="featherless",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_nearai(self) -> ProviderPricingData:
        """Audit Near AI pricing from their API."""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    "https://cloud-api.near.ai/v1/model/list",
                    headers={
                        "User-Agent": "Gatewayz-Pricing-Auditor/1.0",
                    },
                )

                if response.status_code != 200:
                    return ProviderPricingData(
                        provider_name="near",
                        models={},
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        status="error",
                        error_message=f"HTTP {response.status_code}",
                    )

                data = response.json()
                models = {}

                # Parse response
                if isinstance(data, list):
                    for model in data:
                        model_id = model.get("id", "")
                        pricing = model.get("pricing", {})
                        if model_id and pricing:
                            models[model_id] = pricing
                elif isinstance(data, dict) and "models" in data:
                    for model in data["models"]:
                        model_id = model.get("id", "")
                        pricing = model.get("pricing", {})
                        if model_id and pricing:
                            models[model_id] = pricing

                return ProviderPricingData(
                    provider_name="near",
                    models=models,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="success" if models else "partial",
                )
        except Exception as e:
            logger.error(f"Error auditing Near AI: {e}")
            return ProviderPricingData(
                provider_name="near",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_alibaba_cloud(self) -> ProviderPricingData:
        """
        Audit Alibaba Cloud pricing.

        Alibaba doesn't expose pricing via public API.
        Requires manual verification or SDK access.
        """
        return ProviderPricingData(
            provider_name="alibaba-cloud",
            models={},
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="error",
            error_message="Alibaba Cloud pricing requires authenticated API access. Manual verification recommended.",
        )

    async def audit_openrouter(self) -> ProviderPricingData:
        """Audit OpenRouter pricing from their API."""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={
                        "User-Agent": "Gatewayz-Pricing-Auditor/1.0",
                    },
                )

                if response.status_code != 200:
                    return ProviderPricingData(
                        provider_name="openrouter",
                        models={},
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        status="error",
                        error_message=f"HTTP {response.status_code}",
                    )

                data = response.json()
                models = {}

                # Parse OpenRouter response
                if isinstance(data, dict) and "data" in data:
                    for model in data["data"]:
                        model_id = model.get("id", "")
                        pricing = model.get("pricing", {})
                        if model_id and pricing:
                            models[model_id] = {
                                "prompt": pricing.get("prompt", 0),
                                "completion": pricing.get("completion", 0),
                                "request": pricing.get("request", 0),
                                "image": pricing.get("image", 0),
                            }

                return ProviderPricingData(
                    provider_name="openrouter",
                    models=models,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="success" if models else "partial",
                )
        except Exception as e:
            logger.error(f"Error auditing OpenRouter: {e}")
            return ProviderPricingData(
                provider_name="openrouter",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_fireworks(self) -> ProviderPricingData:
        """
        Audit Fireworks AI pricing from their API.

        Fireworks returns pricing in cents per token format.
        """
        try:
            from src.config import Config

            if not Config.FIREWORKS_API_KEY:
                return ProviderPricingData(
                    provider_name="fireworks",
                    models={},
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error_message="Fireworks API key not configured",
                )

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    "https://api.fireworks.ai/inference/v1/models",
                    headers={
                        "Authorization": f"Bearer {Config.FIREWORKS_API_KEY}",
                        "User-Agent": "Gatewayz-Pricing-Auditor/1.0",
                    },
                )

                if response.status_code != 200:
                    return ProviderPricingData(
                        provider_name="fireworks",
                        models={},
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        status="error",
                        error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    )

                data = response.json()
                raw_models = data.get("data", [])

                models = {}
                for model in raw_models:
                    model_id = model.get("id")
                    pricing_info = model.get("pricing", {})

                    if model_id and pricing_info:
                        # Fireworks uses cents per token
                        cents_input = pricing_info.get("cents_per_input_token")
                        cents_output = pricing_info.get("cents_per_output_token")

                        # Also check for dollar-based format
                        input_price = pricing_info.get("input")
                        output_price = pricing_info.get("output")

                        if cents_input is not None or cents_output is not None:
                            # Convert cents to dollars per 1M tokens
                            models[model_id] = {
                                "prompt": (cents_input / 100 * 1_000_000) if cents_input else 0,
                                "completion": (cents_output / 100 * 1_000_000) if cents_output else 0,
                            }
                        elif input_price is not None or output_price is not None:
                            # Already in per-1M format (likely)
                            models[model_id] = {
                                "prompt": input_price if input_price is not None else 0,
                                "completion": output_price if output_price is not None else 0,
                            }

                logger.info(f"Fetched pricing for {len(models)} Fireworks models")

                return ProviderPricingData(
                    provider_name="fireworks",
                    models=models,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="success" if models else "partial",
                    error_message=None if models else "No models with pricing found",
                )

        except Exception as e:
            logger.error(f"Error auditing Fireworks pricing: {e}")
            return ProviderPricingData(
                provider_name="fireworks",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_groq(self) -> ProviderPricingData:
        """
        Audit Groq pricing from their API.

        Groq returns pricing in cents per token format.
        """
        try:
            from src.config import Config

            if not Config.GROQ_API_KEY:
                return ProviderPricingData(
                    provider_name="groq",
                    models={},
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error_message="Groq API key not configured",
                )

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={
                        "Authorization": f"Bearer {Config.GROQ_API_KEY}",
                        "User-Agent": "Gatewayz-Pricing-Auditor/1.0",
                    },
                )

                if response.status_code != 200:
                    return ProviderPricingData(
                        provider_name="groq",
                        models={},
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        status="error",
                        error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    )

                data = response.json()
                raw_models = data.get("data", [])

                models = {}
                for model in raw_models:
                    model_id = model.get("id")
                    pricing_info = model.get("pricing", {})

                    if model_id and pricing_info:
                        # Groq uses cents per token
                        cents_input = pricing_info.get("cents_per_input_token")
                        cents_output = pricing_info.get("cents_per_output_token")

                        if cents_input is not None or cents_output is not None:
                            # Convert cents to dollars per 1M tokens
                            models[f"groq/{model_id}"] = {
                                "prompt": (cents_input / 100 * 1_000_000) if cents_input else 0,
                                "completion": (cents_output / 100 * 1_000_000) if cents_output else 0,
                            }

                logger.info(f"Fetched pricing for {len(models)} Groq models")

                return ProviderPricingData(
                    provider_name="groq",
                    models=models,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="success" if models else "partial",
                    error_message=None if models else "No models with pricing found",
                )

        except Exception as e:
            logger.error(f"Error auditing Groq pricing: {e}")
            return ProviderPricingData(
                provider_name="groq",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_deepinfra(self) -> ProviderPricingData:
        """
        Audit DeepInfra pricing from their API.

        DeepInfra returns pricing in cents per token format.
        """
        try:
            from src.config import Config

            if not Config.DEEPINFRA_API_KEY:
                return ProviderPricingData(
                    provider_name="deepinfra",
                    models={},
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error_message="DeepInfra API key not configured",
                )

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    "https://api.deepinfra.com/models/list",
                    headers={
                        "Authorization": f"Bearer {Config.DEEPINFRA_API_KEY}",
                        "User-Agent": "Gatewayz-Pricing-Auditor/1.0",
                    },
                )

                if response.status_code != 200:
                    return ProviderPricingData(
                        provider_name="deepinfra",
                        models={},
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        status="error",
                        error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    )

                data = response.json()
                # DeepInfra returns array directly
                raw_models = data if isinstance(data, list) else data.get("data", [])

                models = {}
                for model in raw_models:
                    model_id = model.get("model_name") or model.get("id")
                    pricing_info = model.get("pricing", {})

                    if model_id and pricing_info:
                        # DeepInfra uses cents per token
                        cents_input = pricing_info.get("cents_per_input_token")
                        cents_output = pricing_info.get("cents_per_output_token")

                        if cents_input is not None or cents_output is not None:
                            # Convert cents to dollars per 1M tokens
                            models[model_id] = {
                                "prompt": (cents_input / 100 * 1_000_000) if cents_input else 0,
                                "completion": (cents_output / 100 * 1_000_000) if cents_output else 0,
                            }

                logger.info(f"Fetched pricing for {len(models)} DeepInfra models")

                return ProviderPricingData(
                    provider_name="deepinfra",
                    models=models,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="success" if models else "partial",
                    error_message=None if models else "No models with pricing found",
                )

        except Exception as e:
            logger.error(f"Error auditing DeepInfra pricing: {e}")
            return ProviderPricingData(
                provider_name="deepinfra",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_together(self) -> ProviderPricingData:
        """
        Audit Together AI pricing from their API.

        Together AI returns pricing in per-1M format in `pricing.input` and `pricing.output`.
        """
        try:
            from src.config import Config

            if not Config.TOGETHER_API_KEY:
                return ProviderPricingData(
                    provider_name="together",
                    models={},
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error_message="Together API key not configured",
                )

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(
                    "https://api.together.xyz/v1/models",
                    headers={
                        "Authorization": f"Bearer {Config.TOGETHER_API_KEY}",
                        "User-Agent": "Gatewayz-Pricing-Auditor/1.0",
                    },
                )

                if response.status_code != 200:
                    return ProviderPricingData(
                        provider_name="together",
                        models={},
                        fetched_at=datetime.now(timezone.utc).isoformat(),
                        status="error",
                        error_message=f"HTTP {response.status_code}: {response.text[:200]}",
                    )

                data = response.json()
                # Together returns a list directly
                raw_models = data if isinstance(data, list) else data.get("data", [])

                models = {}
                for model in raw_models:
                    model_id = model.get("id")
                    pricing_info = model.get("pricing", {})

                    if model_id and pricing_info:
                        # Together uses 'input' and 'output' keys (per-1M format)
                        prompt_price = pricing_info.get("input")
                        completion_price = pricing_info.get("output")

                        if prompt_price is not None or completion_price is not None:
                            models[model_id] = {
                                "prompt": prompt_price if prompt_price is not None else 0,
                                "completion": completion_price if completion_price is not None else 0,
                            }

                logger.info(f"Fetched pricing for {len(models)} Together AI models")

                return ProviderPricingData(
                    provider_name="together",
                    models=models,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="success" if models else "partial",
                    error_message=None if models else "No models with pricing found",
                )

        except Exception as e:
            logger.error(f"Error auditing Together AI pricing: {e}")
            return ProviderPricingData(
                provider_name="together",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_cerebras(self) -> ProviderPricingData:
        """
        Audit Cerebras pricing from their models.list API.

        Cerebras uses the cerebras-cloud-sdk which provides a models.list endpoint.
        Pricing is included in the model metadata response.
        """
        try:
            # Import client function
            from src.services.cerebras_client import fetch_models_from_cerebras

            # Fetch models (this includes pricing)
            models_data = fetch_models_from_cerebras()
            if not models_data:
                return ProviderPricingData(
                    provider_name="cerebras",
                    models={},
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error_message="No models fetched from Cerebras API",
                )

            # Extract pricing from normalized models
            models = {}
            for model in models_data:
                model_id = model.get("id")
                pricing_info = model.get("pricing", {})

                if model_id and pricing_info:
                    # Pricing is already normalized to per-1M tokens format
                    prompt_price = pricing_info.get("prompt")
                    completion_price = pricing_info.get("completion")

                    if prompt_price or completion_price:
                        # Convert string to float if needed
                        try:
                            prompt_val = float(prompt_price) if prompt_price else 0
                            completion_val = float(completion_price) if completion_price else 0
                            models[model_id] = {
                                "prompt": prompt_val,
                                "completion": completion_val,
                            }
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid pricing for Cerebras model {model_id}")
                            continue

            logger.info(f"Fetched pricing for {len(models)} Cerebras models")

            return ProviderPricingData(
                provider_name="cerebras",
                models=models,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="success" if models else "partial",
                error_message=None if models else "No models with pricing found",
            )

        except Exception as e:
            logger.error(f"Error auditing Cerebras pricing: {e}")
            return ProviderPricingData(
                provider_name="cerebras",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_novita(self) -> ProviderPricingData:
        """
        Audit Novita pricing from their OpenAI-compatible models API.

        Novita provides pricing in their models.list response.
        """
        try:
            # Import client function
            from src.services.novita_client import fetch_models_from_novita

            # Fetch models (this includes pricing)
            models_data = fetch_models_from_novita()
            if not models_data:
                return ProviderPricingData(
                    provider_name="novita",
                    models={},
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error_message="No models fetched from Novita API",
                )

            # Extract pricing from normalized models
            models = {}
            for model in models_data:
                model_id = model.get("id")
                pricing_info = model.get("pricing", {})

                if model_id and pricing_info:
                    # Pricing is already normalized to per-1M tokens format
                    prompt_price = pricing_info.get("prompt")
                    completion_price = pricing_info.get("completion")

                    if prompt_price or completion_price:
                        # Convert string to float if needed
                        try:
                            prompt_val = float(prompt_price) if prompt_price else 0
                            completion_val = float(completion_price) if completion_price else 0
                            models[model_id] = {
                                "prompt": prompt_val,
                                "completion": completion_val,
                            }
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid pricing for Novita model {model_id}")
                            continue

            logger.info(f"Fetched pricing for {len(models)} Novita models")

            return ProviderPricingData(
                provider_name="novita",
                models=models,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="success" if models else "partial",
                error_message=None if models else "No models with pricing found",
            )

        except Exception as e:
            logger.error(f"Error auditing Novita pricing: {e}")
            return ProviderPricingData(
                provider_name="novita",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_nebius(self) -> ProviderPricingData:
        """
        Audit Nebius pricing from their OpenAI-compatible models API.

        Nebius Token Factory provides pricing in their models.list response.
        """
        try:
            # Import client function
            from src.services.nebius_client import fetch_models_from_nebius

            # Fetch models (this includes pricing)
            models_data = fetch_models_from_nebius()
            if not models_data:
                return ProviderPricingData(
                    provider_name="nebius",
                    models={},
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error_message="No models fetched from Nebius API",
                )

            # Extract pricing from normalized models
            models = {}
            for model in models_data:
                model_id = model.get("id")
                pricing_info = model.get("pricing", {})

                if model_id and pricing_info:
                    # Pricing is already normalized to per-1M tokens format
                    prompt_price = pricing_info.get("prompt")
                    completion_price = pricing_info.get("completion")

                    if prompt_price or completion_price:
                        # Convert string to float if needed
                        try:
                            prompt_val = float(prompt_price) if prompt_price else 0
                            completion_val = float(completion_price) if completion_price else 0
                            models[model_id] = {
                                "prompt": prompt_val,
                                "completion": completion_val,
                            }
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid pricing for Nebius model {model_id}")
                            continue

            logger.info(f"Fetched pricing for {len(models)} Nebius models")

            return ProviderPricingData(
                provider_name="nebius",
                models=models,
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="success" if models else "partial",
                error_message=None if models else "No models with pricing found",
            )

        except Exception as e:
            logger.error(f"Error auditing Nebius pricing: {e}")
            return ProviderPricingData(
                provider_name="nebius",
                models={},
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error_message=str(e),
            )

    async def audit_all_providers(self) -> list[ProviderPricingData]:
        """Audit all provider APIs and return results."""
        tasks = [
            self.audit_featherless(),
            self.audit_nearai(),
            self.audit_alibaba_cloud(),
            self.audit_openrouter(),
            self.audit_together(),
            self.audit_fireworks(),
            self.audit_groq(),
            self.audit_deepinfra(),
            self.audit_cerebras(),
            self.audit_novita(),
            self.audit_nebius(),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions
        audit_results = []
        for result in results:
            if isinstance(result, ProviderPricingData):
                audit_results.append(result)
            else:
                logger.error(f"Audit task failed with exception: {result}")

        return audit_results

    def compare_with_manual_pricing(
        self, api_data: ProviderPricingData, manual_pricing: dict[str, Any]
    ) -> list[PricingDiscrepancy]:
        """
        Compare API pricing with manual_pricing.json.

        Args:
            api_data: Pricing from provider API
            manual_pricing: Our stored pricing data

        Returns:
            List of discrepancies found
        """
        discrepancies = []
        gateway = api_data.provider_name.lower()

        if gateway not in manual_pricing:
            return discrepancies

        stored_models = manual_pricing[gateway]

        # Check each model in API response
        for model_id, api_pricing in api_data.models.items():
            # Normalize model ID for comparison
            normalized_id = model_id.lower()

            # Try to find in stored pricing (case-insensitive)
            stored_pricing = None
            for stored_id, stored_data in stored_models.items():
                if stored_id.lower() == normalized_id:
                    stored_pricing = stored_data
                    break

            if not stored_pricing:
                continue

            # Compare pricing fields
            for field in ["prompt", "completion", "request", "image"]:
                api_price = float(api_pricing.get(field, 0) or 0)
                stored_price = float(stored_pricing.get(field, 0) or 0)

                if api_price == 0 or stored_price == 0:
                    continue

                difference = abs(api_price - stored_price)
                difference_pct = (difference / stored_price) * 100

                # Flag if difference is significant (>5%)
                if difference_pct > 5:
                    severity = self._calculate_severity(difference_pct)
                    discrepancy = PricingDiscrepancy(
                        gateway=gateway,
                        model_id=model_id,
                        field=field,
                        stored_price=stored_price,
                        api_price=api_price,
                        difference=difference,
                        difference_pct=difference_pct,
                        impact_severity=severity,
                    )
                    discrepancies.append(discrepancy)

        return discrepancies

    def _calculate_severity(self, difference_pct: float) -> str:
        """Calculate severity based on price difference percentage."""
        if difference_pct > 50:
            return "critical"
        elif difference_pct > 25:
            return "major"
        elif difference_pct > 10:
            return "moderate"
        else:
            return "minor"

    def generate_audit_report(
        self, audit_results: list[ProviderPricingData], manual_pricing: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Generate comprehensive audit report.

        Args:
            audit_results: Results from provider audits
            manual_pricing: Our stored pricing data

        Returns:
            Audit report
        """
        report = {
            "report_type": "provider_pricing_audit",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "audit_results": [],
            "discrepancies": [],
            "summary": {
                "providers_audited": 0,
                "providers_successful": 0,
                "providers_failed": 0,
                "total_discrepancies": 0,
                "critical_discrepancies": 0,
            },
        }

        total_discrepancies = []

        for audit_result in audit_results:
            report["summary"]["providers_audited"] += 1

            result_entry = {
                "provider": audit_result.provider_name,
                "status": audit_result.status,
                "fetched_at": audit_result.fetched_at,
                "model_count": len(audit_result.models),
            }

            if audit_result.status == "success":
                report["summary"]["providers_successful"] += 1

                # Compare with manual pricing
                discrepancies = self.compare_with_manual_pricing(
                    audit_result, manual_pricing
                )
                total_discrepancies.extend(discrepancies)

                result_entry["discrepancy_count"] = len(discrepancies)
            else:
                report["summary"]["providers_failed"] += 1
                result_entry["error"] = audit_result.error_message

            report["audit_results"].append(result_entry)

        # Sort discrepancies by severity and percentage
        total_discrepancies.sort(
            key=lambda x: (
                {"critical": 0, "major": 1, "moderate": 2, "minor": 3}[x.impact_severity],
                x.difference_pct,
            )
        )

        # Add to report
        report["discrepancies"] = [asdict(d) for d in total_discrepancies]
        report["summary"]["total_discrepancies"] = len(total_discrepancies)
        report["summary"]["critical_discrepancies"] = len(
            [d for d in total_discrepancies if d.impact_severity == "critical"]
        )

        # Top 10 worst discrepancies
        report["top_discrepancies"] = [asdict(d) for d in total_discrepancies[:10]]

        # Recommendations
        report["recommendations"] = self._generate_recommendations(total_discrepancies)

        return report

    def _generate_recommendations(self, discrepancies: list[PricingDiscrepancy]) -> list[str]:
        """Generate recommendations based on audit findings."""
        recommendations = []

        if not discrepancies:
            recommendations.append("✅ No pricing discrepancies found with provider APIs")
            return recommendations

        critical = [d for d in discrepancies if d.impact_severity == "critical"]
        major = [d for d in discrepancies if d.impact_severity == "major"]

        if critical:
            recommendations.append(
                f"🔴 URGENT: {len(critical)} critical pricing mismatches detected. "
                "Update pricing immediately to maintain accuracy."
            )

        if major:
            affected_providers = set(d.gateway for d in major)
            recommendations.append(
                f"🟠 {len(major)} major discrepancies in {len(affected_providers)} provider(s). "
                "Review and update pricing data."
            )

        # Check for patterns
        by_provider = {}
        for d in discrepancies:
            if d.gateway not in by_provider:
                by_provider[d.gateway] = []
            by_provider[d.gateway].append(d)

        high_discrepancy_providers = [
            (p, len(d)) for p, d in by_provider.items() if len(d) > 5
        ]
        if high_discrepancy_providers:
            providers_str = ", ".join(p[0] for p in high_discrepancy_providers)
            recommendations.append(
                f"⚠️  Providers with multiple discrepancies: {providers_str}. "
                "Consider implementing automated price sync for these providers."
            )

        # Estimate cost impact
        total_impact = sum(d.difference_pct for d in discrepancies if d.difference_pct > 0)
        if total_impact > 100:
            recommendations.append(
                f"💰 Cumulative pricing variance: {total_impact:.1f}%. "
                "May impact customer costs significantly."
            )

        return recommendations


async def run_provider_audit() -> dict[str, Any]:
    """Run complete provider audit and return report."""
    from src.services.pricing_lookup import load_manual_pricing

    auditor = PricingProviderAuditor()

    # Run audits
    audit_results = await auditor.audit_all_providers()

    # Load manual pricing
    manual_pricing = load_manual_pricing()

    # Generate report
    report = auditor.generate_audit_report(audit_results, manual_pricing)

    return report


# Synchronous wrapper for use in routes
def get_provider_audit_report() -> dict[str, Any]:
    """Get provider audit report (sync wrapper)."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already in async context, create task differently
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return loop.run_in_executor(
                    pool, lambda: asyncio.run(run_provider_audit())
                )
        else:
            return asyncio.run(run_provider_audit())
    except RuntimeError:
        return asyncio.run(run_provider_audit())
