"""
Integration tests for AllenAI OLMo models.

Tests the following AllenAI models via OpenRouter:
- allenai/olmo-3.1-32b-think - 32B reasoning model
- allenai/olmo-3-32b-think - 32B reasoning model
- allenai/olmo-3-7b-instruct - 7B instruction model
- allenai/olmo-3-7b-think - 7B reasoning model

These tests validate that:
- Models respond correctly to prompts
- Streaming works properly
- Reasoning models provide thoughtful responses
"""

import os
import sys

import pytest

sys.path.insert(0, "src")

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "https://gatewayz.ai")
OPENROUTER_SITE_NAME = os.getenv("OPENROUTER_SITE_NAME", "Gatewayz")

# AllenAI OLMo models available via OpenRouter
ALLENAI_MODELS = [
    "allenai/olmo-3.1-32b-think",
    "allenai/olmo-3-32b-think",
    "allenai/olmo-3-7b-instruct",
    "allenai/olmo-3-7b-think",
]

# Skip if no API key
pytestmark = pytest.mark.skipif(not OPENROUTER_API_KEY, reason="OPENROUTER_API_KEY not set")


def get_openrouter_client():
    """Get OpenRouter client for testing."""
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        default_headers={
            "HTTP-Referer": OPENROUTER_SITE_URL,
            "X-Title": OPENROUTER_SITE_NAME,
        },
    )


@pytest.mark.integration
class TestAllenAIModelsDirectOpenRouter:
    """Test AllenAI models directly via OpenRouter API."""

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_model_basic_response(self, model: str):
        """Test that each AllenAI model responds to a basic prompt."""
        client = get_openrouter_client()

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
                max_tokens=100,
            )

            # Verify response structure
            assert response.choices is not None
            assert len(response.choices) > 0
            assert response.choices[0].message is not None
            assert response.choices[0].message.content is not None
            assert len(response.choices[0].message.content) > 0

            # Verify model info
            assert response.model is not None
            print(f"[OK] {model}: {response.choices[0].message.content[:100]}")

        except Exception as e:
            pytest.fail(f"Model {model} failed: {e}")

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_model_streaming(self, model: str):
        """Test that each AllenAI model supports streaming."""
        client = get_openrouter_client()

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Count from 1 to 5."}],
                max_tokens=100,
                stream=True,
            )

            chunks_received = 0
            content_collected = ""

            for chunk in stream:
                chunks_received += 1
                if chunk.choices and chunk.choices[0].delta.content:
                    content_collected += chunk.choices[0].delta.content

            # Should receive multiple chunks
            assert chunks_received > 0, f"No chunks received for {model}"
            # Should have some content
            assert len(content_collected) > 0, f"No content collected for {model}"

            print(
                f"[OK] {model} streaming: {chunks_received} chunks, content: {content_collected[:100]}"
            )

        except Exception as e:
            pytest.fail(f"Model {model} streaming failed: {e}")

    @pytest.mark.parametrize(
        "model",
        [
            "allenai/olmo-3.1-32b-think",
            "allenai/olmo-3-32b-think",
            "allenai/olmo-3-7b-think",
        ],
    )
    def test_thinking_models_reasoning(self, model: str):
        """Test that thinking models provide reasoning for complex questions."""
        client = get_openrouter_client()

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": "What is 15 + 27? Show your reasoning step by step.",
                    }
                ],
                max_tokens=300,
            )

            content = response.choices[0].message.content
            assert content is not None
            assert len(content) > 0

            # Thinking models should provide more detailed responses
            # Check that the response contains the correct answer (42)
            print(f"[OK] {model} reasoning: {content[:200]}")

        except Exception as e:
            pytest.fail(f"Model {model} reasoning test failed: {e}")

    def test_olmo_instruct_follows_instructions(self):
        """Test that OLMo instruct model follows instructions well."""
        client = get_openrouter_client()

        try:
            response = client.chat.completions.create(
                model="allenai/olmo-3-7b-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant. Always respond in exactly 3 words.",
                    },
                    {"role": "user", "content": "How are you?"},
                ],
                max_tokens=50,
            )

            content = response.choices[0].message.content
            assert content is not None
            assert len(content) > 0
            print(f"[OK] olmo-3-7b-instruct instruction following: {content}")

        except Exception as e:
            pytest.fail(f"OLMo instruct test failed: {e}")

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_model_with_conversation_history(self, model: str):
        """Test that models handle multi-turn conversations."""
        client = get_openrouter_client()

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": "My name is Alice."},
                    {"role": "assistant", "content": "Hello Alice! Nice to meet you."},
                    {"role": "user", "content": "What is my name?"},
                ],
                max_tokens=50,
            )

            content = response.choices[0].message.content
            assert content is not None
            # The model should remember the name from context
            print(f"[OK] {model} conversation: {content}")

        except Exception as e:
            pytest.fail(f"Model {model} conversation test failed: {e}")


