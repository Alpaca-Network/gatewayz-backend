#!/usr/bin/env python3
"""
Comprehensive tests for provider failover logic - CRITICAL for reliability

Tests cover:
- Provider chain building
- Failover eligibility detection
- Error mapping from various exception types
- Retry-after header propagation
- Authentication error handling
- Rate limit error handling
- Timeout error handling
- Model not found errors
"""

import asyncio
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException

from src.services.provider_failover import (
    FAILOVER_STATUS_CODES,
    FALLBACK_ELIGIBLE_PROVIDERS,
    FALLBACK_PROVIDER_PRIORITY,
    build_provider_failover_chain,
    enforce_model_failover_rules,
    map_provider_error,
    should_failover,
)

# Try to import OpenAI SDK exceptions (same pattern as the module)
try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        NotFoundError,
        OpenAIError,
        PermissionDeniedError,
        RateLimitError,
    )

    OPENAI_SDK_AVAILABLE = True
except ImportError:
    OPENAI_SDK_AVAILABLE = False

    # Create mock exception classes for testing
    class APIConnectionError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, message, response=None, body=None):
            super().__init__(message)
            self.response = response
            self.body = body
            self.status_code = getattr(response, "status_code", 500) if response else 500

    class AuthenticationError(APIStatusError):
        pass

    class BadRequestError(APIStatusError):
        pass

    class NotFoundError(APIStatusError):
        pass

    class OpenAIError(Exception):
        pass

    class PermissionDeniedError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass


# Try to import Cerebras SDK exceptions (same pattern as the module)
try:
    from cerebras.cloud.sdk import APIConnectionError as CerebrasAPIConnectionError
    from cerebras.cloud.sdk import APIStatusError as CerebrasAPIStatusError
    from cerebras.cloud.sdk import AuthenticationError as CerebrasAuthenticationError
    from cerebras.cloud.sdk import BadRequestError as CerebrasBadRequestError
    from cerebras.cloud.sdk import NotFoundError as CerebrasNotFoundError
    from cerebras.cloud.sdk import PermissionDeniedError as CerebrasPermissionDeniedError
    from cerebras.cloud.sdk import RateLimitError as CerebrasRateLimitError

    CEREBRAS_SDK_AVAILABLE = True
except ImportError:
    CEREBRAS_SDK_AVAILABLE = False

    # Create mock exception classes for testing
    class CerebrasAPIConnectionError(Exception):
        pass

    class CerebrasAPIStatusError(Exception):
        def __init__(self, message, response=None, body=None):
            super().__init__(message)
            self.response = response
            self.body = body
            self.status_code = getattr(response, "status_code", 500) if response else 500

    class CerebrasAuthenticationError(CerebrasAPIStatusError):
        pass

    class CerebrasBadRequestError(CerebrasAPIStatusError):
        pass

    class CerebrasNotFoundError(CerebrasAPIStatusError):
        pass

    class CerebrasPermissionDeniedError(CerebrasAPIStatusError):
        pass

    class CerebrasRateLimitError(CerebrasAPIStatusError):
        pass


# ============================================================
# TEST CLASS: Provider Chain Building
# ============================================================


class TestBuildProviderFailoverChain:
    """Test provider failover chain construction"""

    def test_chain_with_huggingface_first(self):
        """Test chain starting with huggingface"""
        chain = build_provider_failover_chain("huggingface")

        assert chain[0] == "huggingface"
        assert "featherless" in chain
        assert "fireworks" in chain
        assert "together" in chain
        assert "openrouter" in chain
        # Verify all providers in priority list are included
        assert len(chain) == len(FALLBACK_PROVIDER_PRIORITY)

    def test_chain_with_openrouter_first(self):
        """Test chain starting with openrouter"""
        chain = build_provider_failover_chain("openrouter")

        assert chain[0] == "openrouter"
        # Other providers should follow in priority order
        remaining = [p for p in chain if p != "openrouter"]
        for i, provider in enumerate(remaining):
            # Should be in same relative order as FALLBACK_PROVIDER_PRIORITY
            assert provider in FALLBACK_PROVIDER_PRIORITY

    def test_chain_with_featherless_first(self):
        """Test chain starting with featherless"""
        chain = build_provider_failover_chain("featherless")

        assert chain[0] == "featherless"
        assert "huggingface" in chain
        assert "fireworks" in chain
        assert len(chain) == len(FALLBACK_PROVIDER_PRIORITY)

    def test_chain_with_none_provider(self):
        """Test chain with None provider defaults to onerouter"""
        chain = build_provider_failover_chain(None)

        assert chain[0] == "onerouter"

    def test_chain_with_empty_string(self):
        """Test chain with empty string defaults to onerouter"""
        chain = build_provider_failover_chain("")

        assert chain[0] == "onerouter"

    def test_chain_with_unknown_provider(self):
        """Test chain with unknown provider (not in fallback list)"""
        chain = build_provider_failover_chain("custom_provider")

        # Unknown providers should only return themselves (no fallback)
        assert chain == ["custom_provider"]

    def test_chain_with_removed_provider(self):
        """Test chain with removed provider (not eligible for fallback)"""
        # Portkey has been removed and is no longer a valid provider
        # Test that unknown providers return only themselves
        chain = build_provider_failover_chain("unknown_provider")

        # Unknown providers should only return themselves (no fallback chain)
        assert chain == ["unknown_provider"]

    def test_chain_case_insensitive(self):
        """Test chain building is case insensitive"""
        chain_lower = build_provider_failover_chain("huggingface")
        chain_upper = build_provider_failover_chain("HUGGINGFACE")
        chain_mixed = build_provider_failover_chain("HuggingFace")

        assert chain_lower == chain_upper == chain_mixed

    def test_chain_no_duplicates(self):
        """Test chain has no duplicate providers"""
        for provider in FALLBACK_ELIGIBLE_PROVIDERS:
            chain = build_provider_failover_chain(provider)
            assert len(chain) == len(set(chain))

    def test_fallback_provider_priority_constants(self):
        """Test fallback provider constants are defined correctly"""
        assert "huggingface" in FALLBACK_PROVIDER_PRIORITY
        assert "featherless" in FALLBACK_PROVIDER_PRIORITY
        assert "fireworks" in FALLBACK_PROVIDER_PRIORITY
        assert "together" in FALLBACK_PROVIDER_PRIORITY
        assert "openrouter" in FALLBACK_PROVIDER_PRIORITY

        # Verify FALLBACK_ELIGIBLE_PROVIDERS matches priority list
        assert FALLBACK_ELIGIBLE_PROVIDERS == set(FALLBACK_PROVIDER_PRIORITY)


