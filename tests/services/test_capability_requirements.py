"""Tests for src.services.capability_requirements (gatewayz-backend#2212)."""

from src.services.capability_requirements import (
    RequiredCapabilities,
    extract_required_capabilities,
)


def test_no_tools_no_images_yields_no_capability_names():
    result = extract_required_capabilities(messages=[{"role": "user", "content": "hi"}])

    assert result.capability_names == frozenset()
    assert result.needs_json is False


def test_tools_present_requires_tools_capability():
    result = extract_required_capabilities(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "get_weather"}}],
    )

    assert "tools" in result.capability_names


def test_empty_tools_list_does_not_require_tools_capability():
    result = extract_required_capabilities(messages=[], tools=[])

    assert "tools" not in result.capability_names


def test_openai_style_image_url_requires_vision_capability():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
            ],
        }
    ]

    result = extract_required_capabilities(messages=messages)

    assert "vision" in result.capability_names


def test_anthropic_style_image_block_requires_vision_capability():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
                },
            ],
        }
    ]

    result = extract_required_capabilities(messages=messages)

    assert "vision" in result.capability_names


def test_plain_string_content_does_not_require_vision():
    result = extract_required_capabilities(messages=[{"role": "user", "content": "just text"}])

    assert "vision" not in result.capability_names


def test_response_format_sets_needs_json():
    result = extract_required_capabilities(messages=[], response_format={"type": "json_object"})

    assert result.needs_json is True


def test_estimates_input_tokens_from_string_content():
    result = extract_required_capabilities(messages=[{"role": "user", "content": "a" * 400}])

    assert result.estimated_input_tokens == 100


def test_estimates_input_tokens_from_multipart_text_blocks():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "a" * 40},
                {"type": "text", "text": "b" * 40},
            ],
        }
    ]

    result = extract_required_capabilities(messages=messages)

    assert result.estimated_input_tokens == 20


def test_max_cost_per_1k_passes_through_unchanged():
    result = extract_required_capabilities(messages=[], max_cost_per_1k=0.5)

    assert result.max_cost_per_1k == 0.5


def test_malformed_messages_do_not_raise():
    messages = [None, "not-a-dict", {"role": "user", "content": [None, {"no_type": True}]}]

    result = extract_required_capabilities(messages=messages)

    assert isinstance(result, RequiredCapabilities)
    assert result.capability_names == frozenset()
