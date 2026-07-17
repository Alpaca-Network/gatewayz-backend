"""Provider client sub-package.

Contains all AI provider client modules (OpenRouter, Anthropic, OpenAI, etc.)
and the shared anthropic_transformer utility.

Re-exports public API from the legacy providers.py module so that
``from src.services.providers import …`` keeps working now that this
directory package shadows the flat file.
"""

import importlib as _importlib
import sys as _sys

# The flat providers.py file sits next to this package directory.
# Python's package resolution shadows it, so we load it explicitly.
_spec = _importlib.util.spec_from_file_location(
    "src.services._providers_legacy",
    __file__.replace("__init__.py", "").rstrip("/") + ".py",
)
_mod = _importlib.util.module_from_spec(_spec)
_sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

# Re-export every public symbol
from src.services._providers_legacy import (  # noqa: E402, F401
    enhance_providers_with_logos_and_sites,
    fetch_models_from_cerebras,
    fetch_models_from_novita,
    fetch_models_from_xai,
    fetch_providers_from_openrouter,
    get_cached_providers,
    get_provider_info,
    get_provider_logo_from_services,
)
