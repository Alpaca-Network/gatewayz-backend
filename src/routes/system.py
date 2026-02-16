"""
System endpoints for cache management and gateway health monitoring
Phase 2 implementation
"""

import asyncio
import inspect
import io
import json
import logging
import os
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from html import escape
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.security.deps import require_admin

from src.services.model_catalog_cache import (
    clear_models_cache,
    clear_providers_cache,
    get_gateway_cache_metadata as get_models_cache,
    get_provider_cache_metadata as get_providers_cache,
)
from src.services.modelz_client import clear_modelz_cache
from src.config import Config
from src.services.huggingface_models import fetch_models_from_hug

# Import fetch_models functions from their respective client files
from src.services.aihubmix_client import fetch_models_from_aihubmix
from src.services.aimo_client import fetch_models_from_aimo
from src.services.anannas_client import fetch_models_from_anannas
from src.services.chutes_client import fetch_models_from_chutes
from src.services.fal_image_client import fetch_models_from_fal
from src.services.featherless_client import fetch_models_from_featherless
from src.services.fireworks_client import fetch_models_from_fireworks
from src.services.groq_client import fetch_models_from_groq
from src.services.near_client import fetch_models_from_near
from src.services.openrouter_client import fetch_models_from_openrouter
from src.services.together_client import fetch_models_from_together
from src.services.modelz_client import get_modelz_cache_status as get_modelz_cache_status_func
from src.services.modelz_client import refresh_modelz_cache
from src.services.onerouter_client import fetch_models_from_onerouter
from src.services.pricing_lookup import get_model_pricing, refresh_pricing_cache
from src.services.providers import (
    fetch_models_from_cerebras,
    fetch_models_from_nebius,
    fetch_models_from_novita,
    fetch_models_from_xai,
)

# Initialize logging
logger = logging.getLogger(__name__)

try:
    from src.services.gateway_health_service import (  # type: ignore
        GATEWAY_CONFIG,
        run_comprehensive_check,
    )
except Exception as e:  # pragma: no cover - optional dependency for dashboard
    logger.warning(f"Failed to import gateway_health_service: {e}")
    run_comprehensive_check = None  # type: ignore
    GATEWAY_CONFIG = {}  # type: ignore

router = APIRouter()


def get_all_gateway_names() -> list[str]:
    """
    Get all gateway names from GATEWAY_CONFIG.

    This ensures all gateways are automatically included without manual maintenance.
    New gateways added to GATEWAY_CONFIG will be automatically supported.
    """
    if GATEWAY_CONFIG:
        return sorted(GATEWAY_CONFIG.keys())

    # Fallback list if GATEWAY_CONFIG is not available
    return [
        "aihubmix",
        "aimo",
        "anannas",
        "cerebras",
        "chutes",
        "deepinfra",
        "fal",
        "featherless",
        "fireworks",
        "groq",
        "huggingface",
        "near",
        "nebius",
        "novita",
        "onerouter",
        "openrouter",
        "together",
        "xai",
    ]


def get_cacheable_gateways() -> list[str]:
    """
    Get list of gateways that support cache refresh.

    Returns all gateways from GATEWAY_CONFIG that have cache support,
    automatically including new gateways as they're added.

    Note: deepinfra is excluded as it only supports on-demand fetching.
    """
    # Map of gateway names to their fetch functions
    # Only include gateways that have fetch functions implemented
    fetch_function_map = {
        "aihubmix": fetch_models_from_aihubmix,
        "aimo": fetch_models_from_aimo,
        "anannas": fetch_models_from_anannas,
        "cerebras": fetch_models_from_cerebras,
        "chutes": fetch_models_from_chutes,
        # "deepinfra": excluded - only supports on-demand fetching, not bulk refresh
        "fal": fetch_models_from_fal,
        "featherless": fetch_models_from_featherless,
        "fireworks": fetch_models_from_fireworks,
        "groq": fetch_models_from_groq,
        "huggingface": fetch_models_from_hug,
        "near": fetch_models_from_near,
        "nebius": fetch_models_from_nebius,
        "novita": fetch_models_from_novita,
        "onerouter": fetch_models_from_onerouter,
        "openrouter": fetch_models_from_openrouter,
        "together": fetch_models_from_together,
        "xai": fetch_models_from_xai,
    }

    return sorted(fetch_function_map.keys())


def get_fetch_function(gateway: str):
    """
    Get the fetch function for a specific gateway.

    Returns the appropriate fetch function or None if not available.
    Note: deepinfra is excluded as it only supports on-demand fetching.
    """
    fetch_functions = {
        "aihubmix": fetch_models_from_aihubmix,
        "aimo": fetch_models_from_aimo,
        "anannas": fetch_models_from_anannas,
        "cerebras": fetch_models_from_cerebras,
        "chutes": fetch_models_from_chutes,
        # "deepinfra": excluded - only supports on-demand fetching, not bulk refresh
        "fal": fetch_models_from_fal,
        "featherless": fetch_models_from_featherless,
        "fireworks": fetch_models_from_fireworks,
        "groq": fetch_models_from_groq,
        "huggingface": fetch_models_from_hug,
        "near": fetch_models_from_near,
        "nebius": fetch_models_from_nebius,
        "novita": fetch_models_from_novita,
        "onerouter": fetch_models_from_onerouter,
        "openrouter": fetch_models_from_openrouter,
        "together": fetch_models_from_together,
        "xai": fetch_models_from_xai,
    }

    return fetch_functions.get(gateway)