class TestEnforceModelFailoverRules:
    """Test model-specific failover filtering"""

    def test_openrouter_prefix_locked(self):
        chain = ["openrouter", "cerebras", "huggingface"]
        filtered = enforce_model_failover_rules("openrouter/auto", chain)

        assert filtered == ["openrouter"]

    def test_openrouter_suffix_locked(self):
        chain = ["openrouter", "huggingface"]
        filtered = enforce_model_failover_rules("z-ai/glm-4.6:exacto", chain)

        assert filtered == ["openrouter"]

    def test_openai_prefix_routes_to_native_then_openrouter(self):
        """Test that openai/* models route to native OpenAI first, then OpenRouter fallback."""
        chain = ["openai", "openrouter", "cerebras", "huggingface"]
        filtered = enforce_model_failover_rules("openai/gpt-5.1", chain)

        # Should only include openai and openrouter, with openai first
        assert filtered == ["openai", "openrouter"]

    def test_openai_prefix_without_native_falls_back_to_openrouter(self):
        """Test that openai/* models fall back to OpenRouter if native OpenAI not in chain."""
        chain = ["openrouter", "cerebras", "huggingface"]
        filtered = enforce_model_failover_rules("openai/gpt-5.1", chain)

        # Should only include openrouter since openai is not in chain
        assert filtered == ["openrouter"]

    def test_anthropic_prefix_routes_to_native_then_openrouter(self):
        """Test that anthropic/* models route to native Anthropic first, then OpenRouter fallback."""
        chain = ["anthropic", "openrouter", "huggingface"]
        filtered = enforce_model_failover_rules("anthropic/claude-3.5-sonnet", chain)

        # Should only include anthropic and openrouter, with anthropic first
        assert filtered == ["anthropic", "openrouter"]

    def test_anthropic_prefix_without_native_falls_back_to_openrouter(self):
        """Test that anthropic/* models fall back to OpenRouter if native Anthropic not in chain."""
        chain = ["openrouter", "huggingface"]
        filtered = enforce_model_failover_rules("anthropic/claude-3.5-sonnet", chain)

        # Should only include openrouter since anthropic is not in chain
        assert filtered == ["openrouter"]

    def test_non_locked_model_noop(self):
        chain = ["openrouter", "cerebras"]
        filtered = enforce_model_failover_rules("deepseek-ai/deepseek-v3", chain)

        assert filtered == chain

    def test_payment_failover_does_not_bypass_anthropic_restriction(self):
        """Test that allow_payment_failover=True does NOT bypass anthropic/ restriction.

        Anthropic models can ONLY be served by Anthropic or OpenRouter. Routing them
        to other providers like Cerebras or HuggingFace will always fail, so we must
        maintain the restriction even with payment failover enabled.
        """
        chain = ["anthropic", "openrouter", "cerebras", "huggingface"]
        # Without payment failover, anthropic/ models are restricted to anthropic + openrouter
        filtered = enforce_model_failover_rules("anthropic/claude-3.5-sonnet", chain)
        assert filtered == ["anthropic", "openrouter"]

        # With payment failover enabled, restriction is STILL enforced
        # because these models don't exist on other providers
        filtered = enforce_model_failover_rules(
            "anthropic/claude-3.5-sonnet", chain, allow_payment_failover=True
        )
        assert filtered == ["anthropic", "openrouter"]

    def test_payment_failover_does_not_bypass_openai_restriction(self):
        """Test that allow_payment_failover=True does NOT bypass openai/ restriction.

        OpenAI models can ONLY be served by OpenAI or OpenRouter. Routing them
        to other providers like Cerebras or HuggingFace will always fail, so we must
        maintain the restriction even with payment failover enabled.
        """
        chain = ["openai", "openrouter", "cerebras", "huggingface"]
        # Without payment failover, openai/ models are restricted to openai + openrouter
        filtered = enforce_model_failover_rules("openai/gpt-5.1", chain)
        assert filtered == ["openai", "openrouter"]

        # With payment failover enabled, restriction is STILL enforced
        # because these models don't exist on other providers
        filtered = enforce_model_failover_rules(
            "openai/gpt-5.1", chain, allow_payment_failover=True
        )
        assert filtered == ["openai", "openrouter"]

    def test_payment_failover_does_not_bypass_suffix_lock(self):
        """Test that allow_payment_failover=True does NOT bypass suffix-based provider lock.

        Models with :free, :exacto, :extended suffixes only exist on OpenRouter,
        so they must always be routed to OpenRouter regardless of payment failover.
        """
        chain = ["openrouter", "cerebras", "huggingface"]
        # Without payment failover
        filtered = enforce_model_failover_rules("z-ai/glm-4.6:exacto", chain)
        assert filtered == ["openrouter"]

        # With payment failover enabled, restriction is STILL enforced
        # because these model variants only exist on OpenRouter
        filtered = enforce_model_failover_rules(
            "z-ai/glm-4.6:exacto", chain, allow_payment_failover=True
        )
        assert filtered == ["openrouter"]

    def test_payment_failover_no_effect_on_unlocked_models(self):
        """Test that allow_payment_failover has no effect on models without provider lock"""
        chain = ["openrouter", "cerebras", "huggingface"]
        # Non-locked models return full chain regardless of payment failover flag
        filtered_without = enforce_model_failover_rules("deepseek-ai/deepseek-v3", chain)
        filtered_with = enforce_model_failover_rules(
            "deepseek-ai/deepseek-v3", chain, allow_payment_failover=True
        )
        assert filtered_without == chain
        assert filtered_with == chain

    def test_bare_openai_model_names_route_to_native_first(self):
        """Test that bare OpenAI model names route to native OpenAI first, then OpenRouter.

        This ensures OpenAI models are served by the native OpenAI API first,
        with OpenRouter as fallback.
        """
        chain = ["openai", "openrouter", "cerebras", "huggingface", "featherless"]

        # All these bare model names should route to openai first, then openrouter
        bare_openai_models = [
            "gpt-4",
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-16k",
        ]

        for model in bare_openai_models:
            filtered = enforce_model_failover_rules(model, chain.copy())
            assert filtered == [
                "openai",
                "openrouter",
            ], f"Model '{model}' should route to ['openai', 'openrouter'], but got {filtered}"

    def test_bare_openai_model_names_fallback_to_openrouter(self):
        """Test that bare OpenAI model names fall back to OpenRouter if native not in chain."""
        chain = ["openrouter", "cerebras", "huggingface"]

        # Without openai in chain, should fall back to openrouter only
        filtered = enforce_model_failover_rules("gpt-4", chain.copy())
        assert filtered == ["openrouter"]

    def test_bare_openai_model_names_keep_restriction_with_payment_failover(self):
        """Test that bare OpenAI model names keep their restriction even with payment_failover=True.

        Bare model names like 'gpt-4' are aliased to 'openai/gpt-4', which can only be
        served by OpenAI or OpenRouter. Even with payment failover enabled, we cannot
        route these models to other providers.
        """
        chain = ["openai", "openrouter", "cerebras", "huggingface"]

        # With payment failover enabled, restriction is STILL enforced
        filtered = enforce_model_failover_rules("gpt-4", chain.copy(), allow_payment_failover=True)
        assert filtered == ["openai", "openrouter"]

    def test_bare_anthropic_model_names_route_to_native_first(self):
        """Test that bare Anthropic/Claude model names route to native Anthropic first, then OpenRouter.

        This ensures Claude models are served by the native Anthropic API first,
        with OpenRouter as fallback.
        """
        chain = ["anthropic", "openrouter", "cerebras", "huggingface", "featherless"]

        # All these bare model names should route to anthropic first, then openrouter
        bare_anthropic_models = [
            "claude-3-opus",
            "claude-3-sonnet",
            "claude-3-haiku",
            "claude-3.5-sonnet",
            "claude-3.5-haiku",
            "claude-3.7-sonnet",
            "claude-sonnet-4",
            "claude-opus-4",
            "claude-opus-4.5",
        ]

        for model in bare_anthropic_models:
            filtered = enforce_model_failover_rules(model, chain.copy())
            assert filtered == [
                "anthropic",
                "openrouter",
            ], f"Model '{model}' should route to ['anthropic', 'openrouter'], but got {filtered}"

    def test_bare_anthropic_model_names_fallback_to_openrouter(self):
        """Test that bare Anthropic model names fall back to OpenRouter if native not in chain."""
        chain = ["openrouter", "cerebras", "huggingface"]

        # Without anthropic in chain, should fall back to openrouter only
        filtered = enforce_model_failover_rules("claude-3-opus", chain.copy())
        assert filtered == ["openrouter"]

    def test_bare_anthropic_model_names_keep_restriction_with_payment_failover(self):
        """Test that bare Anthropic model names keep restriction with payment_failover=True.

        Bare model names like 'claude-3-opus' are aliased to 'anthropic/claude-3-opus',
        which can only be served by Anthropic or OpenRouter. Even with payment failover
        enabled, we cannot route these models to other providers.
        """
        chain = ["anthropic", "openrouter", "cerebras", "huggingface"]

        # With payment failover enabled, restriction is STILL enforced
        filtered = enforce_model_failover_rules(
            "claude-3-opus", chain.copy(), allow_payment_failover=True
        )
        assert filtered == ["anthropic", "openrouter"]


