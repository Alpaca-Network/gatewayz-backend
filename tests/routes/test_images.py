#!/usr/bin/env python3
"""
Comprehensive tests for image generation endpoint

Tests cover:
- Image generation with DeepInfra
- Image generation with Fal.ai
- Authentication and authorization
- Credit validation and deduction
- Request validation
- Provider selection
- Response processing
- Error handling
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient

from src.main import app


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)


@pytest.fixture
def mock_user():
    """Sample user with sufficient credits"""
    return {
        'id': 1,
        'email': 'test@example.com',
        'credits': 1000.0,
        'api_key': 'test_api_key_12345'
    }


@pytest.fixture
def mock_user_no_credits():
    """Sample user with insufficient credits"""
    return {
        'id': 2,
        'email': 'broke@example.com',
        'credits': 0.01,  # Less than $0.025 (default) or $0.035 (sd3.5-large) needed for 1 image
        'api_key': 'broke_api_key_12345'
    }


@pytest.fixture
def mock_deepinfra_response():
    """Sample DeepInfra image generation response"""
    return {
        'created': 1677652288,
        'data': [
            {
                'url': 'https://cdn.deepinfra.com/image123.png',
                'b64_json': None
            }
        ]
    }


@pytest.fixture
def mock_fal_response():
    """Sample Fal.ai image generation response"""
    return {
        'created': 1677652288,
        'data': [
            {
                'url': 'https://fal.media/files/elephant/image789.png',
                'b64_json': None
            }
        ]
    }


@pytest.fixture
def valid_image_request():
    """Valid image generation request"""
    return {
        'prompt': 'A serene mountain landscape at sunset',
        'model': 'stable-diffusion-3.5-large',
        'size': '1024x1024',
        'n': 1,
        'quality': 'standard',
        'provider': 'deepinfra'
    }


# ============================================================
# TEST CLASS: Image Generation - Success Cases
# ============================================================

class TestImageGenerationSuccess:
    """Test successful image generation"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    @patch('src.routes.images.record_usage')
    @patch('src.routes.images.increment_api_key_usage')
    def test_generate_image_deepinfra_success(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        mock_deepinfra_response,
        valid_image_request
    ):
        """Test successful image generation with DeepInfra"""
        # Setup mocks
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = mock_deepinfra_response
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_deepinfra_response['data'],
            'provider': 'deepinfra',
            'model': 'stable-diffusion-3.5-large'
        }

        # Execute
        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_image_request
        )

        # Verify
        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert 'data' in data
        assert len(data['data']) == 1
        assert 'url' in data['data'][0]
        assert data['data'][0]['url'].startswith('https://')
        assert data['provider'] == 'deepinfra'
        assert data['model'] == 'stable-diffusion-3.5-large'

        # Verify gateway usage metadata
        assert 'gateway_usage' in data
        assert data['gateway_usage']['tokens_charged'] == 100  # Token-equivalent for rate limiting
        assert data['gateway_usage']['images_generated'] == 1
        # New billing fields
        assert 'cost_usd' in data['gateway_usage']
        assert 'cost_per_image' in data['gateway_usage']
        # Cost should be $0.035 for stable-diffusion-3.5-large on deepinfra
        assert data['gateway_usage']['cost_usd'] == 0.035
        assert data['gateway_usage']['cost_per_image'] == 0.035

        # Verify credits were deducted using actual USD cost (not token-based)
        mock_deduct_credits.assert_called_once_with('test_api_key_12345', 0.035)
        mock_record_usage.assert_called_once()
        mock_increment_usage.assert_called_once_with('test_api_key_12345')

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    @patch('src.routes.images.record_usage')
    @patch('src.routes.images.increment_api_key_usage')
    def test_generate_multiple_images(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user
    ):
        """Test generating multiple images"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = {
            'created': 1677652288,
            'data': [
                {'url': 'https://cdn.example.com/image1.png'},
                {'url': 'https://cdn.example.com/image2.png'},
                {'url': 'https://cdn.example.com/image3.png'}
            ]
        }
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_make_request.return_value['data'],
            'provider': 'deepinfra',
            'model': 'stable-diffusion-3.5-large'
        }

        request_data = {
            'prompt': 'Three different scenes',
            'model': 'stable-diffusion-3.5-large',
            'provider': 'deepinfra',
            'n': 3  # Generate 3 images
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=request_data
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data['data']) == 3
        assert data['gateway_usage']['tokens_charged'] == 300  # Token-equivalent: 100 * 3
        assert data['gateway_usage']['images_generated'] == 3
        # Cost should be $0.035 * 3 = $0.105 for stable-diffusion-3.5-large on deepinfra
        assert data['gateway_usage']['cost_usd'] == 0.105
        assert data['gateway_usage']['cost_per_image'] == 0.035

        # Verify credits deducted using actual USD cost (not token-based)
        mock_deduct_credits.assert_called_once_with('test_api_key_12345', 0.105)

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_fal_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    @patch('src.routes.images.record_usage')
    @patch('src.routes.images.increment_api_key_usage')
    def test_generate_image_fal_success(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        mock_fal_response
    ):
        """Test successful image generation with Fal.ai"""
        # Setup mocks
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = mock_fal_response
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_fal_response['data'],
            'provider': 'fal',
            'model': 'fal-ai/stable-diffusion-v15'
        }

        request_data = {
            'prompt': 'A serene mountain landscape at sunset',
            'model': 'fal-ai/stable-diffusion-v15',
            'provider': 'fal',
            'size': '1024x1024',
            'n': 1
        }

        # Execute
        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=request_data
        )

        # Verify
        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert 'data' in data
        assert len(data['data']) == 1
        assert 'url' in data['data'][0]
        assert data['data'][0]['url'].startswith('https://')
        assert data['provider'] == 'fal'
        assert data['model'] == 'fal-ai/stable-diffusion-v15'

        # Verify gateway usage metadata
        assert 'gateway_usage' in data
        assert data['gateway_usage']['tokens_charged'] == 100  # Token-equivalent for rate limiting
        assert data['gateway_usage']['images_generated'] == 1
        # New billing fields - fal-ai/stable-diffusion-v15 uses default price of $0.025
        assert 'cost_usd' in data['gateway_usage']
        assert 'cost_per_image' in data['gateway_usage']
        assert data['gateway_usage']['cost_usd'] == 0.025
        assert data['gateway_usage']['cost_per_image'] == 0.025

        # Verify credits were deducted using actual USD cost (not token-based)
        mock_deduct_credits.assert_called_once_with('test_api_key_12345', 0.025)
        mock_record_usage.assert_called_once()
        mock_increment_usage.assert_called_once_with('test_api_key_12345')


# ============================================================
# TEST CLASS: Image Generation - Authentication
# ============================================================

class TestImageGenerationAuth:
    """Test authentication and authorization"""

    @patch('src.routes.images.get_user')
    def test_generate_image_no_auth_header(self, mock_get_user, client, valid_image_request):
        """Test request without Authorization header"""
        response = client.post(
            '/v1/images/generations',
            json=valid_image_request
        )

        assert response.status_code in [401, 403]

    @patch('src.routes.images.get_user')
    def test_generate_image_invalid_api_key(self, mock_get_user, client, valid_image_request):
        """Test request with invalid API key"""
        mock_get_user.return_value = None

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer invalid_key'},
            json=valid_image_request
        )

        assert response.status_code == 401
        assert 'invalid' in response.json()['detail'].lower()


# ============================================================
# TEST CLASS: Image Generation - Credit Validation
# ============================================================

class TestImageGenerationCredits:
    """Test credit validation and deduction"""

    @patch('src.routes.images.get_user')
    def test_generate_image_insufficient_credits(
        self,
        mock_get_user,
        client,
        mock_user_no_credits,
        valid_image_request
    ):
        """Test request with insufficient credits"""
        mock_get_user.return_value = mock_user_no_credits

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer broke_api_key_12345'},
            json=valid_image_request
        )

        assert response.status_code == 402
        assert 'insufficient credits' in response.json()['detail'].lower()

    @patch('src.routes.images.get_user')
    def test_generate_image_insufficient_credits_multiple(
        self,
        mock_get_user,
        client,
        mock_user_no_credits
    ):
        """Test request for multiple images with insufficient credits"""
        mock_get_user.return_value = mock_user_no_credits

        request_data = {
            'prompt': 'Test prompt',
            'n': 5  # 5 images = $0.125 needed (default $0.025/image)
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer broke_api_key_12345'},
            json=request_data
        )

        assert response.status_code == 402
        detail = response.json()['detail'].lower()
        assert 'insufficient credits' in detail


# ============================================================
# TEST CLASS: Image Generation - Validation
# ============================================================

class TestImageGenerationValidation:
    """Test request validation"""

    def test_generate_image_missing_prompt(self, client):
        """Test request without required prompt"""
        request_data = {
            'model': 'stable-diffusion-3.5-large',
            'n': 1
            # prompt is missing
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_key'},
            json=request_data
        )

        assert response.status_code == 422

    def test_generate_image_empty_prompt(self, client):
        """Test request with empty prompt"""
        request_data = {
            'prompt': '',
            'n': 1
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_key'},
            json=request_data
        )

        assert response.status_code == 422

    def test_generate_image_invalid_size(self, client):
        """Test request with invalid size"""
        request_data = {
            'prompt': 'Test prompt',
            'size': 'invalid_size',
            'n': 1
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_key'},
            json=request_data
        )

        # Should validate size format
        assert response.status_code in [400, 422]

    def test_generate_image_invalid_n(self, client):
        """Test request with invalid n value"""
        request_data = {
            'prompt': 'Test prompt',
            'n': 0  # Invalid: must be positive
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_key'},
            json=request_data
        )

        assert response.status_code == 422


# ============================================================
# TEST CLASS: Image Generation - Provider Selection
# ============================================================

class TestImageGenerationProviders:
    """Test provider selection and routing"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    @patch('src.routes.images.record_usage')
    @patch('src.routes.images.increment_api_key_usage')
    def test_default_provider_is_deepinfra(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        mock_deepinfra_response
    ):
        """Test that DeepInfra is the default provider"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = mock_deepinfra_response
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_deepinfra_response['data'],
            'provider': 'deepinfra',
            'model': 'stable-diffusion-3.5-large'
        }

        # Request without specifying provider
        request_data = {
            'prompt': 'Test prompt',
            'n': 1
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=request_data
        )

        assert response.status_code == 200
        # Verify DeepInfra was called
        mock_make_request.assert_called_once()

    @patch('src.routes.images.get_user')
    def test_unsupported_provider_error(self, mock_get_user, client, mock_user):
        """Test error handling for unsupported providers"""
        mock_get_user.return_value = mock_user

        request_data = {
            'prompt': 'Test prompt',
            'provider': 'unsupported_provider',
            'n': 1
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=request_data
        )

        assert response.status_code == 400
        assert 'not supported' in response.json()['detail'].lower()


# ============================================================
# TEST CLASS: Image Generation - Response Processing
# ============================================================

class TestImageGenerationResponseProcessing:
    """Test response processing"""

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    @patch('src.routes.images.record_usage')
    @patch('src.routes.images.increment_api_key_usage')
    def test_response_includes_gateway_usage(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        mock_deepinfra_response,
        valid_image_request
    ):
        """Test that response includes gateway usage metadata"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = mock_deepinfra_response
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_deepinfra_response['data'],
            'provider': 'deepinfra',
            'model': 'stable-diffusion-3.5-large'
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_image_request
        )

        assert response.status_code == 200
        data = response.json()

        # Verify gateway usage metadata
        assert 'gateway_usage' in data
        gateway_usage = data['gateway_usage']
        assert 'tokens_charged' in gateway_usage
        assert 'request_ms' in gateway_usage
        assert 'user_balance_after' in gateway_usage
        assert 'images_generated' in gateway_usage
        # New billing fields
        assert 'cost_usd' in gateway_usage
        assert 'cost_per_image' in gateway_usage

        # Verify values - cost is $0.035 for stable-diffusion-3.5-large on deepinfra
        assert gateway_usage['tokens_charged'] == 100  # Token-equivalent for rate limiting
        assert gateway_usage['images_generated'] == 1
        assert gateway_usage['cost_usd'] == 0.035
        assert gateway_usage['cost_per_image'] == 0.035
        # Balance is fetched after deduction for accuracy; mock returns same user so balance = 1000 - 0.035
        assert gateway_usage['user_balance_after'] == 999.965
        # New field indicating whether fallback pricing was used
        assert 'used_fallback_pricing' in gateway_usage

    @patch('src.security.deps.validate_api_key_security')
    @patch('src.services.user_lookup_cache.get_user')
    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    @patch('src.routes.images.record_usage')
    @patch('src.routes.images.increment_api_key_usage')
    def test_response_timing_tracked(
        self,
        mock_increment_usage,
        mock_record_usage,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        mock_get_user_cache,
        mock_validate_key,
        client,
        mock_user,
        mock_deepinfra_response,
        valid_image_request
    ):
        """Test that request timing is tracked"""
        mock_validate_key.return_value = 'test_api_key_12345'
        mock_get_user_cache.return_value = mock_user
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = mock_deepinfra_response
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_deepinfra_response['data'],
            'provider': 'deepinfra',
            'model': 'stable-diffusion-3.5-large'
        }

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_image_request
        )

        assert response.status_code == 200
        data = response.json()

        # Verify timing is recorded
        assert 'request_ms' in data['gateway_usage']
        assert data['gateway_usage']['request_ms'] > 0


