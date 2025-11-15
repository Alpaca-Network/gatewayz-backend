"""
Gatewayz Client for HuggingFace Task API

This module provides both synchronous and asynchronous clients for
accessing Gatewayz models through HuggingFace's inference provider network.

Example:
    Async usage:
        async with AsyncGatewayzClient(api_key="...") as client:
            response = await client.text_generation("Hello, world!")

    Sync usage:
        client = GatewayzClient(api_key="...")
        response = client.text_generation("Hello, world!")
"""

import httpx
import asyncio
from typing import Optional, Dict, Any, List, Union, AsyncIterator, Iterator
from urllib.parse import urljoin
import json

from .types import (
    TextGenerationRequest,
    TextGenerationResponse,
    TextGenerationOutput,
    ConversationalRequest,
    ConversationalResponse,
    SummarizationRequest,
    SummarizationResponse,
    SummarizationOutput,
    TranslationRequest,
    TranslationOutput,
    QuestionAnsweringRequest,
    QuestionAnsweringOutput,
    ModelInfo,
    BillingInfo,
    CostInfo,
    UsageResponse,
    UsageRecord,
    TaskType,
)


class BaseGatewayzClient:
    """Base client with common functionality"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://gatewayz.io",
        timeout: int = 60,
    ):
        """
        Initialize client.

        Args:
            api_key: Gatewayz API key
            base_url: Base URL for API
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "gatewayz-py-hf/0.1.0",
        }

    def _build_url(self, path: str) -> str:
        """Build full URL from path"""
        return urljoin(self.base_url, path.lstrip("/"))