# ============================================================
# TEST CLASS: Failover Eligibility
# ============================================================


class TestShouldFailover:
    """Test failover eligibility detection"""

    def test_should_failover_401(self):
        """Test 401 Unauthorized triggers failover"""
        exc = HTTPException(status_code=401, detail="Unauthorized")
        assert should_failover(exc) is True

    def test_should_failover_403(self):
        """Test 403 Forbidden triggers failover"""
        exc = HTTPException(status_code=403, detail="Forbidden")
        assert should_failover(exc) is True

    def test_should_failover_404(self):
        """Test 404 Not Found triggers failover"""
        exc = HTTPException(status_code=404, detail="Not Found")
        assert should_failover(exc) is True

    def test_should_failover_429(self):
        """Test 429 Rate Limit does NOT trigger failover - client should retry"""
        exc = HTTPException(status_code=429, detail="Rate Limited")
        # 429 should be returned to client with Retry-After header, not trigger failover
        assert should_failover(exc) is False

    def test_should_failover_502(self):
        """Test 502 Bad Gateway triggers failover"""
        exc = HTTPException(status_code=502, detail="Bad Gateway")
        assert should_failover(exc) is True

    def test_should_failover_503(self):
        """Test 503 Service Unavailable triggers failover"""
        exc = HTTPException(status_code=503, detail="Service Unavailable")
        assert should_failover(exc) is True

    def test_should_failover_504(self):
        """Test 504 Gateway Timeout triggers failover"""
        exc = HTTPException(status_code=504, detail="Gateway Timeout")
        assert should_failover(exc) is True

    def test_should_not_failover_200(self):
        """Test 200 OK does not trigger failover"""
        exc = HTTPException(status_code=200, detail="OK")
        assert should_failover(exc) is False

    def test_should_not_failover_400(self):
        """Test 400 Bad Request does not trigger failover"""
        exc = HTTPException(status_code=400, detail="Bad Request")
        assert should_failover(exc) is False

    def test_should_not_failover_500(self):
        """Test 500 Internal Server Error does not trigger failover"""
        exc = HTTPException(status_code=500, detail="Internal Server Error")
        assert should_failover(exc) is False

    def test_failover_status_codes_constant(self):
        """Test FAILOVER_STATUS_CODES contains expected codes"""
        assert 401 in FAILOVER_STATUS_CODES
        assert (
            402 in FAILOVER_STATUS_CODES
        )  # Payment Required - failover when provider credits exhausted
        assert 403 in FAILOVER_STATUS_CODES
        assert 404 in FAILOVER_STATUS_CODES
        assert 502 in FAILOVER_STATUS_CODES
        assert 503 in FAILOVER_STATUS_CODES
        assert 504 in FAILOVER_STATUS_CODES

        # Verify codes that should NOT trigger failover
        assert 400 not in FAILOVER_STATUS_CODES
        assert 429 not in FAILOVER_STATUS_CODES  # 429 should be returned to client
        assert 500 not in FAILOVER_STATUS_CODES

    def test_should_failover_402(self):
        """Test 402 Payment Required triggers failover (e.g., provider credits exhausted)"""
        exc = HTTPException(status_code=402, detail="Insufficient credits")
        assert should_failover(exc) is True