# ============================================================
# TEST CLASS: Image Generation - Error Handling
# ============================================================

class TestImageGenerationErrorHandling:
    """Test error handling"""

    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    def test_provider_error_handling(
        self,
        mock_make_request,
        mock_get_user,
        client,
        mock_user,
        valid_image_request
    ):
        """Test handling of provider errors"""
        mock_get_user.return_value = mock_user
        mock_make_request.side_effect = Exception("Provider error")

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_image_request
        )

        assert response.status_code == 500
        assert 'failed' in response.json()['detail'].lower()

    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    def test_credit_deduction_failure_returns_402(
        self,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        client,
        mock_user,
        mock_deepinfra_response,
        valid_image_request
    ):
        """Test that credit deduction failures return 402 Payment Required.

        CRITICAL: Users must NOT receive free images when credit deduction fails.
        This test verifies that billing failures prevent the response from being returned.
        """
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = mock_deepinfra_response
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_deepinfra_response['data'],
            'provider': 'deepinfra',
            'model': 'stable-diffusion-3.5-large'
        }
        mock_deduct_credits.side_effect = ValueError("Insufficient credits")

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_image_request
        )

        # Request must fail with 402 Payment Required when credits can't be deducted
        # Users should NOT receive free images
        assert response.status_code == 402
        assert 'payment required' in response.json()['detail'].lower()

    @patch('src.routes.images.get_user')
    @patch('src.routes.images.make_deepinfra_image_request')
    @patch('src.routes.images.process_image_generation_response')
    @patch('src.routes.images.deduct_credits')
    def test_unexpected_billing_error_returns_500(
        self,
        mock_deduct_credits,
        mock_process_response,
        mock_make_request,
        mock_get_user,
        client,
        mock_user,
        mock_deepinfra_response,
        valid_image_request
    ):
        """Test that unexpected billing errors return 500 and don't give away free images.

        CRITICAL: Any billing error should prevent free images from being returned.
        """
        mock_get_user.return_value = mock_user
        mock_make_request.return_value = mock_deepinfra_response
        mock_process_response.return_value = {
            'created': 1677652288,
            'data': mock_deepinfra_response['data'],
            'provider': 'deepinfra',
            'model': 'stable-diffusion-3.5-large'
        }
        # Simulate an unexpected database/network error during credit deduction
        mock_deduct_credits.side_effect = RuntimeError("Database connection failed")

        response = client.post(
            '/v1/images/generations',
            headers={'Authorization': 'Bearer test_api_key_12345'},
            json=valid_image_request
        )

        # Request must fail with 500 when an unexpected billing error occurs
        # Users should NOT receive free images
        assert response.status_code == 500
        assert 'billing error' in response.json()['detail'].lower()


