"""
End-to-end tests for /v1/images endpoint (Image generation).

These tests verify:
- Image generation requests can be sent and received
- Multiple image generation works
- Provider parameter works (DeepInfra, Google Vertex, Fal)
- Size and model parameters are respected
- Error handling for invalid inputs
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestImagesE2E:
    """E2E tests for image generation endpoint."""

    async def test_images_basic_request(
        self, client: AsyncClient, auth_headers: dict, base_image_payload: dict
    ):
        """Test basic image generation request and response."""
        response = await client.post(
            "/v1/images/generations",
            json=base_image_payload,
            headers=auth_headers,
        )

        # Image generation may require credits; check for success or appropriate error
        assert response.status_code in [200, 402, 503]
        if response.status_code == 200:
            data = response.json()
            assert "data" in data
            assert len(data["data"]) > 0

    async def test_images_with_single_image(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with single image."""
        payload = {
            "prompt": "A beautiful sunset",
            "n": 1,
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 402, 503]
        if response.status_code == 200:
            data = response.json()
            assert len(data["data"]) == 1

    async def test_images_with_multiple_images(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with multiple images."""
        payload = {
            "prompt": "A forest landscape",
            "n": 3,
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 402, 503, 400]
        if response.status_code == 200:
            data = response.json()
            assert len(data["data"]) == 3

    async def test_images_different_sizes(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with different sizes."""
        sizes = ["256x256", "512x512", "1024x1024"]

        for size in sizes:
            payload = {
                "prompt": "A test image",
                "n": 1,
                "size": size,
            }

            response = await client.post(
                "/v1/images/generations",
                json=payload,
                headers=auth_headers,
            )

            assert response.status_code in [200, 402, 503, 400]

    async def test_images_with_deepinfra_provider(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with DeepInfra provider."""
        payload = {
            "prompt": "A beautiful landscape",
            "model": "stable-diffusion-3.5-large",
            "n": 1,
            "size": "1024x1024",
            "provider": "deepinfra",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 402, 503]

    async def test_images_with_google_vertex_provider(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with Google Vertex AI provider."""
        payload = {
            "prompt": "A futuristic city",
            "model": "imagegeneration",
            "n": 1,
            "size": "1024x1024",
            "provider": "google-vertex",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        # Google Vertex requires additional setup
        assert response.status_code in [200, 402, 503, 400]

    async def test_images_with_fal_provider(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with Fal.ai provider."""
        payload = {
            "prompt": "A serene ocean view",
            "model": "stable-diffusion-3.5-large",
            "n": 1,
            "size": "1024x1024",
            "provider": "fal",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 402, 503]

    async def test_images_invalid_provider(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with unsupported provider."""
        payload = {
            "prompt": "Test",
            "n": 1,
            "size": "1024x1024",
            "provider": "invalid-provider",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        # Should reject invalid provider
        assert response.status_code == 400
        data = response.json()
        assert "not supported" in data.get("detail", "").lower()

    async def test_images_missing_prompt(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation without prompt."""
        payload = {
            "n": 1,
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    async def test_images_empty_prompt(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with empty prompt."""
        payload = {
            "prompt": "",
            "n": 1,
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    async def test_images_missing_api_key(
        self, client: AsyncClient, base_image_payload: dict
    ):
        """Test image generation without API key."""
        response = await client.post(
            "/v1/images/generations",
            json=base_image_payload,
        )

        assert response.status_code == 401

    async def test_images_very_long_prompt(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with very long prompt."""
        long_prompt = "A beautiful image. " * 500  # Very long prompt

        payload = {
            "prompt": long_prompt,
            "n": 1,
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        # Should either succeed or fail with appropriate error
        assert response.status_code in [200, 402, 503, 400, 413]

    async def test_images_special_characters_in_prompt(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with special characters in prompt."""
        payload = {
            "prompt": "A beautiful image with émojis 🎨 and special chars: @#$%^&*()",
            "n": 1,
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 402, 503]

    async def test_images_invalid_size(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with invalid size."""
        payload = {
            "prompt": "Test",
            "n": 1,
            "size": "invalid-size",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        # May fail validation or silently fall back to default
        assert response.status_code in [200, 400, 422, 402, 503]

    async def test_images_invalid_number_of_images(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation with invalid n parameter."""
        payload = {
            "prompt": "Test",
            "n": 0,  # Invalid: must be at least 1
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422  # Validation error

    async def test_images_default_size(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation without size (should use default)."""
        payload = {
            "prompt": "A default sized image",
            "n": 1,
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code in [200, 402, 503]

    async def test_images_default_provider(
        self, client: AsyncClient, auth_headers: dict
    ):
        """Test image generation without provider (should default to deepinfra)."""
        payload = {
            "prompt": "Test with default provider",
            "n": 1,
            "size": "1024x1024",
        }

        response = await client.post(
            "/v1/images/generations",
            json=payload,
            headers=auth_headers,
        )

        # Default provider is DeepInfra
        assert response.status_code in [200, 402, 503]