class AsyncGatewayzClient(BaseGatewayzClient):
    """Asynchronous client for Gatewayz HuggingFace inference provider"""

    async def __aenter__(self):
        """Async context manager entry"""
        self.client = httpx.AsyncClient(
            headers=self.headers,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.client.aclose()

    async def text_generation(
        self,
        inputs: str,
        model: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        stream: bool = False,
    ) -> Union[TextGenerationResponse, AsyncIterator[str]]:
        """
        Generate text given input prompt.

        Args:
            inputs: Input text/prompt
            model: Model to use (defaults to gpt-3.5-turbo)
            parameters: Optional generation parameters (temperature, max_tokens, etc)
            stream: Whether to stream response

        Returns:
            TextGenerationResponse or async iterator for streaming
        """
        request = {
            "inputs": inputs,
            "parameters": parameters or {},
        }

        if model:
            request["parameters"]["model"] = model

        if stream:
            return await self._stream_text_generation(request)

        url = self._build_url("/hf/tasks/text-generation")

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.post(url, json=request)
            response.raise_for_status()
            data = response.json()

            return TextGenerationResponse(
                output=[
                    TextGenerationOutput(generated_text=item["generated_text"])
                    for item in data.get("output", [])
                ]
            )

    async def conversational(
        self,
        text: str,
        past_user_inputs: Optional[List[str]] = None,
        generated_responses: Optional[List[str]] = None,
    ) -> ConversationalResponse:
        """
        Generate conversational response.

        Args:
            text: Current user input
            past_user_inputs: Previous user inputs
            generated_responses: Previous model responses

        Returns:
            ConversationalResponse
        """
        request = {
            "text": text,
            "past_user_inputs": past_user_inputs or [],
            "generated_responses": generated_responses or [],
        }

        url = self._build_url("/hf/tasks/conversational")

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.post(url, json=request)
            response.raise_for_status()
            data = response.json()

            return ConversationalResponse(
                conversation=data.get("conversation", {}),
                warnings=data.get("warnings"),
            )

    async def summarization(
        self,
        inputs: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> SummarizationResponse:
        """
        Summarize text.

        Args:
            inputs: Text to summarize
            parameters: Optional parameters

        Returns:
            SummarizationResponse
        """
        request = {
            "inputs": inputs,
            "parameters": parameters or {},
        }

        url = self._build_url("/hf/tasks/summarization")

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.post(url, json=request)
            response.raise_for_status()
            data = response.json()

            return SummarizationResponse(
                output=SummarizationOutput(summary_text=data["output"]["summary_text"])
            )

    async def translation(
        self,
        inputs: str,
        target_language: str = "English",
    ) -> TranslationOutput:
        """
        Translate text.

        Args:
            inputs: Text to translate
            target_language: Target language

        Returns:
            TranslationOutput
        """
        request = {
            "inputs": inputs,
            "target_language": target_language,
        }

        url = self._build_url("/hf/tasks/translation")

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.post(url, json=request)
            response.raise_for_status()
            data = response.json()

            return TranslationOutput(translation_text=data["output"]["translation_text"])

    async def question_answering(
        self,
        question: str,
        context: str,
    ) -> QuestionAnsweringOutput:
        """
        Answer question based on context.

        Args:
            question: Question to answer
            context: Context to answer from

        Returns:
            QuestionAnsweringOutput
        """
        request = {
            "question": question,
            "context": context,
        }

        url = self._build_url("/hf/tasks/question-answering")

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.post(url, json=request)
            response.raise_for_status()
            data = response.json()

            output = data["output"]
            return QuestionAnsweringOutput(
                answer=output["answer"],
                score=output.get("score"),
            )

    async def list_models(
        self,
        task_type: Optional[str] = None,
    ) -> List[ModelInfo]:
        """
        List available models.

        Args:
            task_type: Optional filter by task type

        Returns:
            List of ModelInfo objects
        """
        url = self._build_url("/hf/tasks/models")

        params = {}
        if task_type:
            params["task_type"] = task_type

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            models = []
            for model_data in data.get("models", []):
                models.append(ModelInfo(
                    model_id=model_data["model_id"],
                    hub_model_id=model_data.get("hub_model_id", ""),
                    task_type=model_data["task_type"],
                ))

            return models

    async def calculate_cost(
        self,
        requests: List[Dict[str, Any]],
    ) -> BillingInfo:
        """
        Calculate cost of requests.

        Args:
            requests: List of request objects with task, model, input_tokens, output_tokens

        Returns:
            BillingInfo with costs
        """
        url = self._build_url("/hf/tasks/billing/cost")

        request_body = {"requests": requests}

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.post(url, json=request_body)
            response.raise_for_status()
            data = response.json()

            costs = [
                CostInfo(
                    task=item["task"],
                    model=item["model"],
                    input_tokens=item.get("input_tokens", 0),
                    output_tokens=item.get("output_tokens", 0),
                    cost_nano_usd=item["cost_nano_usd"],
                    cost_usd=item["cost_nano_usd"] / 1e9,
                )
                for item in data.get("costs", [])
            ]

            return BillingInfo(
                total_cost_nano_usd=data["total_cost_nano_usd"],
                costs=costs,
                currency=data.get("currency", "USD"),
            )

    async def get_usage(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> UsageResponse:
        """
        Get usage records for billing.

        Args:
            limit: Max records to return
            offset: Offset for pagination

        Returns:
            UsageResponse with records
        """
        url = self._build_url("/hf/tasks/billing/usage")

        params = {"limit": limit, "offset": offset}

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            records = [
                UsageRecord(
                    request_id=item["request_id"],
                    timestamp=item["timestamp"],
                    task=item["task"],
                    model=item["model"],
                    input_tokens=item.get("input_tokens", 0),
                    output_tokens=item.get("output_tokens", 0),
                    cost_nano_usd=item["cost_nano_usd"],
                )
                for item in data.get("records", [])
            ]

            total_cost_usd = data.get("total_cost_nano_usd", 0) / 1e9

            return UsageResponse(
                records=records,
                total_records=data.get("total_records", 0),
                total_cost_nano_usd=data.get("total_cost_nano_usd", 0),
                total_cost_usd=total_cost_usd,
            )

    async def _stream_text_generation(
        self,
        request: Dict[str, Any],
    ) -> AsyncIterator[str]:
        """Stream text generation response"""
        request["stream"] = True
        url = self._build_url("/hf/tasks/text-generation")

        async with httpx.AsyncClient(headers=self.headers, timeout=None) as client:
            async with client.stream("POST", url, json=request) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        if "token" in data:
                            yield data["token"]["text"]


class GatewayzClient(BaseGatewayzClient):
    """Synchronous client for Gatewayz HuggingFace inference provider"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://gatewayz.io",
        timeout: int = 60,
    ):
        """
        Initialize synchronous client.

        Args:
            api_key: Gatewayz API key
            base_url: Base URL for API
            timeout: Request timeout in seconds
        """
        super().__init__(api_key, base_url, timeout)
        self.client = httpx.Client(headers=self.headers, timeout=timeout)

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.client.close()

    def text_generation(
        self,
        inputs: str,
        model: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> TextGenerationResponse:
        """Generate text given input prompt"""
        request = {
            "inputs": inputs,
            "parameters": parameters or {},
        }

        if model:
            request["parameters"]["model"] = model

        url = self._build_url("/hf/tasks/text-generation")

        response = self.client.post(url, json=request)
        response.raise_for_status()
        data = response.json()

        return TextGenerationResponse(
            output=[
                TextGenerationOutput(generated_text=item["generated_text"])
                for item in data.get("output", [])
            ]
        )

    def conversational(
        self,
        text: str,
        past_user_inputs: Optional[List[str]] = None,
        generated_responses: Optional[List[str]] = None,
    ) -> ConversationalResponse:
        """Generate conversational response"""
        request = {
            "text": text,
            "past_user_inputs": past_user_inputs or [],
            "generated_responses": generated_responses or [],
        }

        url = self._build_url("/hf/tasks/conversational")

        response = self.client.post(url, json=request)
        response.raise_for_status()
        data = response.json()

        return ConversationalResponse(
            conversation=data.get("conversation", {}),
            warnings=data.get("warnings"),
        )

    def summarization(
        self,
        inputs: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> SummarizationResponse:
        """Summarize text"""
        request = {
            "inputs": inputs,
            "parameters": parameters or {},
        }

        url = self._build_url("/hf/tasks/summarization")

        response = self.client.post(url, json=request)
        response.raise_for_status()
        data = response.json()

        return SummarizationResponse(
            output=SummarizationOutput(summary_text=data["output"]["summary_text"])
        )

    def list_models(
        self,
        task_type: Optional[str] = None,
    ) -> List[ModelInfo]:
        """List available models"""
        url = self._build_url("/hf/tasks/models")

        params = {}
        if task_type:
            params["task_type"] = task_type

        response = self.client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        models = []
        for model_data in data.get("models", []):
            models.append(ModelInfo(
                model_id=model_data["model_id"],
                hub_model_id=model_data.get("hub_model_id", ""),
                task_type=model_data["task_type"],
            ))

        return models

    def calculate_cost(
        self,
        requests: List[Dict[str, Any]],
    ) -> BillingInfo:
        """Calculate cost of requests"""
        url = self._build_url("/hf/tasks/billing/cost")

        request_body = {"requests": requests}

        response = self.client.post(url, json=request_body)
        response.raise_for_status()
        data = response.json()

        costs = [
            CostInfo(
                task=item["task"],
                model=item["model"],
                input_tokens=item.get("input_tokens", 0),
                output_tokens=item.get("output_tokens", 0),
                cost_nano_usd=item["cost_nano_usd"],
                cost_usd=item["cost_nano_usd"] / 1e9,
            )
            for item in data.get("costs", [])
        ]

        return BillingInfo(
            total_cost_nano_usd=data["total_cost_nano_usd"],
            costs=costs,
            currency=data.get("currency", "USD"),
        )

    def get_usage(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> UsageResponse:
        """Get usage records for billing"""
        url = self._build_url("/hf/tasks/billing/usage")

        params = {"limit": limit, "offset": offset}

        response = self.client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        records = [
            UsageRecord(
                request_id=item["request_id"],
                timestamp=item["timestamp"],
                task=item["task"],
                model=item["model"],
                input_tokens=item.get("input_tokens", 0),
                output_tokens=item.get("output_tokens", 0),
                cost_nano_usd=item["cost_nano_usd"],
            )
            for item in data.get("records", [])
        ]

        total_cost_usd = data.get("total_cost_nano_usd", 0) / 1e9

        return UsageResponse(
            records=records,
            total_records=data.get("total_records", 0),
            total_cost_nano_usd=data.get("total_cost_nano_usd", 0),
            total_cost_usd=total_cost_usd,
        )