# ============================================================
# TEST CLASS: Image Generation - Pricing
# ============================================================

class TestImageGenerationPricing:
    """Test pricing calculations and fallback behavior"""

    def test_get_image_cost_known_model(self):
        """Test cost calculation for known model"""
        from src.routes.images import get_image_cost

        total_cost, cost_per_image, is_fallback = get_image_cost(
            "deepinfra", "stable-diffusion-3.5-large", 1
        )

        assert total_cost == 0.035
        assert cost_per_image == 0.035
        assert is_fallback is False

    def test_get_image_cost_multiple_images(self):
        """Test cost calculation for multiple images"""
        from src.routes.images import get_image_cost

        total_cost, cost_per_image, is_fallback = get_image_cost(
            "deepinfra", "stable-diffusion-3.5-large", 3
        )

        assert total_cost == 0.105
        assert cost_per_image == 0.035
        assert is_fallback is False

    def test_get_image_cost_unknown_model_uses_provider_default(self):
        """Test that unknown models use provider default pricing and flag as fallback"""
        from src.routes.images import get_image_cost

        total_cost, cost_per_image, is_fallback = get_image_cost(
            "deepinfra", "unknown-model-xyz", 1
        )

        # Should use deepinfra default of 0.025
        assert cost_per_image == 0.025
        assert total_cost == 0.025
        assert is_fallback is True  # Flag that fallback pricing was used

    def test_get_image_cost_unknown_provider_uses_conservative_default(self):
        """Test that unknown providers use conservative high default to avoid revenue loss"""
        from src.routes.images import get_image_cost, UNKNOWN_PROVIDER_DEFAULT_COST

        total_cost, cost_per_image, is_fallback = get_image_cost(
            "unknown-provider", "some-model", 1
        )

        # Should use conservative high default
        assert cost_per_image == UNKNOWN_PROVIDER_DEFAULT_COST
        assert cost_per_image == 0.05  # Verify the actual value
        assert is_fallback is True

    def test_get_image_cost_fal_flux_models(self):
        """Test pricing for Fal flux models"""
        from src.routes.images import get_image_cost

        # Schnell (cheapest)
        total, per_image, fallback = get_image_cost("fal", "flux/schnell", 1)
        assert per_image == 0.003
        assert fallback is False

        # Also test with fal-ai prefix
        total, per_image, fallback = get_image_cost("fal", "fal-ai/flux/schnell", 1)
        assert per_image == 0.003
        assert fallback is False

        # Dev
        total, per_image, fallback = get_image_cost("fal", "flux/dev", 1)
        assert per_image == 0.025
        assert fallback is False

        # Pro
        total, per_image, fallback = get_image_cost("fal", "flux-pro", 1)
        assert per_image == 0.05
        assert fallback is False

        # Pro v1.1 with fal-ai prefix
        total, per_image, fallback = get_image_cost("fal", "fal-ai/flux-pro/v1.1", 1)
        assert per_image == 0.05
        assert fallback is False

        # Pro v1.1-ultra (most expensive Fal model)
        total, per_image, fallback = get_image_cost("fal", "fal-ai/flux-pro/v1.1-ultra", 1)
        assert per_image == 0.06
        assert fallback is False

        # Stable diffusion models with fal-ai prefix
        total, per_image, fallback = get_image_cost("fal", "fal-ai/stable-diffusion-v15", 1)
        assert per_image == 0.02
        assert fallback is False