def _normalize_timestamp(value: Any) -> datetime | None:
    """Convert a cached timestamp into an aware ``datetime`` in timezone.utc."""

    if not value:
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)

    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    if isinstance(value, str):
        try:
            cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
            parsed = datetime.fromisoformat(cleaned)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def _render_gateway_dashboard(results: dict[str, Any], log_output: str, auto_fix: bool) -> str:
    """Generate a minimal HTML dashboard for gateway health results."""

    timestamp = escape(results.get("timestamp", ""))
    summary = {
        "total": results.get("total_gateways", 0),
        "healthy": results.get("healthy", 0),
        "unhealthy": results.get("unhealthy", 0),
        "unconfigured": results.get("unconfigured", 0),
        "fixed": results.get("fixed", 0),
    }

    def format_price_value(value: Any) -> str | None:
        if value is None:
            return None
        value_str = str(value).strip()
        if not value_str:
            return None
        # Remove leading currency symbol for numeric parsing
        cleaned = value_str[1:] if value_str.startswith("$") else value_str
        try:
            numeric = float(cleaned)
            if numeric == 0:
                formatted = "0"
            elif numeric < 0.01:
                formatted = f"{numeric:.6f}".rstrip("0").rstrip(".")
            elif numeric < 1:
                formatted = f"{numeric:.4f}".rstrip("0").rstrip(".")
            else:
                formatted = f"{numeric:.2f}".rstrip("0").rstrip(".")
            return f"${formatted}"
        except ValueError:
            return value_str

    def format_pricing_display(pricing: dict[str, Any] | None) -> str:
        if not isinstance(pricing, dict):
            return ""
        label_map = {
            "prompt": "Prompt",
            "completion": "Completion",
            "input": "Input",
            "output": "Output",
            "cached_prompt": "Cached Prompt",
            "cached_completion": "Cached Completion",
            "request": "Request",
            "image": "Image",
            "audio": "Audio",
            "video": "Video",
            "training": "Training",
            "fine_tune": "Fine-tune",
        }
        unit_map = {
            "prompt": "/1M tokens",
            "completion": "/1M tokens",
            "input": "/1M tokens",
            "output": "/1M tokens",
            "cached_prompt": "/1M tokens",
            "cached_completion": "/1M tokens",
            "request": " each",
            "image": " each",
            "audio": " /min",
            "video": " /min",
            "training": " /hr",
            "fine_tune": " /hr",
        }
        parts: list[str] = []
        for key, raw_value in pricing.items():
            normalized = format_price_value(raw_value)
            if not normalized:
                continue
            label = label_map.get(key, key.replace("_", " ").title())
            unit = unit_map.get(key, "")
            parts.append(f"{label} {normalized}{unit}")
        return " | ".join(parts)

    def status_badge(status: str) -> str:
        status_lower = (status or "unknown").lower()
        if status_lower in {"healthy", "pass", "configured"}:
            cls = "badge badge-healthy"
        elif status_lower in {"unconfigured", "skipped"}:
            cls = "badge badge-unconfigured"
        elif status_lower in {"unhealthy", "fail", "error"}:
            cls = "badge badge-unhealthy"
        else:
            cls = "badge badge-unknown"
        return f'<span class="{cls}">{escape(status.title())}</span>'

    rows = []
    gateways: dict[str, Any] = results.get("gateways", {}) or {}
    for gateway_id in sorted(gateways.keys()):
        data = gateways[gateway_id] or {}
        name = data.get("name") or gateway_id.title()
        configured = "Yes" if data.get("configured") else "No"

        endpoint_test = data.get("endpoint_test") or {}
        endpoint_status = "Pass" if endpoint_test.get("success") else "Fail"
        endpoint_msg = endpoint_test.get("message") or "Not run"
        endpoint_count = endpoint_test.get("model_count")
        endpoint_details = endpoint_msg
        if endpoint_count is not None:
            endpoint_details += f" (models: {endpoint_count})"

        cache_test = data.get("cache_test") or {}
        cache_status = "Pass" if cache_test.get("success") else "Fail"
        cache_msg = cache_test.get("message") or "Not run"
        cache_count = cache_test.get("model_count")
        cache_details = cache_msg
        if cache_count is not None:
            cache_details += f" (models: {cache_count})"

        # Recalculate final_status based on endpoint and cache test results
        # Final status is "healthy" if EITHER endpoint OR cache test passes
        # This allows cache-only gateways (like Fal.ai) and gateways with empty caches but working endpoints
        if not data.get("configured"):
            final_status = "unconfigured"
        elif endpoint_test.get("success") or cache_test.get("success"):
            final_status = "healthy"
        else:
            final_status = "unhealthy"

        # Make badges clickable for refresh actions
        endpoint_badge_html = f"""
        <div class="clickable-badge" onclick="event.stopPropagation(); refreshEndpoint('{escape(gateway_id)}', this)">
            {status_badge(endpoint_status)}
            <div class="details">{escape(endpoint_details)}</div>
        </div>
        """

        cache_badge_html = f"""
        <div class="clickable-badge" onclick="event.stopPropagation(); refreshCache('{escape(gateway_id)}', this)">
            {status_badge(cache_status)}
            <div class="details">{escape(cache_details)}</div>
        </div>
        """

        auto_fix_attempted = data.get("auto_fix_attempted")
        auto_fix_successful = data.get("auto_fix_successful")
        auto_fix_text = "Not attempted"
        if auto_fix_attempted:
            auto_fix_text = "Succeeded" if auto_fix_successful else "Failed"

        final_status_lower = (final_status or "unknown").lower()
        toggle_disabled = (not data.get("configured")) or final_status_lower == "healthy"
        toggle_hint = "Toggle to run fix"
        if not data.get("configured"):
            toggle_hint = "Configure API key to enable fixes"
        elif final_status_lower == "healthy":
            toggle_hint = "Gateway healthy"

        toggle_attributes = 'disabled="disabled"' if toggle_disabled else ""
        auto_fix_cell = f"""
        <div class="fix-toggle" onclick="event.stopPropagation()">
            <div class="status-text">{escape(auto_fix_text)}</div>
            <label class="switch" onclick="event.stopPropagation()">
                <input type="checkbox" onchange="handleFixToggle(event, '{escape(gateway_id)}', this)" {toggle_attributes}>
                <span class="slider"></span>
            </label>
            <span class="toggle-hint">{escape(toggle_hint)}</span>
        </div>
        """

        # Get models from cache test
        models = cache_test.get("models", [])
        has_models = models and len(models) > 0
        models_html = ""
        if has_models:
            model_items = []
            for model in models:
                pricing_info: dict[str, Any] | None = None
                pricing_source: str | None = None

                if isinstance(model, dict):
                    model_id = model.get("id") or model.get("model") or str(model)
                    candidate_pricing = model.get("pricing")
                    if isinstance(candidate_pricing, dict) and any(
                        str(v).strip() for v in candidate_pricing.values() if v is not None
                    ):
                        pricing_info = candidate_pricing
                        pricing_source = model.get("pricing_source")
                else:
                    model_id = str(model)

                if pricing_info is None:
                    manual_pricing = get_model_pricing(gateway_id, model_id)
                    if manual_pricing:
                        pricing_info = manual_pricing
                        pricing_source = "manual"

                pricing_display = format_pricing_display(pricing_info)
                if pricing_display:
                    pricing_html = """
                        <div class="pricing">
                            <span class="pricing-label">Pricing:</span>
                            <span class="pricing-value">{pricing_details}</span>
                            {source}
                        </div>
                    """.format(
                        pricing_details=escape(pricing_display),
                        source=(
                            f'<span class="pricing-source">{escape(pricing_source)}</span>'
                            if pricing_source
                            else ""
                        ),
                    )
                else:
                    pricing_html = """
                        <div class="pricing pricing-missing">
                            <span class="pricing-label">Pricing:</span>
                            <span class="pricing-value">Unavailable</span>
                        </div>
                    """

                model_items.append(
                    f"""
                    <li>
                        <span class="model-id">{escape(model_id)}</span>
                        {pricing_html}
                    </li>
                    """
                )
            models_html = f"""
            <tr class="model-row" id="models-{escape(gateway_id)}" style="display: none;">
                <td colspan="6" class="models-cell">
                    <div class="models-container">
                        <strong>Successfully loaded models ({len(models)}):</strong>
                        <ul class="models-list">
                            {''.join(model_items)}
                        </ul>
                    </div>
                </td>
            </tr>
            """

        rows.append(
            """
            <tr class="gateway-row {clickable_class}" data-gateway="{gateway_attr}" {onclick}>
                <td>{name} {expand_icon}</td>
                <td>{configured}</td>
                <td>{endpoint_badge_cell}</td>
                <td>{cache_badge_cell}</td>
                <td>{final_badge}</td>
                <td>{auto_fix}</td>
            </tr>
            {models_row}
            """.format(
                clickable_class="clickable" if has_models else "",
                onclick=f"onclick=\"toggleModels('{escape(gateway_id)}')\"" if has_models else "",
                gateway_attr=escape(gateway_id),
                name=escape(name),
                expand_icon='<span class="expand-icon">▶</span>' if has_models else "",
                configured=escape(configured),
                endpoint_badge_cell=endpoint_badge_html,
                cache_badge_cell=cache_badge_html,
                final_badge=status_badge(final_status),
                auto_fix=auto_fix_cell,
                models_row=models_html,
            )
        )

    rows_html = "\n".join(rows) or "<tr><td colspan=6>No gateways inspected.</td></tr>"

    summary_cards = """
        <div class="card">
            <div class="metric">{total}</div>
            <div class="label">Total Gateways</div>
        </div>
        <div class="card">
            <div class="metric success">{healthy}</div>
            <div class="label">Healthy</div>
        </div>
        <div class="card">
            <div class="metric warning">{unconfigured}</div>
            <div class="label">Unconfigured</div>
        </div>
        <div class="card">
            <div class="metric danger">{unhealthy}</div>
            <div class="label">Unhealthy</div>
        </div>
    """.format(
        total=summary["total"],
        healthy=summary["healthy"],
        unconfigured=summary["unconfigured"],
        unhealthy=summary["unhealthy"],
    )

    if auto_fix:
        summary_cards += """
            <div class=\"card\">
                <div class=\"metric\">{fixed}</div>
                <div class=\"label\">Auto-fixed</div>
            </div>
        """.format(
            fixed=summary["fixed"]
        )

    raw_json = escape(json.dumps(results, indent=2))
    log_block = escape(log_output.strip()) if log_output else "No log output captured."

    return f"""
    <!DOCTYPE html>
    <html lang=\"en\">
    <head>
        <meta charset=\"utf-8\" />
        <title>Gateway Health Dashboard</title>
        <style>
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: #0f172a;
                color: #e2e8f0;
                margin: 0;
                padding: 32px;
            }}
            h1 {{
                margin-top: 0;
                font-size: 2rem;
            }}
            .summary {{
                display: flex;
                flex-wrap: wrap;
                gap: 16px;
                margin: 24px 0;
            }}
            .card {{
                background: rgba(148, 163, 184, 0.1);
                border-radius: 12px;
                padding: 16px 20px;
                min-width: 160px;
                box-shadow: 0 12px 24px rgba(15, 23, 42, 0.45);
            }}
            .metric {{
                font-size: 1.75rem;
                font-weight: 700;
            }}
            .metric.success {{ color: #4ade80; }}
            .metric.danger {{ color: #f87171; }}
            .metric.warning {{ color: #facc15; }}
            .label {{
                margin-top: 4px;
                font-size: 0.9rem;
                color: #cbd5f5;
                opacity: 0.85;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: rgba(15, 23, 42, 0.8);
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 16px 32px rgba(15, 23, 42, 0.65);
            }}
            thead {{
                background: rgba(30, 41, 59, 0.9);
            }}
            th, td {{
                padding: 14px 16px;
                text-align: left;
                border-bottom: 1px solid rgba(148, 163, 184, 0.15);
                vertical-align: top;
            }}
            tr:last-child td {{
                border-bottom: none;
            }}
            .badge {{
                display: inline-block;
                padding: 4px 10px;
                border-radius: 999px;
                font-size: 0.75rem;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.04em;
            }}
            .badge-healthy {{
                background: rgba(74, 222, 128, 0.16);
                color: #4ade80;
                border: 1px solid rgba(74, 222, 128, 0.4);
            }}
            .badge-unhealthy {{
                background: rgba(248, 113, 113, 0.16);
                color: #f87171;
                border: 1px solid rgba(248, 113, 113, 0.4);
            }}
            .badge-unconfigured {{
                background: rgba(250, 204, 21, 0.16);
                color: #facc15;
                border: 1px solid rgba(250, 204, 21, 0.4);
            }}
            .badge-unknown {{
                background: rgba(148, 163, 184, 0.16);
                color: #e2e8f0;
                border: 1px solid rgba(148, 163, 184, 0.35);
            }}
            .details {{
                margin-top: 6px;
                font-size: 0.85rem;
                color: rgba(226, 232, 240, 0.8);
            }}
            details {{
                margin-top: 24px;
                background: rgba(30, 41, 59, 0.65);
                border-radius: 12px;
                padding: 16px 20px;
                box-shadow: 0 10px 22px rgba(15, 23, 42, 0.5);
            }}
            summary {{
                cursor: pointer;
                font-weight: 600;
            }}
            pre {{
                white-space: pre-wrap;
                word-break: break-word;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                background: rgba(15, 23, 42, 0.75);
                padding: 16px;
                border-radius: 8px;
                color: #cbd5f5;
                margin-top: 16px;
            }}
            .meta {{
                display: flex;
                gap: 12px;
                align-items: center;
                color: rgba(226, 232, 240, 0.75);
                font-size: 0.95rem;
            }}
            .meta strong {{
                color: #e2e8f0;
            }}
            .gateway-row.clickable {{
                cursor: pointer;
                transition: background-color 0.2s ease;
            }}
            .gateway-row.clickable:hover {{
                background: rgba(148, 163, 184, 0.08);
            }}
            .expand-icon {{
                display: inline-block;
                margin-left: 8px;
                transition: transform 0.2s ease;
                font-size: 0.8rem;
                color: #94a3b8;
            }}
            .gateway-row.expanded .expand-icon {{
                transform: rotate(90deg);
            }}
            .models-cell {{
                background: rgba(30, 41, 59, 0.5);
                padding: 20px !important;
            }}
            .models-container {{
                background: rgba(15, 23, 42, 0.6);
                border-radius: 8px;
                padding: 16px;
                border-left: 3px solid #4ade80;
            }}
            .models-container strong {{
                color: #4ade80;
                display: block;
                margin-bottom: 12px;
                font-size: 0.95rem;
            }}
            .models-list {{
                list-style: none;
                padding: 0;
                margin: 0;
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 8px;
                max-height: 400px;
                overflow-y: auto;
            }}
            .models-list li {{
                background: rgba(148, 163, 184, 0.08);
                padding: 8px 12px;
                border-radius: 6px;
                font-size: 0.85rem;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                color: #cbd5e1;
                border: 1px solid rgba(148, 163, 184, 0.15);
            }}
            .model-id {{
                display: block;
                font-weight: 600;
                color: #f8fafc;
                margin-bottom: 4px;
            }}
            .pricing {{
                font-size: 0.75rem;
                color: rgba(226, 232, 240, 0.75);
                display: flex;
                flex-wrap: wrap;
                gap: 4px;
                align-items: center;
            }}
            .pricing-label {{
                font-weight: 600;
                color: #94a3b8;
            }}
            .pricing-value {{
                color: #cbd5f5;
            }}
            .pricing-source {{
                background: rgba(59, 130, 246, 0.2);
                color: #93c5fd;
                border: 1px solid rgba(59, 130, 246, 0.35);
                border-radius: 999px;
                padding: 2px 8px;
                font-size: 0.7rem;
                text-transform: uppercase;
                letter-spacing: 0.04em;
            }}
            .pricing-missing .pricing-value {{
                color: rgba(226, 232, 240, 0.45);
                font-style: italic;
            }}
            .fix-toggle {{
                display: flex;
                flex-direction: column;
                gap: 8px;
                align-items: flex-start;
            }}
            .fix-toggle .status-text {{
                font-weight: 600;
                font-size: 0.85rem;
                color: #cbd5f5;
            }}
            .fix-toggle .toggle-hint {{
                font-size: 0.75rem;
                color: rgba(226, 232, 240, 0.6);
            }}
            .switch {{
                position: relative;
                display: inline-block;
                width: 48px;
                height: 24px;
                cursor: pointer;
            }}
            .switch input {{
                opacity: 0;
                width: 0;
                height: 0;
            }}
            .slider {{
                position: absolute;
                cursor: pointer;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background-color: rgba(248, 113, 113, 0.35);
                transition: 0.2s;
                border-radius: 24px;
                border: 1px solid rgba(248, 113, 113, 0.5);
            }}
            .slider:hover {{
                background-color: rgba(248, 113, 113, 0.45);
                border-color: rgba(248, 113, 113, 0.65);
            }}
            .slider:before {{
                position: absolute;
                content: "";
                height: 18px;
                width: 18px;
                left: 3px;
                bottom: 2px;
                background-color: #0f172a;
                transition: 0.2s;
                border-radius: 50%;
                box-shadow: 0 2px 4px rgba(15, 23, 42, 0.4);
            }}
            .switch input:checked + .slider {{
                background-color: rgba(74, 222, 128, 0.45);
                border-color: rgba(74, 222, 128, 0.7);
            }}
            .switch input:checked + .slider:hover {{
                background-color: rgba(74, 222, 128, 0.55);
                border-color: rgba(74, 222, 128, 0.85);
            }}
            .switch input:checked + .slider:before {{
                transform: translateX(22px);
            }}
            .switch input:disabled + .slider {{
                background-color: rgba(148, 163, 184, 0.2);
                border-color: rgba(148, 163, 184, 0.3);
                cursor: not-allowed;
            }}
            .switch:has(input:disabled) {{
                cursor: not-allowed;
            }}
            .models-list::-webkit-scrollbar {{
                width: 8px;
            }}
            .models-list::-webkit-scrollbar-track {{
                background: rgba(15, 23, 42, 0.4);
                border-radius: 4px;
            }}
            .models-list::-webkit-scrollbar-thumb {{
                background: rgba(148, 163, 184, 0.3);
                border-radius: 4px;
            }}
            .models-list::-webkit-scrollbar-thumb:hover {{
                background: rgba(148, 163, 184, 0.5);
            }}
            .clickable-badge {{
                cursor: pointer;
                transition: all 0.2s ease;
                padding: 4px;
                border-radius: 8px;
            }}
            .clickable-badge:hover {{
                background: rgba(148, 163, 184, 0.1);
                transform: translateY(-1px);
            }}
            .clickable-badge:active {{
                transform: translateY(0);
            }}
            .clickable-badge .details {{
                pointer-events: none;
            }}
            .clickable-badge .badge {{
                pointer-events: none;
            }}
            .refreshing {{
                opacity: 0.6;
                pointer-events: none;
            }}
            .refresh-spinner {{
                display: inline-block;
                width: 12px;
                height: 12px;
                border: 2px solid rgba(226, 232, 240, 0.3);
                border-top-color: #4ade80;
                border-radius: 50%;
                animation: spin 0.8s linear infinite;
                margin-left: 6px;
            }}
            @keyframes spin {{
                to {{ transform: rotate(360deg); }}
            }}
        </style>
    </head>
    <body>
        <h1>Gateway Health Dashboard</h1>
        <div class="meta">
            <div><strong>Run completed:</strong> {timestamp or 'unknown'}</div>
            <div><strong>Auto-fix:</strong> {'Enabled' if auto_fix else 'Disabled'}</div>
        </div>
        <div class="summary">
            {summary_cards}
        </div>
        <table>
            <thead>
                <tr>
                    <th>Gateway</th>
                    <th>Configured</th>
                    <th>Endpoint Check</th>
                    <th>Cache Check</th>
                    <th>Final Status</th>
                    <th>Fix Gateway</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        <details>
            <summary>View raw log output</summary>
            <pre>{log_block}</pre>
        </details>
        <details>
            <summary>View raw JSON payload</summary>
            <pre>{raw_json}</pre>
        </details>
        <script>
            function toggleModels(gatewayId) {{
                const modelsRow = document.getElementById('models-' + gatewayId);
                const gatewayRow = document.querySelector('[data-gateway="' + gatewayId + '"]');

                if (modelsRow) {{
                    if (modelsRow.style.display === 'none' || modelsRow.style.display === '') {{
                        modelsRow.style.display = 'table-row';
                        if (gatewayRow) gatewayRow.classList.add('expanded');
                    }} else {{
                        modelsRow.style.display = 'none';
                        if (gatewayRow) gatewayRow.classList.remove('expanded');
                    }}
                }}
            }}

            async function refreshEndpoint(gatewayId, element) {{
                console.log('Refreshing endpoint for', gatewayId);
                const container = element.closest('.clickable-badge');
                if (!container) return;

                container.classList.add('refreshing');
                const badge = container.querySelector('.badge');
                const details = container.querySelector('.details');
                const originalBadgeText = badge ? badge.textContent : '';
                const originalDetailsText = details ? details.textContent : '';

                if (badge) {{
                    badge.innerHTML = 'Checking<span class="refresh-spinner"></span>';
                }}
                if (details) {{
                    details.textContent = 'Running endpoint check...';
                }}

                try {{
                    const response = await fetch('/health/' + gatewayId);
                    if (!response.ok) {{
                        throw new Error('HTTP ' + response.status);
                    }}
                    const data = await response.json();

                    if (badge) {{
                        badge.textContent = data.data.available ? 'Pass' : 'Fail';
                        badge.className = data.data.available ? 'badge badge-healthy' : 'badge badge-unhealthy';
                    }}
                    if (details) {{
                        const latency = data.data.latency_ms ? ' (' + data.data.latency_ms + 'ms)' : '';
                        details.textContent = (data.data.error || 'Endpoint accessible') + latency;
                    }}
                }} catch (error) {{
                    console.error('Failed to refresh endpoint for', gatewayId, error);
                    if (badge) {{
                        badge.textContent = 'Error';
                        badge.className = 'badge badge-unhealthy';
                    }}
                    if (details) {{
                        details.textContent = 'Failed to check endpoint';
                    }}
                }} finally {{
                    container.classList.remove('refreshing');
                }}
            }}

            async function refreshCache(gatewayId, element) {{
                console.log('Refreshing cache for', gatewayId);
                const container = element.closest('.clickable-badge');
                if (!container) return;

                container.classList.add('refreshing');
                const badge = container.querySelector('.badge');
                const details = container.querySelector('.details');
                const originalBadgeText = badge ? badge.textContent : '';
                const originalDetailsText = details ? details.textContent : '';

                if (badge) {{
                    badge.innerHTML = 'Refreshing<span class="refresh-spinner"></span>';
                }}
                if (details) {{
                    details.textContent = 'Refreshing cache...';
                }}

                try {{
                    const response = await fetch('/cache/refresh/' + gatewayId + '?force=true', {{
                        method: 'POST'
                    }});
                    if (!response.ok) {{
                        throw new Error('HTTP ' + response.status);
                    }}
                    const data = await response.json();

                    if (badge) {{
                        badge.textContent = data.success ? 'Pass' : 'Fail';
                        badge.className = data.success ? 'badge badge-healthy' : 'badge badge-unhealthy';
                    }}
                    if (details) {{
                        const count = data.models_cached || 0;
                        details.textContent = data.message + ' (models: ' + count + ')';
                    }}

                    // Reload the page after a short delay to show updated models
                    setTimeout(() => window.location.reload(), 800);
                }} catch (error) {{
                    console.error('Failed to refresh cache for', gatewayId, error);
                    if (badge) {{
                        badge.textContent = 'Error';
                        badge.className = 'badge badge-unhealthy';
                    }}
                    if (details) {{
                        details.textContent = 'Failed to refresh cache';
                    }}
                }} finally {{
                    container.classList.remove('refreshing');
                }}
            }}

            async function handleFixToggle(event, gatewayId, checkbox) {{
                event.stopPropagation();
                if (!checkbox.checked) {{
                    return;
                }}

                const container = checkbox.closest('.fix-toggle');
                const statusText = container ? container.querySelector('.status-text') : null;
                const hintText = container ? container.querySelector('.toggle-hint') : null;
                const originalStatus = statusText ? statusText.textContent : '';
                const originalHint = hintText ? hintText.textContent : '';

                checkbox.disabled = true;
                if (statusText) {{
                    statusText.textContent = 'Running fix...';
                }}
                if (hintText) {{
                    hintText.textContent = 'Attempting auto-fix via API...';
                }}

                try {{
                    const response = await fetch('/health/gateways/' + gatewayId + '/fix?auto_fix=true', {{
                        method: 'POST'
                    }});

                    if (!response.ok) {{
                        throw new Error('HTTP ' + response.status);
                    }}

                    const payload = await response.json();

                    if (payload && payload.data) {{
                        const resultStatus = payload.data.auto_fix_successful ? 'Succeeded' : 'Failed';
                        if (statusText) {{
                            statusText.textContent = 'Auto-fix ' + resultStatus;
                        }}
                    }} else if (statusText) {{
                        statusText.textContent = 'Fix attempted';
                    }}

                    if (hintText) {{
                        hintText.textContent = 'Fix completed. Refreshing...';
                    }}

                    checkbox.checked = false;
                    setTimeout(() => window.location.reload(), 600);
                }} catch (error) {{
                    console.error('Failed to run fix for', gatewayId, error);
                    if (statusText) {{
                        statusText.textContent = originalStatus || 'Not attempted';
                    }}
                    if (hintText) {{
                        hintText.textContent = 'Fix failed. Check logs.';
                    }}
                    checkbox.checked = false;
                    checkbox.disabled = false;
                }}
            }}
        </script>
    </body>
    </html>
    """