# ============================================================
# TEST CLASS: Error Mapping - HTTPException
# ============================================================


class TestMapProviderErrorHTTPException:
    """Test mapping existing HTTPException instances"""

    def test_map_http_exception_passthrough(self):
        """Test HTTPException is passed through unchanged"""
        original = HTTPException(status_code=404, detail="Not found")
        mapped = map_provider_error("openrouter", "gpt-4", original)

        assert mapped is original
        assert mapped.status_code == 404
        assert mapped.detail == "Not found"

    def test_map_value_error(self):
        """Test ValueError is mapped to 400"""
        error = ValueError("Invalid parameter")
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 400
        assert "Invalid parameter" in mapped.detail


# ============================================================
# TEST CLASS: Error Mapping - HTTPX Exceptions
# ============================================================


class TestMapProviderErrorHTTPX:
    """Test mapping httpx exceptions"""

    def test_map_httpx_timeout_exception(self):
        """Test httpx.TimeoutException maps to 504"""
        error = httpx.TimeoutException("Request timeout")
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 504
        assert "timeout" in mapped.detail.lower()

    def test_map_asyncio_timeout_error(self):
        """Test asyncio.TimeoutError maps to 504"""
        error = TimeoutError()
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 504
        assert "timeout" in mapped.detail.lower()

    def test_map_httpx_request_error(self):
        """Test httpx.RequestError maps to 503"""
        error = httpx.RequestError("Connection failed")
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 503
        assert "unavailable" in mapped.detail.lower()

    def test_map_httpx_status_error_429_with_retry_after(self):
        """Test httpx 429 error preserves Retry-After header"""
        response = Mock()
        response.status_code = 429
        response.headers = {"retry-after": "60"}

        error = httpx.HTTPStatusError("Rate limited", request=Mock(), response=response)
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 429
        assert "rate limit" in mapped.detail.lower()
        assert mapped.headers is not None
        assert mapped.headers.get("Retry-After") == "60"

    def test_map_httpx_status_error_429_without_retry_after(self):
        """Test httpx 429 error without Retry-After header"""
        response = Mock()
        response.status_code = 429
        response.headers = {}

        error = httpx.HTTPStatusError("Rate limited", request=Mock(), response=response)
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 429
        assert "rate limit" in mapped.detail.lower()

    def test_map_httpx_status_error_401(self):
        """Test httpx 401 error maps to 500 (internal auth issue)"""
        response = Mock()
        response.status_code = 401
        response.headers = {}

        error = httpx.HTTPStatusError("Unauthorized", request=Mock(), response=response)
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 500
        assert "authentication" in mapped.detail.lower()

    def test_map_httpx_status_error_404(self):
        """Test httpx 404 error indicates model not found"""
        response = Mock()
        response.status_code = 404
        response.headers = {}

        error = httpx.HTTPStatusError("Not found", request=Mock(), response=response)
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 404
        assert "not found" in mapped.detail.lower()
        assert "gpt-4" in mapped.detail

    def test_map_httpx_status_error_4xx(self):
        """Test httpx 4xx errors map to 400"""
        for status in [400, 422]:
            response = Mock()
            response.status_code = status
            response.headers = {}

            error = httpx.HTTPStatusError("Client error", request=Mock(), response=response)
            mapped = map_provider_error("openrouter", "gpt-4", error)

            assert mapped.status_code == 400
            assert "rejected" in mapped.detail.lower()

    def test_map_httpx_status_error_5xx(self):
        """Test httpx 5xx errors map to 502"""
        for status in [500, 502, 503]:
            response = Mock()
            response.status_code = status
            response.headers = {}

            error = httpx.HTTPStatusError("Server error", request=Mock(), response=response)
            mapped = map_provider_error("openrouter", "gpt-4", error)

            assert mapped.status_code == 502
            assert "error" in mapped.detail.lower()


