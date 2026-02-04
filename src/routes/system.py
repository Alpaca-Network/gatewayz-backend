"""
System endpoints for cache management and gateway health monitoring
Phase 2 implementation
"""

import io
import json
import logging
import os
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from html import escape
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.cache import (
    clear_models_cache,
    clear_modelz_cache,
    clear_providers_cache,
    get_models_cache,
    get_providers_cache,
)
from src.config import Config
from src.services.huggingface_models import fetch_models_from_hug
from src.services.models import (
    fetch_models_from_aihubmix,
    fetch_models_from_aimo,
    fetch_models_from_anannas,
    fetch_models_from_chutes,
    fetch_models_from_fal,
    fetch_models_from_featherless,
    fetch_models_from_fireworks,
    fetch_models_from_groq,
    fetch_models_from_near,
    fetch_models_from_openrouter,
    fetch_models_from_together,
)
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


@router.get("/cache/warmer/stats", tags=["cache", "monitoring"])
async def get_cache_warmer_stats():
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


@router.get("/cache/status", tags=["cache"])
async def get_cache_status():
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


@router.post("/cache/refresh/{gateway}", tags=["cache"])
async def refresh_gateway_cache(
    gateway: str,
    force: bool = Query(False, description="Force refresh even if cache is still valid"),
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
            # Most fetch functions are sync, so we need to handle both
            try:
                result = fetch_func()
                # If it's a coroutine, await it
                if hasattr(result, "__await__"):
                    await result
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


@router.post("/cache/clear", tags=["cache"])
async def clear_all_caches(
    gateway: str | None = Query(
        None, description="Specific gateway to clear, or all if not specified"
    )
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


@router.get("/cache/modelz/status", tags=["cache", "modelz"])
async def get_modelz_cache_status():
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


@router.post("/cache/modelz/refresh", tags=["cache", "modelz"])
async def refresh_modelz_cache_endpoint():
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


@router.delete("/cache/modelz/clear", tags=["cache", "modelz"])
async def clear_modelz_cache_endpoint():
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


@router.post("/api/cache/invalidate", tags=["cache"])
async def invalidate_cache(
    gateway: str | None = Query(
        None, description="Specific gateway cache to invalidate, or all if not specified"
    ),
    cache_type: str | None = Query(
        None, description="Type of cache to invalidate: 'models', 'providers', 'pricing', or all if not specified"
    ),
):
    """
    Invalidate cache for specified gateway or cache type.

    This endpoint is called by the frontend dashboard to invalidate caches
    after configuration changes.

    **Parameters:**
    - `gateway`: Optional gateway name to invalidate (e.g., 'openrouter', 'together')
    - `cache_type`: Optional cache type ('models', 'providers', 'pricing')

    **Example:**
    ```bash
    curl -X POST "http://localhost:8000/api/cache/invalidate?gateway=openrouter"
    ```
    """
    try:
        invalidated = []

        if gateway:
            gateway = gateway.lower()
            clear_models_cache(gateway)
            invalidated.append(f"models:{gateway}")
        elif cache_type == "models":
            # Clear all gateway model caches
            gateways = get_all_gateway_names()
            for gw in gateways:
                clear_models_cache(gw)
            invalidated.extend([f"models:{gw}" for gw in gateways])
        elif cache_type == "providers":
            clear_providers_cache()
            invalidated.append("providers")
        elif cache_type == "pricing":
            refresh_pricing_cache()
            invalidated.append("pricing")
        else:
            # Clear all caches
            gateways = get_all_gateway_names()
            for gw in gateways:
                clear_models_cache(gw)
            clear_providers_cache()
            refresh_pricing_cache()
            invalidated = [f"models:{gw}" for gw in gateways] + ["providers", "pricing"]

        return {
            "success": True,
            "message": "Cache invalidated successfully",
            "invalidated": invalidated,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to invalidate cache: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to invalidate cache: {str(e)}") from e


@router.post("/cache/pricing/refresh", tags=["cache", "pricing"])
async def refresh_pricing_cache_endpoint():
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
# Pricing Health Monitoring Endpoints (Issue #1038)
# ============================================================================


@router.get("/health/pricing", tags=["health", "pricing"])
async def check_pricing_health():
    """
    Check overall health of the pricing system.

    Monitors:
    - Pricing data staleness (alerts if >24h old)
    - Default pricing usage (models without pricing data)
    - Provider sync health (last sync status)

    Returns comprehensive health status with actionable information.

    **Example Response:**
    ```json
    {
      "status": "healthy",
      "timestamp": "2026-02-03T12:00:00Z",
      "checks": {
        "staleness": {
          "status": "healthy",
          "message": "Pricing data is fresh (2.3h old)",
          "hours_since_update": 2.3
        },
        "default_pricing_usage": {
          "status": "warning",
          "message": "5 models using default pricing",
          "models_using_default": 5
        },
        "provider_sync_health": {
          "status": "healthy",
          "providers": {
            "openrouter": {"status": "healthy", "hours_since_sync": 1.5},
            "featherless": {"status": "healthy", "hours_since_sync": 2.1}
          }
        }
      }
    }
    ```

    **Status Values:**
    - `healthy`: All checks passed
    - `warning`: Minor issues detected (e.g., stale data, some models using default pricing)
    - `critical`: Major issues detected (e.g., very stale data, many models using default pricing)
    - `unknown`: Health check failed

    **Relates to:** Issue #1038 - Pricing System Audit
    """
    try:
        from src.services.pricing_health_monitor import check_pricing_health

        health = check_pricing_health()

        # Update Prometheus metric
        try:
            from src.services.prometheus_metrics import pricing_health_status

            status_value = {
                "unknown": 0,
                "healthy": 1,
                "warning": 2,
                "critical": 3
            }.get(health["status"], 0)
            pricing_health_status.set(status_value)
        except (ImportError, AttributeError):
            pass

        return {
            "success": True,
            "data": health,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to check pricing health: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to check pricing health: {str(e)}"
        ) from e


@router.get("/health/pricing/staleness", tags=["health", "pricing"])
async def check_pricing_staleness():
    """
    Check if pricing data is stale.

    Returns detailed information about when pricing was last updated.

    Alerts if pricing data is:
    - **Warning**: >24 hours old
    - **Critical**: >72 hours old

    **Example Response:**
    ```json
    {
      "status": "healthy",
      "message": "Pricing data is fresh (2.3h old)",
      "last_updated": "2026-02-03T09:42:00Z",
      "hours_since_update": 2.3,
      "threshold_hours": 24,
      "critical_threshold_hours": 72
    }
    ```
    """
    try:
        from src.services.pricing_health_monitor import get_pricing_health_monitor

        monitor = get_pricing_health_monitor()
        staleness = monitor.check_pricing_staleness()

        return {
            "success": True,
            "data": staleness,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to check pricing staleness: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to check pricing staleness: {str(e)}"
        ) from e


@router.get("/health/pricing/default-usage", tags=["health", "pricing"])
async def check_default_pricing_usage():
    """
    Check how many models are using default pricing.

    Default pricing ($0.00002/token) indicates missing pricing data and
    can lead to significant under-billing or over-billing.

    Returns list of models using default pricing with usage statistics.

    **Example Response:**
    ```json
    {
      "status": "warning",
      "message": "5 models using default pricing",
      "models_using_default": 5,
      "details": {
        "anthropic/claude-3-opus": {
          "count": 42,
          "first_seen": 1706543210.5,
          "last_seen": 1706629610.5,
          "error_count": 0
        }
      }
    }
    ```
    """
    try:
        from src.services.pricing_health_monitor import get_pricing_health_monitor

        monitor = get_pricing_health_monitor()
        usage = monitor.check_default_pricing_usage()

        return {
            "success": True,
            "data": usage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to check default pricing usage: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to check default pricing usage: {str(e)}"
        ) from e