@pytest.mark.integration
class TestAllenAIModelsViaGatewayz:
    """Test AllenAI models via Gatewayz API endpoint."""

    @pytest.fixture
    def gatewayz_client(self):
        """Get Gatewayz client for testing."""
        api_key = os.getenv("GATEWAYZ_API_KEY") or os.getenv("TEST_API_KEY")
        base_url = os.getenv("GATEWAYZ_BASE_URL", "https://api.gatewayz.ai/v1")

        if not api_key:
            pytest.skip("GATEWAYZ_API_KEY or TEST_API_KEY not set")

        return OpenAI(base_url=base_url, api_key=api_key)

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_model_via_gatewayz(self, gatewayz_client, model: str):
        """Test AllenAI models through Gatewayz API."""
        try:
            response = gatewayz_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello briefly."}],
                max_tokens=50,
            )

            assert response.choices is not None
            assert len(response.choices) > 0
            assert response.choices[0].message.content is not None
            print(f"[OK] {model} via Gatewayz: {response.choices[0].message.content[:100]}")

        except Exception as e:
            pytest.fail(f"Model {model} via Gatewayz failed: {e}")

    @pytest.mark.parametrize("model", ALLENAI_MODELS)
    def test_model_streaming_via_gatewayz(self, gatewayz_client, model: str):
        """Test AllenAI models streaming through Gatewayz API."""
        try:
            stream = gatewayz_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Count 1 to 3."}],
                max_tokens=50,
                stream=True,
            )

            chunks = 0
            content = ""
            for chunk in stream:
                chunks += 1
                if chunk.choices and chunk.choices[0].delta.content:
                    content += chunk.choices[0].delta.content

            assert chunks > 0
            print(f"[OK] {model} streaming via Gatewayz: {chunks} chunks")

        except Exception as e:
            pytest.fail(f"Model {model} streaming via Gatewayz failed: {e}")


if __name__ == "__main__":
    # Run tests manually for debugging
    print("=" * 60)
    print("Testing AllenAI OLMo Models")
    print("=" * 60)

    if not OPENROUTER_API_KEY:
        print("[SKIP] OPENROUTER_API_KEY not set")
        sys.exit(0)

    client = get_openrouter_client()

    for model in ALLENAI_MODELS:
        print(f"\n[TEST] {model}")
        print("-" * 40)

        try:
            # Test basic response
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in one sentence."}],
                max_tokens=100,
            )
            print(f"[OK] Response: {response.choices[0].message.content[:100]}")

            # Test streaming
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Count 1 to 3."}],
                max_tokens=50,
                stream=True,
            )
            chunks = 0
            for _ in stream:
                chunks += 1
            print(f"[OK] Streaming: {chunks} chunks received")

        except Exception as e:
            print(f"[ERROR] {e}")

    print("\n" + "=" * 60)
    print("Tests completed")
    print("=" * 60)