# ============================================================
# TEST CLASS: Error Mapping - OpenAI SDK Exceptions
# ============================================================


@pytest.mark.skipif(not OPENAI_SDK_AVAILABLE, reason="OpenAI SDK not installed")
class TestMapProviderErrorOpenAI:
    """Test mapping OpenAI SDK exceptions"""

    def test_map_api_connection_error(self):
        """Test APIConnectionError maps to 503"""
        # APIConnectionError requires a request parameter in newer OpenAI SDK versions
        mock_request = Mock()
        error = APIConnectionError(request=mock_request)
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 503
        assert "unavailable" in mapped.detail.lower()

    def test_map_api_timeout_error(self):
        """Test APITimeoutError maps to 504"""
        # APITimeoutError requires a request parameter in newer OpenAI SDK versions
        mock_request = Mock()
        error = APITimeoutError(request=mock_request)
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 504
        assert "timeout" in mapped.detail.lower()

    def test_map_rate_limit_error_with_retry_after_header(self):
        """Test RateLimitError with Retry-After in response headers"""
        response = Mock()
        response.headers = {"retry-after": "120"}

        error = RateLimitError("Rate limited", response=response, body=None)
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 429
        assert "rate limit" in mapped.detail.lower()
        assert mapped.headers is not None
        assert mapped.headers.get("Retry-After") == "120"

    def test_map_rate_limit_error_with_retry_after_body(self):
        """Test RateLimitError with retry_after in body"""
        # Create a mock response with no retry-after header
        response = Mock()
        response.headers = {}
        # RateLimitError requires message, response, and body parameters
        error = RateLimitError(message="Rate limited", response=response, body={"retry_after": 90})
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 429
        assert mapped.headers is not None
        assert mapped.headers.get("Retry-After") == "90"

    def test_map_authentication_error(self):
        """Test AuthenticationError maps to 401"""
        response = Mock()
        response.status_code = 401
        error = AuthenticationError("Invalid API key", response=response, body=None)
        error.status_code = 401
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_permission_denied_error(self):
        """Test PermissionDeniedError maps to 401"""
        response = Mock()
        response.status_code = 403
        error = PermissionDeniedError("Permission denied", response=response, body=None)
        error.status_code = 403
        mapped = map_provider_error("openrouter", "gpt-4", error)

        # Should map to 401 (authentication issue)
        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_not_found_error(self):
        """Test NotFoundError indicates model not available"""
        response = Mock()
        response.status_code = 404
        error = NotFoundError("Model not found", response=response, body=None)
        error.status_code = 404
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 404
        assert "not found" in mapped.detail.lower()
        assert "gpt-4" in mapped.detail
        assert "openrouter" in mapped.detail.lower()

    def test_map_bad_request_error(self):
        """Test BadRequestError maps to 400"""
        response = Mock()
        response.status_code = 400
        error = BadRequestError("Invalid request", response=response, body=None)
        error.status_code = 400
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 400
        assert "rejected" in mapped.detail.lower()

    def test_map_generic_openai_error(self):
        """Test generic OpenAIError maps to 502"""
        error = OpenAIError("Unknown error")
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 502

    def test_map_api_status_error_with_custom_status(self):
        """Test APIStatusError with custom status code"""
        response = Mock()
        response.status_code = 418  # I'm a teapot
        error = APIStatusError("Custom error", response=response, body=None)
        error.status_code = 418
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 418

    def test_map_api_status_error_invalid_status(self):
        """Test APIStatusError with invalid status code defaults to 500"""
        response = Mock()
        response.status_code = "invalid"
        error = APIStatusError("Error", response=response, body=None)
        error.status_code = "invalid"
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 500

    def test_map_api_status_error_404_generic(self):
        """Test generic APIStatusError with 404 status provides proper error message"""
        response = Mock()
        response.status_code = 404
        error = APIStatusError("Not Found", response=response, body=None)
        error.status_code = 404
        error.message = "Not Found"
        mapped = map_provider_error("openrouter", "test-model", error)

        assert mapped.status_code == 404
        assert "test-model" in mapped.detail
        assert "openrouter" in mapped.detail.lower()
        assert "not found" in mapped.detail.lower()
        # Should NOT be just "Not Found" - should include model and provider
        assert mapped.detail != "Not Found"

    def test_map_api_status_error_403_maps_to_401(self):
        """Test generic APIStatusError with 403 maps to 401 for consistency"""
        response = Mock()
        response.status_code = 403
        error = APIStatusError("Forbidden", response=response, body=None)
        error.status_code = 403
        error.message = "Forbidden"
        mapped = map_provider_error("openrouter", "test-model", error)

        # 403 should be mapped to 401 for auth error consistency
        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()
        assert "openrouter" in mapped.detail.lower()


