"""
ComfyUI Workflow Templates

Pre-defined workflow templates for common image and video generation tasks.
Each workflow is a ComfyUI API format JSON that can be customized with parameters.
"""

from src.models.comfyui_models import ComfyUIWorkflowTemplate, WorkflowType


# =============================================================================
# TEXT TO IMAGE WORKFLOWS
# =============================================================================

SDXL_TEXT_TO_IMAGE_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "cfg": 7,
            "denoise": 1,
            "latent_image": ["5", 0],
            "model": ["4", 0],
            "negative": ["7", 0],
            "positive": ["6", 0],
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "seed": 42,
            "steps": 20
        }
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "sd_xl_base_1.0.safetensors"
        }
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "batch_size": 1,
            "height": 1024,
            "width": 1024
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["4", 1],
            "text": "a beautiful landscape"
        }
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["4", 1],
            "text": "bad quality, blurry"
        }
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["3", 0],
            "vae": ["4", 2]
        }
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "ComfyUI",
            "images": ["8", 0]
        }
    }
}


SD15_TEXT_TO_IMAGE_WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "cfg": 7.5,
            "denoise": 1,
            "latent_image": ["5", 0],
            "model": ["4", 0],
            "negative": ["7", 0],
            "positive": ["6", 0],
            "sampler_name": "euler",
            "scheduler": "normal",
            "seed": 42,
            "steps": 25
        }
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "v1-5-pruned-emaonly.safetensors"
        }
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "batch_size": 1,
            "height": 512,
            "width": 512
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["4", 1],
            "text": "a beautiful landscape"
        }
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["4", 1],
            "text": "bad quality, blurry, ugly"
        }
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["3", 0],
            "vae": ["4", 2]
        }
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "ComfyUI",
            "images": ["8", 0]
        }
    }
}


# =============================================================================
# IMAGE TO IMAGE WORKFLOWS
# =============================================================================

SDXL_IMAGE_TO_IMAGE_WORKFLOW = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "input.png"
        }
    },
    "2": {
        "class_type": "VAEEncode",
        "inputs": {
            "pixels": ["1", 0],
            "vae": ["4", 2]
        }
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "cfg": 7,
            "denoise": 0.75,
            "latent_image": ["2", 0],
            "model": ["4", 0],
            "negative": ["7", 0],
            "positive": ["6", 0],
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "seed": 42,
            "steps": 20
        }
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "sd_xl_base_1.0.safetensors"
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["4", 1],
            "text": "enhanced version"
        }
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["4", 1],
            "text": "bad quality, blurry"
        }
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["3", 0],
            "vae": ["4", 2]
        }
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "ComfyUI_img2img",
            "images": ["8", 0]
        }
    }
}


# =============================================================================
# UPSCALE WORKFLOWS
# =============================================================================

UPSCALE_WORKFLOW = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "input.png"
        }
    },
    "2": {
        "class_type": "UpscaleModelLoader",
        "inputs": {
            "model_name": "RealESRGAN_x4plus.pth"
        }
    },
    "3": {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {
            "image": ["1", 0],
            "upscale_model": ["2", 0]
        }
    },
    "4": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "ComfyUI_upscaled",
            "images": ["3", 0]
        }
    }
}


# =============================================================================
# VIDEO WORKFLOWS (AnimateDiff)
# =============================================================================

ANIMATEDIFF_TEXT_TO_VIDEO_WORKFLOW = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "v1-5-pruned-emaonly.safetensors"
        }
    },
    "2": {
        "class_type": "ADE_AnimateDiffLoaderWithContext",
        "inputs": {
            "model": ["1", 0],
            "context_options": ["3", 0],
            "motion_lora": None,
            "motion_model_settings": None,
            "motion_scale": 1,
            "apply_v2_models_properly": False,
            "model_name": "mm_sd_v15_v2.ckpt"
        }
    },
    "3": {
        "class_type": "ADE_StandardUniformContextOptions",
        "inputs": {
            "context_length": 16,
            "context_stride": 1,
            "context_overlap": 4,
            "closed_loop": False,
            "fuse_method": "flat",
            "use_on_equal_length": False
        }
    },
    "4": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["1", 1],
            "text": "a cat walking"
        }
    },
    "5": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["1", 1],
            "text": "bad quality, blurry, static"
        }
    },
    "6": {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "batch_size": 16,
            "height": 512,
            "width": 512
        }
    },
    "7": {
        "class_type": "KSampler",
        "inputs": {
            "cfg": 7.5,
            "denoise": 1,
            "latent_image": ["6", 0],
            "model": ["2", 0],
            "negative": ["5", 0],
            "positive": ["4", 0],
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "seed": 42,
            "steps": 20
        }
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["7", 0],
            "vae": ["1", 2]
        }
    },
    "9": {
        "class_type": "ADE_AnimateDiffCombine",
        "inputs": {
            "images": ["8", 0],
            "frame_rate": 8,
            "loop_count": 0,
            "format": "video/h264-mp4",
            "pingpong": False,
            "save_output": True
        }
    }
}


# =============================================================================
# WORKFLOW TEMPLATES REGISTRY
# =============================================================================

