"""
Application Constants

This module contains constant values used throughout the application,
helping to reduce code duplication and improve maintainability.
"""

import os

# Frontend URLs
# These can be overridden by environment variables for different environments
FRONTEND_BASE_URL = os.environ.get("FRONTEND_URL", "https://beta.gatewayz.ai")
FRONTEND_BETA_URL = "https://beta.gatewayz.ai"
FRONTEND_STAGING_URL = "https://staging.gatewayz.ai"

# Tauri Desktop App URLs (for CORS)
# Tauri 2.x uses both tauri:// and http://tauri.localhost as origins for desktop apps
TAURI_DESKTOP_URL = "http://tauri.localhost"
TAURI_DESKTOP_PROTOCOL_URL = "tauri://localhost"

# Common frontend paths
SETTINGS_CREDITS_PATH = "/settings/credits"

# Full URLs combining base and paths
SETTINGS_CREDITS_URL = f"{FRONTEND_BASE_URL}{SETTINGS_CREDITS_PATH}"

# Application metadata
APP_NAME = os.environ.get("APP_NAME", "Gatewayz")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@gatewayz.ai")
