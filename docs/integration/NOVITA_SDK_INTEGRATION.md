# Novita AI SDK Integration Guide

## Overview

The Gatewayz API integrates with Novita AI through two different APIs:

1. **OpenAI-Compatible API** - For LLM chat completions (Qwen, DeepSeek, Llama, etc.)
2. **Native Novita API** - For image/video generation (via `novita-client` SDK)

## Architecture

### LLM Models (Chat Completions)

Novita provides an OpenAI-compatible API endpoint for LLM models:
- **Endpoint**: `https://api.novita.ai/v3/openai`
- **Access Method**: OpenAI Python SDK
- **Models**: Qwen3, DeepSeek V3, Llama 3.3, Mistral Nemo, etc.

### Image/Video Generation

Novita provides a native API for image and video generation:
- **Endpoint**: `https://api.novita.ai/v3/`
- **Access Method**: Official `novita-client` Python SDK
- **Capabilities**: txt2img, img2img, img2video, inpainting, ControlNet, etc.

## Installation

The `novita-client` SDK is included in `requirements.txt`:

```bash
pip install novita-client>=0.5.0
```

## Configuration

Set your Novita API key in the environment:

```bash
export NOVITA_API_KEY=your_api_key_here
```

Or add it to your `.env` file:

```
NOVITA_API_KEY=your_api_key_here
```

## Usage

### 1. Fetching LLM Models

```python
from src.services.novita_client import fetch_models_from_novita

# Fetch available LLM models from OpenAI-compatible endpoint
models = fetch_models_from_novita()

# Models returned include:
# - qwen3-235b-thinking
# - qwen3-max
# - deepseek-v3
# - llama-3.3-70b
# - mistral-nemo
```

### 2. Getting the Novita SDK Client

```python
from src.services.novita_client import get_novita_sdk_client, NOVITA_SDK_AVAILABLE

# Check if SDK is available
if NOVITA_SDK_AVAILABLE:
    client = get_novita_sdk_client()
    print("Novita SDK client initialized")
else:
    print("Install novita-client: pip install novita-client")
```

### 3. Fetching Image Generation Models

```python
from src.services.novita_client import fetch_image_models_from_novita_sdk

# Fetch image generation models (checkpoints, LoRAs, VAE, ControlNet, etc.)
image_models = fetch_image_models_from_novita_sdk()

if image_models:
    print(f"Found {len(image_models)} image models")
    # Filter by type, tags, etc.
```

### 4. Generating Images

```python
from src.services.novita_client import generate_image_with_novita_sdk

# Generate an image
response = generate_image_with_novita_sdk(
    prompt="a beautiful sunset over mountains",
    model_name="dreamshaper_8_93211.safetensors",
    width=512,
    height=512,
    steps=30,
    guidance_scale=7.5,
    negative_prompt="ugly, blurry, low quality"
)

# Access generated images
for img in response.images_encoded:
    # img is base64-encoded image data
    pass
```

## Advanced Features

### Using LoRAs

```python
from novita_client import Txt2ImgV3LoRA

response = generate_image_with_novita_sdk(
    prompt="a cute anime character",
    model_name="MeinaHentai_V5.safetensors",
    loras=[
        Txt2ImgV3LoRA(model_name="anime_style_lora", strength=0.8)
    ]
)
```

### Using ControlNet

```python
from novita_client import Img2ImgV3ControlNetUnit

client = get_novita_sdk_client()
response = client.img2img_v3(
    input_image="https://example.com/image.jpg",
    model_name="dreamshaper_8_93211.safetensors",
    prompt="transform this image",
    controlnet_units=[
        Img2ImgV3ControlNetUnit(
            image_base64="https://example.com/control.jpg",
            model_name="control_v11f1p_sd15_depth",
            strength=1.0
        )
    ]
)
```

### Image-to-Video

```python
client = get_novita_sdk_client()
response = client.img2video(
    image="https://example.com/image.jpg",
    model_name="svd_xt",
    steps=25
)
```

## API Reference

### Functions

