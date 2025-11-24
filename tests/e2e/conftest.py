"""
Playwright E2E test configuration and fixtures.

This module provides reusable fixtures for end-to-end testing
of API endpoints using HTTP requests.
"""

import asyncio
import os
from typing import AsyncGenerator

import pytest
from httpx import AsyncClient
from src.main import create_app


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def app():
    """Create FastAPI app instance."""
    app = create_app()
    yield app


@pytest.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Create async HTTP client for API testing."""
    async with AsyncClient(app=app, base_url="http://localhost:8000") as ac:
        yield ac


@pytest.fixture
def api_key():
    """Get test API key from environment or use default."""
    return os.getenv("TEST_API_KEY", "test-api-key-123")


@pytest.fixture
def auth_headers(api_key):
    """Return authorization headers for requests."""
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def base_chat_payload():
    """Base chat completion payload."""
    return {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is 2+2?"},
        ],
    }


@pytest.fixture
def base_messages_payload():
    """Base Anthropic messages payload."""
    return {
        "model": "claude-3.5-sonnet",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "What is 2+2?"},
        ],
    }


@pytest.fixture
def base_responses_payload():
    """Base unified responses payload."""
    return {
        "model": "gpt-3.5-turbo",
        "input": [
            {"role": "user", "content": "What is 2+2?"},
        ],
    }


@pytest.fixture
def base_image_payload():
    """Base image generation payload."""
    return {
        "prompt": "A beautiful sunset over the ocean",
        "model": "stable-diffusion-3.5-large",
        "n": 1,
        "size": "1024x1024",
    }