WORKFLOW_TEMPLATES: list[ComfyUIWorkflowTemplate] = [
    # Text to Image
    ComfyUIWorkflowTemplate(
        id="sdxl-txt2img",
        name="SDXL Text to Image",
        description="Generate high-quality 1024x1024 images using Stable Diffusion XL",
        type=WorkflowType.TEXT_TO_IMAGE,
        workflow_json=SDXL_TEXT_TO_IMAGE_WORKFLOW,
        thumbnail_url=None,
        default_params={
            "width": 1024,
            "height": 1024,
            "steps": 20,
            "cfg_scale": 7,
            "sampler": "euler_ancestral"
        },
        param_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text description of the image to generate"},
                "negative_prompt": {"type": "string", "description": "Things to avoid in the image"},
                "width": {"type": "integer", "minimum": 512, "maximum": 2048, "default": 1024},
                "height": {"type": "integer", "minimum": 512, "maximum": 2048, "default": 1024},
                "steps": {"type": "integer", "minimum": 1, "maximum": 150, "default": 20},
                "cfg_scale": {"type": "number", "minimum": 1, "maximum": 30, "default": 7},
                "seed": {"type": "integer", "description": "Random seed for reproducibility"}
            },
            "required": ["prompt"]
        },
        credits_per_run=100,
        estimated_time_seconds=30
    ),

    ComfyUIWorkflowTemplate(
        id="sd15-txt2img",
        name="SD 1.5 Text to Image",
        description="Classic Stable Diffusion 1.5 for fast 512x512 image generation",
        type=WorkflowType.TEXT_TO_IMAGE,
        workflow_json=SD15_TEXT_TO_IMAGE_WORKFLOW,
        thumbnail_url=None,
        default_params={
            "width": 512,
            "height": 512,
            "steps": 25,
            "cfg_scale": 7.5,
            "sampler": "euler"
        },
        param_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text description of the image to generate"},
                "negative_prompt": {"type": "string", "description": "Things to avoid in the image"},
                "width": {"type": "integer", "minimum": 256, "maximum": 1024, "default": 512},
                "height": {"type": "integer", "minimum": 256, "maximum": 1024, "default": 512},
                "steps": {"type": "integer", "minimum": 1, "maximum": 150, "default": 25},
                "cfg_scale": {"type": "number", "minimum": 1, "maximum": 30, "default": 7.5},
                "seed": {"type": "integer", "description": "Random seed for reproducibility"}
            },
            "required": ["prompt"]
        },
        credits_per_run=50,
        estimated_time_seconds=15
    ),

    # Image to Image
    ComfyUIWorkflowTemplate(
        id="sdxl-img2img",
        name="SDXL Image to Image",
        description="Transform existing images with SDXL using text guidance",
        type=WorkflowType.IMAGE_TO_IMAGE,
        workflow_json=SDXL_IMAGE_TO_IMAGE_WORKFLOW,
        thumbnail_url=None,
        default_params={
            "denoise_strength": 0.75,
            "steps": 20,
            "cfg_scale": 7
        },
        param_schema={
            "type": "object",
            "properties": {
                "input_image": {"type": "string", "format": "base64", "description": "Input image (base64)"},
                "prompt": {"type": "string", "description": "Text guidance for transformation"},
                "negative_prompt": {"type": "string", "description": "Things to avoid"},
                "denoise_strength": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.75},
                "steps": {"type": "integer", "minimum": 1, "maximum": 150, "default": 20},
                "cfg_scale": {"type": "number", "minimum": 1, "maximum": 30, "default": 7},
                "seed": {"type": "integer", "description": "Random seed for reproducibility"}
            },
            "required": ["input_image", "prompt"]
        },
        credits_per_run=100,
        estimated_time_seconds=30
    ),

    # Upscale
    ComfyUIWorkflowTemplate(
        id="upscale-4x",
        name="4x Image Upscale",
        description="Upscale images 4x using Real-ESRGAN",
        type=WorkflowType.UPSCALE,
        workflow_json=UPSCALE_WORKFLOW,
        thumbnail_url=None,
        default_params={},
        param_schema={
            "type": "object",
            "properties": {
                "input_image": {"type": "string", "format": "base64", "description": "Input image (base64)"}
            },
            "required": ["input_image"]
        },
        credits_per_run=50,
        estimated_time_seconds=20
    ),

    # Text to Video (AnimateDiff)
    ComfyUIWorkflowTemplate(
        id="animatediff-txt2vid",
        name="AnimateDiff Text to Video",
        description="Generate short animated videos from text prompts",
        type=WorkflowType.TEXT_TO_VIDEO,
        workflow_json=ANIMATEDIFF_TEXT_TO_VIDEO_WORKFLOW,
        thumbnail_url=None,
        default_params={
            "frames": 16,
            "fps": 8,
            "width": 512,
            "height": 512,
            "steps": 20
        },
        param_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text description of the video to generate"},
                "negative_prompt": {"type": "string", "description": "Things to avoid"},
                "frames": {"type": "integer", "minimum": 8, "maximum": 32, "default": 16},
                "fps": {"type": "integer", "minimum": 4, "maximum": 30, "default": 8},
                "width": {"type": "integer", "minimum": 256, "maximum": 768, "default": 512},
                "height": {"type": "integer", "minimum": 256, "maximum": 768, "default": 512},
                "steps": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "cfg_scale": {"type": "number", "minimum": 1, "maximum": 30, "default": 7.5},
                "seed": {"type": "integer", "description": "Random seed for reproducibility"}
            },
            "required": ["prompt"]
        },
        credits_per_run=200,
        estimated_time_seconds=120
    ),
]


def get_workflow_template(workflow_id: str) -> ComfyUIWorkflowTemplate | None:
    """Get a workflow template by ID"""
    for template in WORKFLOW_TEMPLATES:
        if template.id == workflow_id:
            return template
    return None


def get_workflow_templates_by_type(workflow_type: WorkflowType) -> list[ComfyUIWorkflowTemplate]:
    """Get all workflow templates of a specific type"""
    return [t for t in WORKFLOW_TEMPLATES if t.type == workflow_type]


def list_workflow_templates() -> list[ComfyUIWorkflowTemplate]:
    """Get all available workflow templates"""
    return WORKFLOW_TEMPLATES