#### `fetch_models_from_novita()`
Fetches LLM models from Novita's OpenAI-compatible API.

**Returns**: `list[dict[str, Any]]` - List of normalized model definitions

#### `get_novita_sdk_client()`
Creates and returns a Novita SDK client instance for image/video generation.

**Returns**: `NovitaClient` instance or `None` if SDK unavailable

**Raises**: `ValueError` if `NOVITA_API_KEY` not configured

#### `fetch_image_models_from_novita_sdk()`
Fetches image generation models using the official Novita SDK.

**Returns**: `list` of image models or `None`

#### `generate_image_with_novita_sdk(prompt, model_name, **kwargs)`
Generates an image using the Novita SDK.

**Parameters**:
- `prompt` (str): Text description of the image
- `model_name` (str): Name of the model to use
- `**kwargs`: Additional parameters (width, height, steps, etc.)

**Returns**: Image generation response with encoded images

## Error Handling

The integration includes graceful degradation:

1. **SDK Not Installed**: Functions will log warnings and return `None` for image functions
2. **API Key Missing**: Falls back to static LLM model catalog
3. **API Errors**: Logs error and returns cached/fallback data

## Testing

Run the integration test:

```bash
python3 /tmp/test_novita_integration.py
```

## Model Catalog

### Default LLM Models

When the API is unavailable, these models are used as fallback:

| Model ID | Name | Provider | Context Length |
|----------|------|----------|----------------|
| `qwen3-235b-thinking` | Qwen3 235B Thinking | Alibaba | 32,768 |
| `qwen3-max` | Qwen3 Max | Alibaba | 32,768 |
| `deepseek-v3` | DeepSeek V3 | DeepSeek | 64,000 |
| `llama-3.3-70b` | Llama 3.3 70B | Meta | 8,192 |
| `mistral-nemo` | Mistral Nemo | Mistral | 8,192 |

## Resources

- [Novita AI Documentation](https://novita.ai/docs)
- [Novita Python SDK GitHub](https://github.com/novitalabs/python-sdk)
- [Novita API Reference](https://docs.novita.ai/)
- [OpenAI-Compatible API](https://api.novita.ai/v3/openai)

## Migration Notes

### From Previous Implementation

The previous implementation used only the OpenAI SDK for LLM models. The new implementation:

1. ✅ **Maintains** OpenAI SDK for LLM models (no breaking changes)
2. ✅ **Adds** native SDK support for image/video generation
3. ✅ **Provides** both APIs through a unified interface
4. ✅ **Includes** proper error handling and fallbacks

### Benefits

- **Better Image Generation**: Direct SDK access to all Novita image features
- **Type Safety**: Pydantic models from SDK for request/response validation
- **Feature Parity**: Access to LoRAs, ControlNet, embeddings, etc.
- **Official Support**: Using the officially maintained SDK

## Examples

See the [examples directory](../../examples/) for complete working examples:

- `novita_llm_chat.py` - LLM chat completion example
- `novita_image_generation.py` - Image generation example
- `novita_advanced_image.py` - Advanced image generation with LoRAs and ControlNet

## Troubleshooting

### SDK Not Available

**Issue**: `NOVITA_SDK_AVAILABLE` is `False`

**Solution**: Install the SDK:
```bash
pip install novita-client
```

### API Key Error

**Issue**: `ValueError: NOVITA_API_KEY not configured`

**Solution**: Set the environment variable:
```bash
export NOVITA_API_KEY=your_key_here
```

### Import Error

**Issue**: `ImportError: No module named 'novita_client'`

**Solution**: Install dependencies:
```bash
pip install -r requirements.txt
```

## Support

For issues with the Novita SDK integration:
1. Check the [Novita Discord](https://discord.com/invite/Mqx7nWYzDF)
2. Review [GitHub Issues](https://github.com/novitalabs/python-sdk/issues)
3. Consult the [official documentation](https://novita.ai/docs)

---

**Last Updated**: 2025-11-27
**SDK Version**: novita-client >= 0.5.0