# ============================================================
# TEST CLASS: Error Mapping - Cerebras SDK Exceptions
# ============================================================


@pytest.mark.skipif(not CEREBRAS_SDK_AVAILABLE, reason="Cerebras SDK not installed")
class TestMapProviderErrorCerebras:
    """Test mapping Cerebras SDK exceptions"""

    def test_map_cerebras_api_connection_error(self):
        """Test Cerebras APIConnectionError maps to 503"""
        # CerebrasAPIConnectionError requires a request parameter
        mock_request = Mock()
        error = CerebrasAPIConnectionError(request=mock_request)
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 503
        assert "unavailable" in mapped.detail.lower()

    def test_map_cerebras_rate_limit_error_with_retry_after_header(self):
        """Test Cerebras RateLimitError with Retry-After in response headers"""
        response = Mock()
        response.headers = {"retry-after": "120"}

        error = CerebrasRateLimitError("Rate limited", response=response, body=None)
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 429
        assert "rate limit" in mapped.detail.lower()
        assert mapped.headers is not None
        assert mapped.headers.get("Retry-After") == "120"

    def test_map_cerebras_rate_limit_error_with_retry_after_body(self):
        """Test Cerebras RateLimitError with retry_after in body"""
        # Create a mock response with no retry-after header
        response = Mock()
        response.headers = {}
        error = CerebrasRateLimitError(
            message="Rate limited", response=response, body={"retry_after": 90}
        )
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 429
        assert mapped.headers is not None
        assert mapped.headers.get("Retry-After") == "90"

    def test_map_cerebras_authentication_error(self):
        """Test Cerebras AuthenticationError maps to 401"""
        response = Mock()
        response.status_code = 401
        error = CerebrasAuthenticationError("Invalid API key", response=response, body=None)
        error.status_code = 401
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_cerebras_permission_denied_error(self):
        """Test Cerebras PermissionDeniedError maps to 401"""
        response = Mock()
        response.status_code = 403
        error = CerebrasPermissionDeniedError("Permission denied", response=response, body=None)
        error.status_code = 403
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        # Should map to 401 (authentication issue)
        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_cerebras_not_found_error(self):
        """Test Cerebras NotFoundError indicates model not available"""
        response = Mock()
        response.status_code = 404
        error = CerebrasNotFoundError("Model not found", response=response, body=None)
        error.status_code = 404
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 404
        assert "not found" in mapped.detail.lower()
        assert "llama-3.3-70b" in mapped.detail
        assert "cerebras" in mapped.detail.lower()

    def test_map_cerebras_bad_request_error(self):
        """Test Cerebras BadRequestError maps to 400"""
        response = Mock()
        response.status_code = 400
        error = CerebrasBadRequestError("Invalid request", response=response, body=None)
        error.status_code = 400
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 400
        assert "rejected" in mapped.detail.lower()

    def test_map_cerebras_api_status_error_with_custom_status(self):
        """Test Cerebras APIStatusError with custom status code"""
        response = Mock()
        response.status_code = 418  # I'm a teapot
        error = CerebrasAPIStatusError("Custom error", response=response, body=None)
        error.status_code = 418
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 418

    def test_map_cerebras_api_status_error_invalid_status(self):
        """Test Cerebras APIStatusError with invalid status code defaults to 500"""
        response = Mock()
        response.status_code = "invalid"
        error = CerebrasAPIStatusError("Error", response=response, body=None)
        error.status_code = "invalid"
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 500

    def test_map_cerebras_api_status_error_5xx(self):
        """Test Cerebras APIStatusError with 5xx status maps to service error"""
        response = Mock()
        response.status_code = 503
        error = CerebrasAPIStatusError("Service unavailable", response=response, body=None)
        error.status_code = 503
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 503
        assert "service error" in mapped.detail.lower()

    def test_map_cerebras_api_status_error_403_generic(self):
        """Test generic Cerebras APIStatusError with 403 maps to 401"""
        response = Mock()
        response.status_code = 403
        error = CerebrasAPIStatusError("Forbidden", response=response, body=None)
        error.status_code = 403
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_cerebras_api_status_error_404_generic(self):
        """Test generic Cerebras APIStatusError with 404 provides proper error message"""
        response = Mock()
        response.status_code = 404
        error = CerebrasAPIStatusError("Not Found", response=response, body=None)
        error.status_code = 404
        error.message = "Not Found"
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 404
        assert "llama-3.3-70b" in mapped.detail
        assert "cerebras" in mapped.detail.lower()


