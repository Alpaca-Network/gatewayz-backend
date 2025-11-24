"""
Utility helpers for resolving project-relative filesystem paths.

These helpers ensure we never rely on hard-coded absolute paths that only
exist in specific environments (e.g., /root/repo). Instead, paths are computed
relative to the installed package location with optional environment variable
overrides for deployment-specific customization.
"""

from __future__ import annotations

import os
from pathlib import Path

# Resolve important reference directories once.
_SRC_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _SRC_DIR / "data"


def get_project_root() -> Path:
    """Return the project root directory (parent of src)."""
    return _SRC_DIR.parent


def get_src_dir() -> Path:
    """Return the absolute path to the src directory."""
    return _SRC_DIR


def get_data_dir(*subpaths: str) -> Path:
    """
    Return the path to the shared data directory (or a subpath inside it).

    Args:
        *subpaths: Optional subdirectories/files inside the data directory.

    Environment Overrides:
        GATEWAYZ_DATA_DIR: Override the base data directory.
    """
    base_dir = Path(os.getenv("GATEWAYZ_DATA_DIR", _DEFAULT_DATA_DIR))
    return base_dir.joinpath(*subpaths) if subpaths else base_dir


def resolve_path(env_var: str, default: Path) -> Path:
    """
    Resolve a path using an environment variable override if present.

    Args:
        env_var: Name of the environment variable to check.
        default: Default path to use if the env var is unset.

    Returns:
        Path: Either the override value or the default path.
    """
    override = os.getenv(env_var)
    if override:
        return Path(override).expanduser()
    return default