async def _run_gateway_check(auto_fix: bool) -> tuple[dict[str, Any], str]:
    """Execute the comprehensive check and capture stdout."""

    if run_comprehensive_check is None:
        raise HTTPException(
            status_code=503,
            detail="check_and_fix_gateway_models module is unavailable in this deployment.",
        )

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        # run_comprehensive_check is now async, so we await it directly
        results = await run_comprehensive_check(auto_fix=auto_fix, verbose=False)  # type: ignore[arg-type]
    return results, buffer.getvalue()


async def _run_single_gateway_check(gateway: str, auto_fix: bool) -> tuple[dict[str, Any], str]:
    """Execute the check for a single gateway and capture stdout."""

    if run_comprehensive_check is None:
        raise HTTPException(
            status_code=503,
            detail="check_and_fix_gateway_models module is unavailable in this deployment.",
        )

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        # run_comprehensive_check is now async, so we await it directly
        results = await run_comprehensive_check(  # type: ignore[arg-type]
            auto_fix=auto_fix, verbose=False, gateway=gateway
        )
    return results, buffer.getvalue()


@router.post("/health/gateways/{gateway}/fix", tags=["health"])
async def trigger_gateway_fix(
    gateway: str,
    auto_fix: bool = Query(
        True, description="Attempt to auto-fix the specified gateway after running diagnostics."
    ),
):
    """
    Trigger a targeted gateway diagnostics run with optional auto-fix.

    Returns structured status along with captured logs so operators can review
    what happened without leaving the dashboard.
    """
    try:
        results, log_output = await _run_single_gateway_check(gateway=gateway, auto_fix=auto_fix)
        gateway_key = gateway.lower()
        gateway_payload = results.get("gateways", {}).get(gateway_key)

        if not gateway_payload:
            raise HTTPException(
                status_code=404, detail=f"Gateway '{gateway}' not found in health check results."
            )

        return {
            "success": True,
            "gateway": gateway_key,
            "auto_fix": auto_fix,
            "timestamp": results.get("timestamp"),
            "data": {
                "final_status": gateway_payload.get("final_status"),
                "auto_fix_attempted": gateway_payload.get("auto_fix_attempted"),
                "auto_fix_successful": gateway_payload.get("auto_fix_successful"),
                "endpoint_test": gateway_payload.get("endpoint_test"),
                "cache_test": gateway_payload.get("cache_test"),
            },
            "summary": {
                "total_gateways": results.get("total_gateways"),
                "healthy": results.get("healthy"),
                "unhealthy": results.get("unhealthy"),
                "unconfigured": results.get("unconfigured"),
                "fixed": results.get("fixed"),
            },
            "logs": log_output.strip(),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - unexpected failures
        logger.exception("Failed to trigger gateway fix for %s", gateway)
        raise HTTPException(
            status_code=500, detail=f"Failed to run gateway fix: {exc}"
        ) from exc  # pragma: no cover - unexpected failures


# ============================================================================
# Cache Management Endpoints
# ============================================================================


@router.get("/admin/cache/debouncer/stats", tags=["admin", "cache", "monitoring"])
async def get_cache_debouncer_stats(admin_user: dict = Depends(require_admin)):
    """
    Get cache debouncer statistics (Issue #1099).

    Shows effectiveness of debouncing in preventing cache thrashing:
    - Number of invalidations scheduled
    - Number executed vs coalesced (deduplicated)
    - Efficiency percentage
    - Currently pending invalidations

    Returns detailed metrics about the cache debouncing system.
    """
    try:
        from src.services.model_catalog_cache import _invalidation_debouncer

        debouncer_stats = _invalidation_debouncer.get_stats()

        return {
            "success": True,
            "debouncer": {
                "status": "active",
                "delay_seconds": _invalidation_debouncer.delay,
                "scheduled": debouncer_stats["scheduled"],
                "executed": debouncer_stats["executed"],
                "coalesced": debouncer_stats["coalesced"],
                "pending": debouncer_stats["pending_count"],
                "efficiency_percent": debouncer_stats["efficiency_percent"],
                "description": "Coalesces rapid invalidation requests to prevent cache thrashing (Issue #1099)",
            },
            "impact": {
                "operations_prevented": debouncer_stats["coalesced"],
                "operations_saved_percent": debouncer_stats["efficiency_percent"],
                "status": "healthy" if debouncer_stats["efficiency_percent"] > 0 else "idle"
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get cache debouncer stats: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get cache debouncer stats: {str(e)}"
        ) from e


@router.get("/admin/cache/warmer/stats", tags=["admin", "cache", "monitoring"])
async def get_cache_warmer_stats(admin_user: dict = Depends(require_admin)):
    """
    Get cache warmer statistics including:
    - Background refresh counts
    - Request coalescing effectiveness
    - Error rates
    - Currently in-flight refreshes

    Returns detailed metrics about the cache warming system.
    """
    try:
        from src.services.cache_warmer import get_cache_warmer
        from src.services.model_catalog_cache import get_catalog_cache_stats
        from src.services.local_memory_cache import get_local_cache

        warmer = get_cache_warmer()
        warmer_stats = warmer.get_stats()

        # Get catalog cache stats
        catalog_cache_stats = get_catalog_cache_stats()

        # Get local cache stats
        local_cache = get_local_cache()
        local_cache_stats = local_cache.get_stats()

        return {
            "cache_warmer": {
                "status": "healthy",
                "refreshes": warmer_stats["refreshes"],
                "coalesced_requests": warmer_stats["coalesced"],
                "errors": warmer_stats["errors"],
                "skipped": warmer_stats["skipped"],
                "in_flight": warmer_stats["in_flight"],
                "description": "Background cache warming prevents thundering herd problems",
            },
            "redis_cache": catalog_cache_stats,
            "local_memory_cache": {
                **local_cache_stats,
                "description": "Fallback cache when Redis is unavailable",
            },
            "health_summary": {
                "redis_available": catalog_cache_stats.get("redis_available", False),
                "local_cache_entries": local_cache_stats["entries"],
                "stale_hit_rate": round(
                    local_cache_stats["stale_hits"]
                    / max(local_cache_stats["total_requests"], 1)
                    * 100,
                    2,
                ),
                "cache_warmer_effectiveness": round(
                    (warmer_stats["refreshes"] - warmer_stats["errors"])
                    / max(warmer_stats["refreshes"], 1)
                    * 100,
                    2,
                )
                if warmer_stats["refreshes"] > 0
                else 100.0,
            },
        }
    except Exception as e:
        logger.error(f"Failed to get cache warmer stats: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get cache warmer stats: {str(e)}"
        ) from e


@router.get("/admin/cache/status", tags=["admin", "cache"])
async def get_cache_status(admin_user: dict = Depends(require_admin)):
    """
    Get cache status for all gateways.

    Returns information about:
    - Number of models cached per gateway
    - Last refresh timestamp
    - TTL (Time To Live)
    - Cache size estimate

    **Example Response:**
    ```json
    {
        "openrouter": {
            "models_cached": 250,
            "last_refresh": "2025-01-15T10:30:00Z",
            "ttl_seconds": 3600,
            "status": "healthy"
        },
        ...
    }
    ```
    """
    try:
        cache_status = {}
        # Get all gateways dynamically from GATEWAY_CONFIG
        gateways = get_all_gateway_names()

        for gateway in gateways:
            try:
                cache_info = get_models_cache(gateway)
            except StopIteration:
                cache_info = getattr(get_models_cache, "return_value", None)

            if cache_info:
                models = cache_info.get("data") or []
                timestamp = cache_info.get("timestamp")
                ttl = cache_info.get("ttl", 3600)

                # Calculate cache age
                cache_age_seconds = None
                is_stale = False
                if timestamp:
                    normalized_timestamp = _normalize_timestamp(timestamp)
                    if normalized_timestamp:
                        age = (datetime.now(timezone.utc) - normalized_timestamp).total_seconds()
                        cache_age_seconds = int(age)
                        is_stale = age > ttl

                # Convert timestamp to ISO format string
                normalized_timestamp = _normalize_timestamp(timestamp)
                last_refresh = normalized_timestamp.isoformat() if normalized_timestamp else None

                cache_status[gateway] = {
                    "models_cached": len(models) if models else 0,
                    "last_refresh": last_refresh,
                    "ttl_seconds": ttl,
                    "cache_age_seconds": cache_age_seconds,
                    "status": "stale" if is_stale else ("healthy" if models else "empty"),
                    "has_data": bool(models),
                }
            else:
                cache_status[gateway] = {
                    "models_cached": 0,
                    "last_refresh": None,
                    "ttl_seconds": 3600,
                    "cache_age_seconds": None,
                    "status": "empty",
                    "has_data": False,
                }

        # Add providers cache
        providers_cache = get_providers_cache()
        if providers_cache:
            providers = providers_cache.get("data") or []
            timestamp = providers_cache.get("timestamp")
            ttl = providers_cache.get("ttl", 3600)

            cache_age_seconds = None
            is_stale = False
            if timestamp:
                normalized_timestamp = _normalize_timestamp(timestamp)
                if normalized_timestamp:
                    age = (datetime.now(timezone.utc) - normalized_timestamp).total_seconds()
                    cache_age_seconds = int(age)
                    is_stale = age > ttl

            # Convert timestamp to ISO format string
            normalized_timestamp = _normalize_timestamp(timestamp)
            last_refresh = normalized_timestamp.isoformat() if normalized_timestamp else None

            cache_status["providers"] = {
                "providers_cached": len(providers) if providers else 0,
                "last_refresh": last_refresh,
                "ttl_seconds": ttl,
                "cache_age_seconds": cache_age_seconds,
                "status": "stale" if is_stale else ("healthy" if providers else "empty"),
                "has_data": bool(providers),
            }
        else:
            cache_status["providers"] = {
                "providers_cached": 0,
                "last_refresh": None,
                "ttl_seconds": 3600,
                "cache_age_seconds": None,
                "status": "empty",
                "has_data": False,
            }

        return {
            "success": True,
            "data": cache_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get cache status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get cache status: {str(e)}") from e


@router.post("/admin/cache/refresh/{gateway}", tags=["admin", "cache"])
async def refresh_gateway_cache(
    gateway: str,
    force: bool = Query(False, description="Force refresh even if cache is still valid"),
    admin_user: dict = Depends(require_admin),
):
    """
    Force refresh cache for a specific gateway.

    **Parameters:**
    - `gateway`: The gateway to refresh (openrouter, featherless, etc.)
    - `force`: If true, refresh even if cache is still valid

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/cache/refresh/openrouter?force=true"
    ```
    """
    try:
        gateway = gateway.lower()
        # Get all gateway names for validation (includes deepinfra for special handling)
        all_gateways = get_all_gateway_names()

        if gateway not in all_gateways:
            # Get cacheable gateways for error message
            valid_gateways = get_cacheable_gateways()
            raise HTTPException(
                status_code=400,
                detail=f"Invalid gateway. Must be one of: {', '.join(sorted(valid_gateways + ['deepinfra']))}",
            )

        # Check if refresh is needed
        try:
            cache_info = get_models_cache(gateway)
        except StopIteration:
            cache_info = getattr(get_models_cache, "return_value", None)
        needs_refresh = force

        if not force and cache_info:
            timestamp = cache_info.get("timestamp")
            ttl = cache_info.get("ttl", 3600)
            if timestamp:
                normalized_timestamp = _normalize_timestamp(timestamp)
                if normalized_timestamp:
                    age = (datetime.now(timezone.utc) - normalized_timestamp).total_seconds()
                    needs_refresh = age > ttl

        if not needs_refresh:
            return {
                "success": True,
                "message": f"Cache for {gateway} is still valid. Use force=true to refresh anyway.",
                "gateway": gateway,
                "action": "skipped",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # Clear existing cache
        clear_models_cache(gateway)

        # Fetch new data based on gateway
        logger.info(f"Refreshing cache for {gateway}...")

        # Get the fetch function dynamically
        fetch_func = get_fetch_function(gateway)
        if fetch_func:
            try:
                if inspect.iscoroutinefunction(fetch_func):
                    # Async fetch function - await directly
                    await fetch_func()
                else:
                    # Sync fetch function - run in thread to avoid blocking event loop
                    await asyncio.to_thread(fetch_func)
            except Exception as fetch_error:
                logger.error(f"Error fetching models from {gateway}: {fetch_error}")
                raise HTTPException(
                    status_code=500, detail=f"Failed to fetch models from {gateway}"
                ) from fetch_error
        elif gateway == "deepinfra":
            # DeepInfra doesn't have bulk fetching, only individual model fetching
            return {
                "success": False,
                "message": "DeepInfra does not support bulk cache refresh. Models are fetched on-demand.",
                "gateway": gateway,
                "action": "not_supported",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unknown gateway: {gateway}")

        # Get updated cache info
        try:
            new_cache_info = get_models_cache(gateway)
        except StopIteration:
            new_cache_info = getattr(get_models_cache, "return_value", None)
        models_count = len(new_cache_info.get("data", [])) if new_cache_info else 0

        return {
            "success": True,
            "message": f"Cache refreshed successfully for {gateway}",
            "gateway": gateway,
            "models_cached": models_count,
            "action": "refreshed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to refresh cache for {gateway}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to refresh cache: {str(e)}") from e


@router.post("/admin/cache/clear", tags=["admin", "cache"])
async def clear_all_caches(
    gateway: str | None = Query(
        None, description="Specific gateway to clear, or all if not specified"
    ),
    admin_user: dict = Depends(require_admin),
):
    """
    Clear cache for all gateways or a specific gateway.

    **Warning:** This will remove all cached data. Use with caution.
    """
    try:
        if gateway:
            gateway = gateway.lower()
            clear_models_cache(gateway)
            return {
                "success": True,
                "message": f"Cache cleared for {gateway}",
                "gateway": gateway,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        else:
            # Clear all gateways dynamically
            gateways = get_all_gateway_names()
            for gw in gateways:
                clear_models_cache(gw)
            clear_providers_cache()

            return {
                "success": True,
                "message": "All caches cleared",
                "gateways_cleared": gateways + ["providers"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}") from e


# ============================================================================
# Gateway Health Monitoring Endpoints
# ============================================================================


@router.get("/health/gateways", tags=["health"])
async def check_all_gateways():
    """
    Get health status of all 28 configured gateways from health-service cache.

    **Performance:** Uses cached data from health-service (sub-second response).
    **Data Source:** Redis cache updated by health-service every 60 seconds.
    **Coverage:** All 28 gateways from GATEWAY_CONFIG.

    **Returns:**
    ```json
    {
        "success": true,
        "data": {
            "openrouter": {
                "status": "healthy",
                "latency_ms": 150,
                "available": true,
                "last_check": "2025-01-15T10:30:00Z",
                "error": null
            },
            ...
        },
        "summary": {
            "total_gateways": 28,
            "healthy": 20,
            "degraded": 0,
            "unhealthy": 0,
            "unconfigured": 8,
            "overall_health_percentage": 100.0
        },
        "timestamp": "2025-01-15T10:30:00Z",
        "metadata": {
            "cache_age_seconds": 45,
            "data_source": "health-service-cache"
        }
    }
    ```

    **Note:** If no cached data is available, returns empty data with metadata.
    Run model sync to populate database if gateways are showing as unconfigured.
    """
    try:
        from src.services.simple_health_cache import simple_health_cache

        # Get cached gateway health from health-service (fast)
        cached_gateways = simple_health_cache.get_gateways_health()

        if not cached_gateways:
            # No cached data - health-service may not be running or no models synced
            logger.warning("No gateway health data in cache - health-service may not be running or model sync needed")
            return {
                "success": True,
                "data": {},
                "summary": {
                    "total_gateways": 0,
                    "healthy": 0,
                    "degraded": 0,
                    "unhealthy": 0,
                    "unconfigured": 0,
                    "overall_health_percentage": 0,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": {
                    "cache_age_seconds": None,
                    "data_source": "none",
                    "warning": "No cached data available. Ensure health-service is running and model sync has been executed."
                }
            }

        # Process cached gateway data
        health_status = {}
        healthy_count = 0
        degraded_count = 0
        unhealthy_count = 0
        unconfigured_count = 0

        for gateway_name, gateway_info in cached_gateways.items():
            # Normalize status
            status = gateway_info.get('status', 'unknown').lower()
            latency_ms = gateway_info.get('latency_ms', 0)

            if status in ['healthy', 'online']:
                final_status = 'healthy'
                healthy_count += 1
            elif status in ['degraded']:
                final_status = 'degraded'
                degraded_count += 1
            elif status in ['unhealthy', 'offline', 'error', 'timeout']:
                final_status = 'unhealthy'
                unhealthy_count += 1
            else:
                final_status = 'unconfigured'
                unconfigured_count += 1

            health_status[gateway_name] = {
                "status": final_status,
                "latency_ms": latency_ms if latency_ms else None,
                "available": gateway_info.get('available', final_status == 'healthy'),
                "last_check": gateway_info.get('last_check', datetime.now(timezone.utc).isoformat()),
                "error": gateway_info.get('error', None),
            }

        # Calculate overall health
        total_gateways = len(health_status)
        total_configured = healthy_count + degraded_count + unhealthy_count

        return {
            "success": True,
            "data": health_status,
            "summary": {
                "total_gateways": total_gateways,
                "healthy": healthy_count,
                "degraded": degraded_count,
                "unhealthy": unhealthy_count,
                "unconfigured": unconfigured_count,
                "overall_health_percentage": round(
                    (healthy_count / total_configured * 100) if total_configured > 0 else 0,
                    1
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "cache_age_seconds": None,  # Could be calculated if we store cache timestamp
                "data_source": "health-service-cache",
                "note": "Data refreshed every 60 seconds by health-service"
            }
        }

    except Exception as e:
        logger.error(f"Failed to retrieve gateway health from cache: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve gateway health: {str(e)}"
        ) from e


@router.get("/health/gateways/dashboard", response_class=HTMLResponse, tags=["health"])
async def gateway_health_dashboard(
    auto_fix: bool = Query(
        False,
        description="Attempt to auto-fix failing gateways using the CLI logic before rendering the dashboard.",
    )
):
    """Render an HTML dashboard view of the comprehensive gateway health check."""

    results, log_output = await _run_gateway_check(auto_fix=auto_fix)
    html = _render_gateway_dashboard(results, log_output, auto_fix)
    return HTMLResponse(content=html)


@router.get("/health/gateways/dashboard/data", tags=["health"])
async def gateway_health_dashboard_data(
    auto_fix: bool = Query(
        False,
        description="Attempt to auto-fix failing gateways using the CLI logic before returning the payload.",
    ),
    include_logs: bool = Query(
        False, description="Include captured stdout logs from the CLI run in the response."
    ),
):
    """Expose the dashboard data as JSON for programmatic consumption."""

    results, log_output = await _run_gateway_check(auto_fix=auto_fix)

    # Enrich gateway data with models and model metadata from cache
    gateways = results.get("gateways", {})
    for gateway_name, gateway_data in gateways.items():
        try:
            # Get cached models for this gateway
            cache_info = get_models_cache(gateway_name)
            if cache_info and cache_info.get("data"):
                models = cache_info.get("data", [])
                timestamp = cache_info.get("timestamp")

                # Add models array to gateway data
                gateway_data["models"] = models
                gateway_data["models_metadata"] = {
                    "count": len(models),
                    "last_updated": (
                        _normalize_timestamp(timestamp).isoformat()
                        if _normalize_timestamp(timestamp)
                        else None
                    ),
                }
            else:
                gateway_data["models"] = []
                gateway_data["models_metadata"] = {
                    "count": 0,
                    "last_updated": None,
                }
        except Exception as e:
            logger.warning(f"Failed to enrich gateway {gateway_name} with models: {e}")
            gateway_data["models"] = []
            gateway_data["models_metadata"] = {
                "count": 0,
                "last_updated": None,
                "error": str(e),
            }

    payload: dict[str, Any] = {
        "success": True,
        "timestamp": results.get("timestamp"),
        "auto_fix": auto_fix,
        "summary": {
            "total_gateways": results.get("total_gateways"),
            "healthy": results.get("healthy"),
            "unhealthy": results.get("unhealthy"),
            "unconfigured": results.get("unconfigured"),
            "auto_fixed": results.get("fixed"),
        },
        "gateways": gateways,
    }

    if include_logs:
        payload["logs"] = log_output

    return payload


@router.get("/health/{gateway}", tags=["health"])
async def check_single_gateway(gateway: str):
    """
    Check health status of a specific gateway with detailed diagnostics.

    **Parameters:**
    - `gateway`: Gateway name (openrouter, featherless, etc.)

    **Returns detailed health information including:**
    - API connectivity
    - Response latency
    - Models available
    - Cache status
    """
    try:
        # Get all gateway health first
        all_health = await check_all_gateways()
        gateway_health = all_health["data"].get(gateway.lower())

        if not gateway_health:
            raise HTTPException(status_code=404, detail=f"Gateway '{gateway}' not found")

        # Add cache information
        cache_info = get_models_cache(gateway.lower())
        if cache_info:
            models = cache_info.get("data") or []
            timestamp = cache_info.get("timestamp")

            normalized_timestamp = _normalize_timestamp(timestamp)

            gateway_health["cache"] = {
                "models_cached": len(models),
                "last_refresh": normalized_timestamp.isoformat() if normalized_timestamp else None,
                "has_data": bool(models),
            }
        else:
            gateway_health["cache"] = {"models_cached": 0, "last_refresh": None, "has_data": False}

        return {
            "success": True,
            "gateway": gateway.lower(),
            "data": gateway_health,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to check gateway {gateway}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to check gateway health: {str(e)}"
        ) from e


# ============================================================================
# Modelz Cache Management Endpoints
# ============================================================================


@router.get("/admin/cache/modelz/status", tags=["admin", "cache", "modelz"])
async def get_modelz_cache_status(admin_user: dict = Depends(require_admin)):
    """
    Get the current status of the Modelz cache.

    Returns information about:
    - Cache validity status
    - Number of tokens cached
    - Last refresh timestamp
    - Cache age and TTL

    **Example Response:**
    ```json
    {
      "status": "valid",
      "message": "Modelz cache is valid",
      "cache_size": 53,
      "timestamp": 1705123456.789,
      "ttl": 1800,
      "age_seconds": 245.3,
      "is_valid": true
    }
    ```
    """
    try:
        cache_status = get_modelz_cache_status_func()
        return {
            "success": True,
            "data": cache_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get Modelz cache status: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get Modelz cache status: {str(e)}"
        ) from e


@router.post("/admin/cache/modelz/refresh", tags=["admin", "cache", "modelz"])
async def refresh_modelz_cache_endpoint(admin_user: dict = Depends(require_admin)):
    """
    Force refresh the Modelz cache by fetching fresh data from the API.

    This endpoint:
    - Clears the existing Modelz cache
    - Fetches fresh data from the Modelz API
    - Updates the cache with new data

    **Example Response:**
    ```json
    {
      "success": true,
      "data": {
        "status": "success",
        "message": "Modelz cache refreshed with 53 tokens",
        "cache_size": 53,
        "timestamp": 1705123456.789,
        "ttl": 1800
      },
      "timestamp": "2024-01-15T10:30:45.123Z"
    }
    ```
    """
    try:
        logger.info("Refreshing Modelz cache via API endpoint")
        refresh_result = await refresh_modelz_cache()

        return {
            "success": True,
            "data": refresh_result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to refresh Modelz cache: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to refresh Modelz cache: {str(e)}"
        ) from e


@router.delete("/admin/cache/modelz/clear", tags=["admin", "cache", "modelz"])
async def clear_modelz_cache_endpoint(admin_user: dict = Depends(require_admin)):
    """
    Clear the Modelz cache.

    This endpoint:
    - Removes all cached Modelz data
    - Resets cache timestamps
    - Forces next request to fetch fresh data from API

    **Example Response:**
    ```json
    {
      "success": true,
      "message": "Modelz cache cleared successfully",
      "timestamp": "2024-01-15T10:30:45.123Z"
    }
    ```
    """
    try:
        logger.info("Clearing Modelz cache via API endpoint")
        clear_modelz_cache()

        return {
            "success": True,
            "message": "Modelz cache cleared successfully",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to clear Modelz cache: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to clear Modelz cache: {str(e)}"
        ) from e


_last_invalidation_time: float = 0.0
_INVALIDATION_COOLDOWN_SECONDS = 30.0


def _perform_cache_invalidation(gateway: str | None, cache_type: str | None) -> None:
    """
    Background task: Perform cache invalidation operations with debouncing.

    This function runs in the background to avoid blocking the API response.
    Performs all cache invalidation operations including gateway caches,
    provider caches, and pricing refresh.

    Includes a 30-second cooldown: if a full invalidation ran recently,
    duplicate calls are skipped to prevent thundering herd from rapid-fire
    admin dashboard clicks or retries.

    Args:
        gateway: Optional gateway name to invalidate (e.g., 'openrouter', 'together')
        cache_type: Optional cache type ('models', 'providers', 'pricing')
    """
    global _last_invalidation_time
    import time

    # Skip duplicate full-invalidation calls within cooldown window
    if not gateway and not cache_type:
        now = time.monotonic()
        elapsed = now - _last_invalidation_time
        if elapsed < _INVALIDATION_COOLDOWN_SECONDS:
            logger.info(
                f"Background task: Skipping duplicate full cache invalidation "
                f"({elapsed:.1f}s since last, cooldown={_INVALIDATION_COOLDOWN_SECONDS}s)"
            )
            return
        _last_invalidation_time = now

    try:
        start_time = datetime.now(timezone.utc)
        invalidated = []

        if gateway:
            gateway = gateway.lower()
            logger.info(f"Background task: Invalidating cache for gateway '{gateway}' (debounced)")
            # Enable debouncing for frontend-triggered invalidations (Issue #1099)
            clear_models_cache(gateway, debounce=True)
            invalidated.append(f"models:{gateway}")
        elif cache_type == "models":
            # Clear all gateway model caches using batch operation (Issue #1099)
            gateways = get_all_gateway_names()
            logger.info(f"Background task: Batch invalidating model caches for {len(gateways)} gateways")
            # Use batch invalidation for better performance (1 Redis operation vs 30+)
            from src.services.model_catalog_cache import get_model_catalog_cache
            cache = get_model_catalog_cache()
            result = cache.invalidate_providers_batch(gateways, cascade=False)
            logger.info(f"Batch invalidation result: {result}")
            invalidated.extend([f"models:{gw}" for gw in gateways])
        elif cache_type == "providers":
            logger.info("Background task: Invalidating provider cache")
            clear_providers_cache()
            invalidated.append("providers")
        elif cache_type == "pricing":
            logger.info("Background task: Refreshing pricing cache")
            refresh_pricing_cache()
            invalidated.append("pricing")
        else:
            # Clear all caches using batch operation (Issue #1099)
            gateways = get_all_gateway_names()
            logger.info(f"Background task: Batch invalidating all caches ({len(gateways)} gateways + providers + pricing)")
            # Use batch invalidation for better performance (1 Redis operation vs 30+)
            from src.services.model_catalog_cache import get_model_catalog_cache
            cache = get_model_catalog_cache()
            result = cache.invalidate_providers_batch(gateways, cascade=False)
            logger.info(f"Batch invalidation result: {result}")
            clear_providers_cache()
            refresh_pricing_cache()
            invalidated = [f"models:{gw}" for gw in gateways] + ["providers", "pricing"]

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info(
            f"Background task: Cache invalidation scheduled in {duration:.2f}s. "
            f"Invalidated (debounced): {', '.join(invalidated[:5])}{' and more...' if len(invalidated) > 5 else ''}"
        )

    except Exception as e:
        logger.error(f"Background task: Failed to invalidate cache: {e}", exc_info=True)


@router.post("/admin/api/cache/invalidate", tags=["admin", "cache"])
async def invalidate_cache(
    background_tasks: BackgroundTasks,
    gateway: str | None = Query(
        None, description="Specific gateway cache to invalidate, or all if not specified"
    ),
    cache_type: str | None = Query(
        None, description="Type of cache to invalidate: 'models', 'providers', 'pricing', or all if not specified"
    ),
    admin_user: dict = Depends(require_admin),
):
    """
    Invalidate cache for specified gateway or cache type.

    This endpoint is called by the frontend dashboard to invalidate caches
    after configuration changes.

    **Performance:** Returns immediately and performs invalidation in the background.
    This prevents long wait times when clearing multiple gateway caches.

    **Parameters:**
    - `gateway`: Optional gateway name to invalidate (e.g., 'openrouter', 'together')
    - `cache_type`: Optional cache type ('models', 'providers', 'pricing')

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/api/cache/invalidate?gateway=openrouter"
    ```

    **Note:** The actual cache invalidation happens asynchronously in the background.
    Check logs for completion status.
    """
    try:
        # Determine what will be invalidated for response message
        if gateway:
            scope = f"gateway '{gateway}'"
        elif cache_type:
            scope = f"{cache_type} cache"
        else:
            scope = "all caches"

        # Schedule background task
        background_tasks.add_task(_perform_cache_invalidation, gateway, cache_type)

        logger.info(f"Cache invalidation task scheduled for: {scope}")

        return {
            "success": True,
            "message": f"Cache invalidation started in background for {scope}",
            "status": "processing",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to schedule cache invalidation: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to schedule cache invalidation: {str(e)}"
        ) from e


@router.post("/admin/cache/pricing/refresh", tags=["admin", "cache", "pricing"])
async def refresh_pricing_cache_endpoint(admin_user: dict = Depends(require_admin)):
    """
    Force refresh the pricing cache by reloading from the manual pricing file.

    This endpoint:
    - Clears the existing pricing cache
    - Reloads pricing data from manual_pricing.json
    - Updates the in-memory pricing cache

    **Example Response:**
    ```json
    {
      "success": true,
      "message": "Pricing cache refreshed successfully",
      "providers_loaded": 15,
      "timestamp": "2024-01-15T10:30:45.123Z"
    }
    ```
    """
    try:
        logger.info("Refreshing pricing cache via API endpoint")
        pricing_data = refresh_pricing_cache()

        # Count providers (excluding metadata)
        provider_count = len([k for k in pricing_data.keys() if k != "_metadata"])

        return {
            "success": True,
            "message": "Pricing cache refreshed successfully",
            "providers_loaded": provider_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to refresh pricing cache: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to refresh pricing cache: {str(e)}"
        ) from e


# ============================================================================
# Velocity Mode Status Endpoint
# ============================================================================


@router.get("/velocity-mode-status", tags=["security", "monitoring"])
async def get_velocity_mode_status():
    """
    Get the current velocity mode status from the security middleware.

    Velocity mode is an automatic protection system that activates during high error rates
    to protect the service from cascading failures by temporarily reducing rate limits.

    **Returns:**
    ```json
    {
        "active": false,
        "until": null,
        "remaining_seconds": 0,
        "trigger_count": 0,
        "current_error_rate": 0.0,
        "sample_size": 0,
        "threshold": 25.0,
        "limits": {
            "normal": {
                "ip_limit": 300,
                "strict_ip_limit": 60,
                "fingerprint_limit": 100
            },
            "velocity": {
                "ip_limit": 150,
                "strict_ip_limit": 30,
                "fingerprint_limit": 50
            }
        }
    }
    ```

    **Status Codes:**
    - 200: Successfully retrieved velocity mode status
    - 503: Security middleware not available
    """
    try:
        # Import here to avoid circular dependencies
        from src.middleware.security_middleware import (
            DEFAULT_IP_LIMIT,
            STRICT_IP_LIMIT,
            FINGERPRINT_LIMIT,
            VELOCITY_ERROR_THRESHOLD,
            VELOCITY_WINDOW_SECONDS,
            VELOCITY_LIMIT_MULTIPLIER,
        )

        # Try to get the security middleware instance from the app
        from src.main import app

        security_middleware = None
        for middleware in app.user_middleware:
            if hasattr(middleware, "cls") and middleware.cls.__name__ == "SecurityMiddleware":
                # Get the actual middleware instance
                if hasattr(middleware, "kwargs"):
                    security_middleware = middleware.kwargs.get("dispatch")
                break

        # If we can't find it via app.user_middleware, try getting it from the app's middleware stack
        if not security_middleware and hasattr(app, "middleware_stack"):
            for mw in app.middleware_stack:
                if hasattr(mw, "app") and mw.app.__class__.__name__ == "SecurityMiddleware":
                    security_middleware = mw.app
                    break

        if not security_middleware:
            # Return default status if middleware not found
            logger.warning("Security middleware instance not found - returning default status")
            return {
                "active": False,
                "until": None,
                "remaining_seconds": 0,
                "trigger_count": 0,
                "current_error_rate": 0.0,
                "sample_size": 0,
                "threshold": VELOCITY_ERROR_THRESHOLD * 100,
                "limits": {
                    "normal": {
                        "ip_limit": DEFAULT_IP_LIMIT,
                        "strict_ip_limit": STRICT_IP_LIMIT,
                        "fingerprint_limit": FINGERPRINT_LIMIT,
                    },
                    "velocity": {
                        "ip_limit": int(DEFAULT_IP_LIMIT * VELOCITY_LIMIT_MULTIPLIER),
                        "strict_ip_limit": int(STRICT_IP_LIMIT * VELOCITY_LIMIT_MULTIPLIER),
                        "fingerprint_limit": int(FINGERPRINT_LIMIT * VELOCITY_LIMIT_MULTIPLIER),
                    },
                },
                "warning": "Security middleware instance not found - showing configuration only",
            }

        # Get current velocity mode status from middleware
        import time

        now = time.time()
        is_active = security_middleware._is_velocity_mode_active()

        # Calculate current error rate
        cutoff = now - VELOCITY_WINDOW_SECONDS
        recent_requests = [
            (ts, err, status)
            for ts, err, status in security_middleware._request_log
            if ts >= cutoff
        ]

        error_rate = 0.0
        if len(recent_requests) > 0:
            error_count = sum(1 for _, is_error, _ in recent_requests if is_error)
            error_rate = error_count / len(recent_requests)

        return {
            "active": is_active,
            "until": security_middleware._velocity_mode_until if is_active else None,
            "remaining_seconds": (
                max(0, int(security_middleware._velocity_mode_until - now)) if is_active else 0
            ),
            "trigger_count": security_middleware._velocity_mode_triggered_count,
            "current_error_rate": round(error_rate * 100, 2),
            "sample_size": len(recent_requests),
            "threshold": VELOCITY_ERROR_THRESHOLD * 100,
            "limits": {
                "normal": {
                    "ip_limit": DEFAULT_IP_LIMIT,
                    "strict_ip_limit": STRICT_IP_LIMIT,
                    "fingerprint_limit": FINGERPRINT_LIMIT,
                },
                "velocity": {
                    "ip_limit": int(DEFAULT_IP_LIMIT * VELOCITY_LIMIT_MULTIPLIER),
                    "strict_ip_limit": int(STRICT_IP_LIMIT * VELOCITY_LIMIT_MULTIPLIER),
                    "fingerprint_limit": int(FINGERPRINT_LIMIT * VELOCITY_LIMIT_MULTIPLIER),
                },
            },
        }

    except Exception as e:
        logger.error(f"Failed to get velocity mode status: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get velocity mode status: {str(e)}"
        ) from e


# ============================================================================
# Pricing Health Monitoring Endpoints - DEPRECATED (Phase 2)
# ============================================================================
# These endpoints were removed as part of the pricing sync deprecation (Issue #1062).
# Pricing health is now monitored through model sync and the models_catalog table.