# ============================================================
# TEST CLASS: Error Mapping - Cerebras SDK Exceptions
# ============================================================


@pytest.mark.skipif(not CEREBRAS_SDK_AVAILABLE, reason="Cerebras SDK not installed")
class TestMapProviderErrorCerebras:
    """Test mapping Cerebras SDK exceptions"""

    def test_map_cerebras_api_connection_error(self):
        """Test Cerebras APIConnectionError maps to 503"""
        # CerebrasAPIConnectionError requires a request parameter
        mock_request = Mock()
        error = CerebrasAPIConnectionError(request=mock_request)
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 503
        assert "unavailable" in mapped.detail.lower()

    def test_map_cerebras_rate_limit_error_with_retry_after_header(self):
        """Test Cerebras RateLimitError with Retry-After in response headers"""
        response = Mock()
        response.headers = {"retry-after": "120"}

        error = CerebrasRateLimitError("Rate limited", response=response, body=None)
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 429
        assert "rate limit" in mapped.detail.lower()
        assert mapped.headers is not None
        assert mapped.headers.get("Retry-After") == "120"

    def test_map_cerebras_rate_limit_error_with_retry_after_body(self):
        """Test Cerebras RateLimitError with retry_after in body"""
        # Create a mock response with no retry-after header
        response = Mock()
        response.headers = {}
        error = CerebrasRateLimitError(
            message="Rate limited", response=response, body={"retry_after": 90}
        )
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 429
        assert mapped.headers is not None
        assert mapped.headers.get("Retry-After") == "90"

    def test_map_cerebras_authentication_error(self):
        """Test Cerebras AuthenticationError maps to 401"""
        response = Mock()
        response.status_code = 401
        error = CerebrasAuthenticationError("Invalid API key", response=response, body=None)
        error.status_code = 401
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_cerebras_permission_denied_error(self):
        """Test Cerebras PermissionDeniedError maps to 401"""
        response = Mock()
        response.status_code = 403
        error = CerebrasPermissionDeniedError("Permission denied", response=response, body=None)
        error.status_code = 403
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        # Should map to 401 (authentication issue)
        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_cerebras_not_found_error(self):
        """Test Cerebras NotFoundError indicates model not available"""
        response = Mock()
        response.status_code = 404
        error = CerebrasNotFoundError("Model not found", response=response, body=None)
        error.status_code = 404
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 404
        assert "not found" in mapped.detail.lower()
        assert "llama-3.3-70b" in mapped.detail
        assert "cerebras" in mapped.detail.lower()

    def test_map_cerebras_bad_request_error(self):
        """Test Cerebras BadRequestError maps to 400"""
        response = Mock()
        response.status_code = 400
        error = CerebrasBadRequestError("Invalid request", response=response, body=None)
        error.status_code = 400
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 400
        assert "rejected" in mapped.detail.lower()

    def test_map_cerebras_api_status_error_with_custom_status(self):
        """Test Cerebras APIStatusError with custom status code"""
        response = Mock()
        response.status_code = 418  # I'm a teapot
        error = CerebrasAPIStatusError("Custom error", response=response, body=None)
        error.status_code = 418
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 418

    def test_map_cerebras_api_status_error_invalid_status(self):
        """Test Cerebras APIStatusError with invalid status code defaults to 500"""
        response = Mock()
        response.status_code = "invalid"
        error = CerebrasAPIStatusError("Error", response=response, body=None)
        error.status_code = "invalid"
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 500

    def test_map_cerebras_api_status_error_5xx(self):
        """Test Cerebras APIStatusError with 5xx status maps to service error"""
        response = Mock()
        response.status_code = 503
        error = CerebrasAPIStatusError("Service unavailable", response=response, body=None)
        error.status_code = 503
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 503
        assert "service error" in mapped.detail.lower()

    def test_map_cerebras_api_status_error_403_generic(self):
        """Test generic Cerebras APIStatusError with 403 maps to 401"""
        response = Mock()
        response.status_code = 403
        error = CerebrasAPIStatusError("Forbidden", response=response, body=None)
        error.status_code = 403
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 401
        assert "authentication" in mapped.detail.lower()

    def test_map_cerebras_api_status_error_404_generic(self):
        """Test generic Cerebras APIStatusError with 404 provides proper error message"""
        response = Mock()
        response.status_code = 404
        error = CerebrasAPIStatusError("Not Found", response=response, body=None)
        error.status_code = 404
        error.message = "Not Found"
        mapped = map_provider_error("cerebras", "llama-3.3-70b", error)

        assert mapped.status_code == 404
        assert "llama-3.3-70b" in mapped.detail
        assert "cerebras" in mapped.detail.lower()


# ============================================================
# TEST CLASS: Error Mapping - Generic Exceptions
# ============================================================


class TestMapProviderErrorGeneric:
    """Test mapping generic exceptions"""

    def test_map_generic_exception(self):
        """Test generic Exception maps to 502"""
        error = Exception("Unknown error")
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 502
        assert "error" in mapped.detail.lower()

    def test_map_runtime_error(self):
        """Test RuntimeError maps to 502"""
        error = RuntimeError("Runtime error")
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 502

    def test_map_key_error(self):
        """Test KeyError maps to 502"""
        error = KeyError("missing_key")
        mapped = map_provider_error("openrouter", "gpt-4", error)

        assert mapped.status_code == 502


# ============================================================
# TEST CLASS: Integration Tests
# ============================================================


class TestProviderFailoverIntegration:
    """Test provider failover integration scenarios"""

    def test_complete_failover_chain_all_providers(self):
        """Test complete failover chain with all providers"""
        for provider in FALLBACK_ELIGIBLE_PROVIDERS:
            chain = build_provider_failover_chain(provider)

            # First should be the requested provider
            assert chain[0] == provider

            # Should include all fallback providers
            for fallback in FALLBACK_PROVIDER_PRIORITY:
                assert fallback in chain

            # Should have no duplicates
            assert len(chain) == len(set(chain))

    def test_failover_decision_matrix(self):
        """Test failover decisions for various error scenarios"""
        test_cases = [
            # (status_code, should_failover)
            (401, True),  # Auth error - try another provider
            (403, True),  # Permission denied - try another provider
            (404, True),  # Model not found - try another provider
            (429, False),  # Rate limited - return to client (don't failover)
            (502, True),  # Bad gateway - try another provider
            (503, True),  # Service unavailable - try another provider
            (504, True),  # Gateway timeout - try another provider
            (400, False),  # Bad request - don't failover
            (422, False),  # Validation error - don't failover
            (500, False),  # Internal server error - don't failover
        ]

        for status_code, expected_should_failover in test_cases:
            exc = HTTPException(status_code=status_code, detail="Test")
            result = should_failover(exc)
            assert (
                result == expected_should_failover
            ), f"Status {status_code} should {' ' if expected_should_failover else 'not '}failover"

    def test_error_mapping_preserves_provider_context(self):
        """Test error messages include provider and model context"""
        test_cases = [
            (
                httpx.HTTPStatusError(
                    "", request=Mock(), response=Mock(status_code=404, headers={})
                ),
                "openrouter",
                "gpt-4",
                404,
            ),
            (
                httpx.HTTPStatusError(
                    "", request=Mock(), response=Mock(status_code=401, headers={})
                ),
                "fireworks",
                "llama-2",
                500,
            ),
        ]

        for error, provider, model, expected_status in test_cases:
            mapped = map_provider_error(provider, model, error)
            assert mapped.status_code == expected_status

            # Verify context is preserved in detail
            if expected_status == 404:
                assert model in mapped.detail
                assert provider in mapped.detail.lower()


# ============================================================
# TEST CLASS: No Candidates Error Handling for Vertex AI
# ============================================================


class TestMapProviderErrorNoCandidates:
    """Test that 'no candidates' errors from Vertex AI trigger failover"""

    def test_no_candidates_error_triggers_failover(self):
        """Test 'no candidates' ValueError maps to 503 for failover"""
        exc = ValueError(
            "Vertex AI returned no candidates. Model 'gemini-3-flash-preview' returned "
            "no candidates without explicit block reason."
        )
        http_exc = map_provider_error("google-vertex", "gemini-3-flash-preview", exc)

        assert http_exc.status_code == 503  # Should trigger failover
        assert "no response candidates" in http_exc.detail.lower()

    def test_block_reason_error_triggers_failover(self):
        """Test that block reason errors map to 503"""
        exc = ValueError("Vertex AI returned no candidates. Block reason: SAFETY")
        http_exc = map_provider_error("google-vertex", "gemini-2.5-flash", exc)

        assert http_exc.status_code == 503

    def test_prompt_feedback_error_triggers_failover(self):
        """Test that promptFeedback errors map to 503"""
        exc = ValueError("No candidates, promptFeedback indicates content was blocked")
        http_exc = map_provider_error("google-vertex", "gemini-2.5-pro", exc)

        assert http_exc.status_code == 503

    def test_regular_value_error_does_not_trigger_failover(self):
        """Test that regular ValueErrors map to 400 (no failover)"""
        exc = ValueError("Invalid parameter: temperature must be between 0 and 2")
        http_exc = map_provider_error("google-vertex", "gemini-2.5-flash", exc)

        assert http_exc.status_code == 400  # Bad request, no failover

    def test_503_is_in_failover_status_codes(self):
        """Verify that 503 is in the set of status codes that trigger failover"""
        assert 503 in FAILOVER_STATUS_CODES

    def test_no_candidates_from_other_provider(self):
        """Test 'no candidates' detection works for any provider, not just google-vertex"""
        exc = ValueError("Response returned no candidates. Empty candidates array.")
        http_exc = map_provider_error("openrouter", "google/gemini-3-flash", exc)

        # Should still trigger failover even if provider is different
        # The error pattern is the key, not the provider
        assert http_exc.status_code == 503
