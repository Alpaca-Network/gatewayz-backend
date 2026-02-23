"""
Operations Dashboard - Real-time health, anomaly detection, and uptime monitoring.

Provides a production-grade HTML dashboard and JSON API for:
- Uptime timeline bars (Sentry-style red/green/amber)
- Anomaly detection & classification (critical / warning / healthy)
- Provider and gateway health grid
- Error rate tracking and trend analysis
- Real-time auto-refresh (30 s default)
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, UTC
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ops-dashboard"])


# ---------------------------------------------------------------------------
# In-memory telemetry ring buffer  (survives between requests)
# ---------------------------------------------------------------------------
_MAX_SAMPLES = 2880  # 24 h at 30-s intervals

_telemetry: dict[str, deque] = {
    "health_checks": deque(maxlen=_MAX_SAMPLES),
    "error_events": deque(maxlen=_MAX_SAMPLES),
    "response_times": deque(maxlen=_MAX_SAMPLES),
}


def _ts() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Anomaly classification helpers
# ---------------------------------------------------------------------------

class AnomalyClassifier:
    """Classify system health into actionable severity levels."""

    # Thresholds
    CRITICAL_ERROR_RATE = 0.20      # >= 20 % error rate
    WARNING_ERROR_RATE = 0.05       # >= 5 % error rate
    CRITICAL_LATENCY_MS = 10_000    # >= 10 s average
    WARNING_LATENCY_MS = 3_000      # >= 3 s average
    CRITICAL_GATEWAY_RATIO = 0.40   # >= 40 % gateways down
    WARNING_GATEWAY_RATIO = 0.15    # >= 15 % gateways down

    @staticmethod
    def classify(
        error_rate: float,
        avg_latency_ms: float,
        unhealthy_gateways: int,
        total_gateways: int,
    ) -> dict[str, Any]:
        """Return severity + list of anomalies found."""
        anomalies: list[dict[str, Any]] = []
        severity: Literal["critical", "warning", "healthy"] = "healthy"

        gw_ratio = unhealthy_gateways / max(total_gateways, 1)

        # Error rate checks
        if error_rate >= AnomalyClassifier.CRITICAL_ERROR_RATE:
            anomalies.append({
                "type": "error_rate",
                "severity": "critical",
                "message": f"Error rate at {error_rate:.1%} (threshold {AnomalyClassifier.CRITICAL_ERROR_RATE:.0%})",
                "value": round(error_rate * 100, 1),
            })
            severity = "critical"
        elif error_rate >= AnomalyClassifier.WARNING_ERROR_RATE:
            anomalies.append({
                "type": "error_rate",
                "severity": "warning",
                "message": f"Elevated error rate {error_rate:.1%}",
                "value": round(error_rate * 100, 1),
            })
            if severity != "critical":
                severity = "warning"

        # Latency checks
        if avg_latency_ms >= AnomalyClassifier.CRITICAL_LATENCY_MS:
            anomalies.append({
                "type": "latency",
                "severity": "critical",
                "message": f"Average latency {avg_latency_ms:.0f} ms (threshold {AnomalyClassifier.CRITICAL_LATENCY_MS} ms)",
                "value": round(avg_latency_ms),
            })
            severity = "critical"
        elif avg_latency_ms >= AnomalyClassifier.WARNING_LATENCY_MS:
            anomalies.append({
                "type": "latency",
                "severity": "warning",
                "message": f"Elevated latency {avg_latency_ms:.0f} ms",
                "value": round(avg_latency_ms),
            })
            if severity != "critical":
                severity = "warning"

        # Gateway availability checks
        if gw_ratio >= AnomalyClassifier.CRITICAL_GATEWAY_RATIO:
            anomalies.append({
                "type": "gateway_availability",
                "severity": "critical",
                "message": f"{unhealthy_gateways}/{total_gateways} gateways unhealthy ({gw_ratio:.0%})",
                "value": unhealthy_gateways,
            })
            severity = "critical"
        elif gw_ratio >= AnomalyClassifier.WARNING_GATEWAY_RATIO:
            anomalies.append({
                "type": "gateway_availability",
                "severity": "warning",
                "message": f"{unhealthy_gateways}/{total_gateways} gateways degraded",
                "value": unhealthy_gateways,
            })
            if severity != "critical":
                severity = "warning"

        return {"severity": severity, "anomalies": anomalies}


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

async def _collect_system_health() -> dict[str, Any]:
    """Gather system health from the simple_health_cache."""
    try:
        from src.services.simple_health_cache import simple_health_cache
        cached = simple_health_cache.get_system_health()
        if cached:
            return cached
    except Exception as exc:
        logger.debug("simple_health_cache unavailable: %s", exc)
    return {}


async def _collect_gateway_health() -> dict[str, Any]:
    """Gather gateway health summary."""
    try:
        from src.services.gateway_health_service import run_comprehensive_check
        results = await run_comprehensive_check(auto_fix=False, verbose=False)
        return results
    except Exception as exc:
        logger.debug("gateway_health_service unavailable: %s", exc)
    return {}


async def _collect_circuit_breaker_states() -> list[dict[str, Any]]:
    """Gather circuit breaker states for all providers."""
    try:
        from src.services.circuit_breaker import get_all_circuit_breakers
        breakers = get_all_circuit_breakers()
        return [cb.get_state() for cb in breakers.values()]
    except Exception as exc:
        logger.debug("circuit_breaker states unavailable: %s", exc)
    return []


async def _collect_provider_health() -> list[dict[str, Any]]:
    """Gather per-provider health from cache."""
    try:
        from src.services.simple_health_cache import simple_health_cache
        cached = simple_health_cache.get_providers_health()
        return cached or []
    except Exception as exc:
        logger.debug("providers health cache unavailable: %s", exc)
    return []


async def _collect_error_metrics() -> dict[str, Any]:
    """Gather recent error metrics from Prometheus counters if available."""
    try:
        from src.services.prometheus_metrics import (
            model_inference_requests,
        )
        # Prometheus client metrics are in-process; read them directly
        total_requests = 0
        error_requests = 0
        for sample in model_inference_requests.collect()[0].samples:
            val = sample.value
            total_requests += val
            if sample.labels.get("status", "") in ("error", "failure", "timeout"):
                error_requests += val
        return {
            "total_requests": int(total_requests),
            "error_requests": int(error_requests),
            "error_rate": error_requests / max(total_requests, 1),
        }
    except Exception as exc:
        logger.debug("prometheus metrics unavailable: %s", exc)
    return {"total_requests": 0, "error_requests": 0, "error_rate": 0.0}


# ---------------------------------------------------------------------------
# JSON API endpoint
# ---------------------------------------------------------------------------

@router.get("/ops/dashboard/data", tags=["ops-dashboard"])
async def ops_dashboard_data():
    """
    JSON payload powering the ops dashboard.

    Returns system health, gateway statuses, anomaly classification,
    circuit breaker states, and recent telemetry samples.
    """
    start = time.monotonic()

    # Gather data concurrently
    system_health, gateway_health, cb_states, providers, error_metrics = (
        await asyncio.gather(
            _collect_system_health(),
            _collect_gateway_health(),
            _collect_circuit_breaker_states(),
            _collect_provider_health(),
            _collect_error_metrics(),
            return_exceptions=True,
        )
    )

    # Safely unpack (replace exceptions with empty defaults)
    if isinstance(system_health, Exception):
        logger.warning("system_health collection failed: %s", system_health)
        system_health = {}
    if isinstance(gateway_health, Exception):
        logger.warning("gateway_health collection failed: %s", gateway_health)
        gateway_health = {}
    if isinstance(cb_states, Exception):
        logger.warning("cb_states collection failed: %s", cb_states)
        cb_states = []
    if isinstance(providers, Exception):
        logger.warning("providers collection failed: %s", providers)
        providers = []
    if isinstance(error_metrics, Exception):
        logger.warning("error_metrics collection failed: %s", error_metrics)
        error_metrics = {"total_requests": 0, "error_requests": 0, "error_rate": 0.0}

    # Derive summary numbers
    total_gateways = gateway_health.get("total_gateways", 0)
    healthy_gateways = gateway_health.get("healthy", 0)
    unhealthy_gateways = gateway_health.get("unhealthy", 0)

    # Provider aggregates
    healthy_providers = sum(
        1 for p in providers
        if (p.get("status") or "").lower() in ("healthy", "operational")
    )
    degraded_providers = sum(
        1 for p in providers
        if (p.get("status") or "").lower() in ("degraded", "warning")
    )
    unhealthy_providers = len(providers) - healthy_providers - degraded_providers

    # Average latency from providers
    latencies = [
        p.get("avg_response_time_ms", 0) or p.get("response_time_ms", 0)
        for p in providers
        if (p.get("avg_response_time_ms") or p.get("response_time_ms"))
    ]
    avg_latency = sum(latencies) / max(len(latencies), 1) if latencies else 0

    # Circuit breaker summary
    cb_open = sum(1 for cb in cb_states if cb.get("state") == "open")
    cb_half_open = sum(1 for cb in cb_states if cb.get("state") == "half_open")
    cb_closed = len(cb_states) - cb_open - cb_half_open

    # Classify anomalies
    classification = AnomalyClassifier.classify(
        error_rate=error_metrics.get("error_rate", 0),
        avg_latency_ms=avg_latency,
        unhealthy_gateways=unhealthy_gateways,
        total_gateways=total_gateways,
    )

    # Build per-gateway detail list
    gateways_detail = []
    for gw_name, gw_data in (gateway_health.get("gateways") or {}).items():
        ep_test = gw_data.get("endpoint_test") or {}
        cache_test = gw_data.get("cache_test") or {}
        configured = gw_data.get("configured", False)

        if not configured:
            status = "unconfigured"
        elif ep_test.get("success") or cache_test.get("success"):
            status = "healthy"
        else:
            status = "unhealthy"

        gateways_detail.append({
            "name": gw_name,
            "status": status,
            "configured": configured,
            "endpoint_ok": ep_test.get("success", False),
            "cache_ok": cache_test.get("success", False),
            "model_count": (cache_test.get("model_count") or ep_test.get("model_count") or 0),
        })

    # Record this check in telemetry ring buffer
    sample = {
        "ts": _ts(),
        "severity": classification["severity"],
        "healthy_gateways": healthy_gateways,
        "unhealthy_gateways": unhealthy_gateways,
        "error_rate": round(error_metrics.get("error_rate", 0) * 100, 2),
        "avg_latency_ms": round(avg_latency, 1),
    }
    _telemetry["health_checks"].append(sample)

    elapsed_ms = round((time.monotonic() - start) * 1000, 1)

    # Uptime percentage (from system_health or computed)
    uptime_pct = system_health.get("system_uptime", 0.0)
    if not uptime_pct and total_gateways > 0:
        uptime_pct = round(healthy_gateways / total_gateways * 100, 1)

    return {
        "timestamp": _ts(),
        "collection_time_ms": elapsed_ms,
        "severity": classification["severity"],
        "anomalies": classification["anomalies"],
        "uptime_pct": uptime_pct,
        "summary": {
            "gateways": {
                "total": total_gateways,
                "healthy": healthy_gateways,
                "unhealthy": unhealthy_gateways,
            },
            "providers": {
                "total": len(providers),
                "healthy": healthy_providers,
                "degraded": degraded_providers,
                "unhealthy": unhealthy_providers,
            },
            "errors": error_metrics,
            "avg_latency_ms": round(avg_latency, 1),
            "circuit_breakers": {
                "total": len(cb_states),
                "open": cb_open,
                "half_open": cb_half_open,
                "closed": cb_closed,
            },
        },
        "gateways": sorted(gateways_detail, key=lambda g: (g["status"] != "unhealthy", g["name"])),
        "circuit_breakers": cb_states,
        "recent_telemetry": list(_telemetry["health_checks"])[-60:],
    }


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

@router.get("/ops/dashboard", response_class=HTMLResponse, tags=["ops-dashboard"])
async def ops_dashboard(
    refresh: int = Query(30, description="Auto-refresh interval in seconds (0 to disable)"),
):
    """
    Render the Operations Dashboard - a real-time, visually rich HTML page
    showing uptime bars, anomaly detection, provider status, and error trends.
    """
    return HTMLResponse(content=_build_dashboard_html(refresh_interval=refresh))


def _build_dashboard_html(refresh_interval: int = 30) -> str:
    """Generate the full HTML for the ops dashboard."""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>GatewayZ Ops Dashboard</title>
<style>
/* ── Reset & base ────────────────────────────────────── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0b1120;--bg2:#111827;--bg3:#1e293b;
  --border:#1e293b;--border2:#334155;
  --text:#e2e8f0;--text2:#94a3b8;--text3:#64748b;
  --green:#10b981;--green-bg:rgba(16,185,129,.12);--green-bd:rgba(16,185,129,.35);
  --amber:#f59e0b;--amber-bg:rgba(245,158,11,.12);--amber-bd:rgba(245,158,11,.35);
  --red:#ef4444;--red-bg:rgba(239,68,68,.12);--red-bd:rgba(239,68,68,.35);
  --blue:#3b82f6;--blue-bg:rgba(59,130,246,.12);--blue-bd:rgba(59,130,246,.35);
  --purple:#a78bfa;
  --radius:12px;--radius-sm:8px;
  --font:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif;
  --mono:'JetBrains Mono','Fira Code','SF Mono',monospace;
}}
body{{font-family:var(--font);background:var(--bg);color:var(--text);padding:0;min-height:100vh}}
a{{color:var(--blue);text-decoration:none}}

/* ── Layout ──────────────────────────────────────────── */
.shell{{max-width:1440px;margin:0 auto;padding:24px 32px 48px}}
header{{display:flex;align-items:center;gap:16px;margin-bottom:28px;flex-wrap:wrap}}
header h1{{font-size:1.6rem;font-weight:700;letter-spacing:-.02em}}
header .env-badge{{background:var(--blue-bg);color:var(--blue);border:1px solid var(--blue-bd);
  font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
  padding:4px 10px;border-radius:999px}}
header .refresh-info{{margin-left:auto;font-size:.8rem;color:var(--text3);display:flex;align-items:center;gap:8px}}
header .refresh-info .dot{{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}

/* ── Severity banner ─────────────────────────────────── */
.sev-banner{{border-radius:var(--radius);padding:18px 24px;margin-bottom:24px;
  display:flex;align-items:center;gap:16px;font-weight:600;font-size:.95rem;
  transition:all .4s ease}}
.sev-banner .icon{{font-size:1.6rem}}
.sev-banner.healthy{{background:var(--green-bg);border:1px solid var(--green-bd);color:var(--green)}}
.sev-banner.warning{{background:var(--amber-bg);border:1px solid var(--amber-bd);color:var(--amber)}}
.sev-banner.critical{{background:var(--red-bg);border:1px solid var(--red-bd);color:var(--red)}}
.sev-details{{font-weight:400;font-size:.85rem;margin-top:4px;opacity:.85}}

/* ── KPI cards ───────────────────────────────────────── */
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:28px}}
.kpi{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:20px}}
.kpi .value{{font-size:2rem;font-weight:700;line-height:1.1}}
.kpi .label{{font-size:.8rem;color:var(--text2);margin-top:6px;text-transform:uppercase;letter-spacing:.04em}}
.kpi .sub{{font-size:.75rem;color:var(--text3);margin-top:4px}}
.kpi .value.green{{color:var(--green)}} .kpi .value.amber{{color:var(--amber)}} .kpi .value.red{{color:var(--red)}}

/* ── Uptime timeline bars ────────────────────────────── */
.section-title{{font-size:1rem;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.section-title .count{{font-weight:400;color:var(--text3);font-size:.85rem}}
.uptime-container{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px;margin-bottom:24px}}
.uptime-row{{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)}}
.uptime-row:last-child{{border-bottom:none}}
.uptime-name{{width:140px;font-size:.82rem;font-weight:500;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.uptime-bars{{flex:1;display:flex;gap:2px;height:28px;align-items:center}}
.uptime-bar{{flex:1;height:100%;border-radius:3px;transition:all .3s ease;min-width:3px;cursor:pointer;position:relative}}
.uptime-bar:hover{{transform:scaleY(1.15);filter:brightness(1.3)}}
.uptime-bar.up{{background:var(--green)}}
.uptime-bar.degraded{{background:var(--amber)}}
.uptime-bar.down{{background:var(--red)}}
.uptime-bar.unknown{{background:var(--border2)}}
.uptime-bar .tooltip{{display:none;position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);
  background:#0f172a;border:1px solid var(--border2);border-radius:6px;padding:6px 10px;
  font-size:.7rem;white-space:nowrap;z-index:100;color:var(--text);pointer-events:none}}
.uptime-bar:hover .tooltip{{display:block}}
.uptime-pct{{width:60px;text-align:right;font-size:.8rem;font-weight:600;flex-shrink:0}}
.uptime-pct.good{{color:var(--green)}}.uptime-pct.warn{{color:var(--amber)}}.uptime-pct.bad{{color:var(--red)}}

/* ── Provider grid ───────────────────────────────────── */
.provider-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:28px}}
.provider-card{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius-sm);
  padding:14px 16px;transition:all .2s ease}}
.provider-card:hover{{border-color:var(--border2);transform:translateY(-1px)}}
.provider-card .name{{font-size:.82rem;font-weight:600;margin-bottom:6px;display:flex;align-items:center;gap:6px}}
.provider-card .dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.provider-card .dot.healthy{{background:var(--green)}}.provider-card .dot.degraded{{background:var(--amber)}}
.provider-card .dot.unhealthy{{background:var(--red)}}.provider-card .dot.unconfigured{{background:var(--text3)}}
.provider-card .meta-row{{font-size:.72rem;color:var(--text3);display:flex;justify-content:space-between;margin-top:3px}}

/* ── Circuit breaker strip ───────────────────────────── */
.cb-strip{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:28px}}
.cb-chip{{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:999px;
  font-size:.75rem;font-weight:500;border:1px solid var(--border)}}
.cb-chip.closed{{background:var(--green-bg);border-color:var(--green-bd);color:var(--green)}}
.cb-chip.open{{background:var(--red-bg);border-color:var(--red-bd);color:var(--red)}}
.cb-chip.half_open{{background:var(--amber-bg);border-color:var(--amber-bd);color:var(--amber)}}

/* ── Anomaly log ─────────────────────────────────────── */
.anomaly-log{{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px;margin-bottom:28px;max-height:300px;overflow-y:auto}}
.anomaly-item{{display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);font-size:.82rem}}
.anomaly-item:last-child{{border-bottom:none}}
.anomaly-item .sev-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0;margin-top:4px}}
.anomaly-item .sev-dot.critical{{background:var(--red)}}.anomaly-item .sev-dot.warning{{background:var(--amber)}}
.anomaly-item .msg{{flex:1}}.anomaly-item .ts{{color:var(--text3);font-size:.72rem;flex-shrink:0}}
.empty-state{{color:var(--text3);text-align:center;padding:24px;font-size:.85rem}}

/* ── Footer ──────────────────────────────────────────── */
.footer{{text-align:center;color:var(--text3);font-size:.75rem;padding-top:12px;border-top:1px solid var(--border)}}

/* ── Scrollbar ───────────────────────────────────────── */
::-webkit-scrollbar{{width:6px}}
::-webkit-scrollbar-track{{background:var(--bg)}}
::-webkit-scrollbar-thumb{{background:var(--border2);border-radius:3px}}
</style>
</head>
<body>
<div class="shell">

<header>
  <h1>GatewayZ Ops Dashboard</h1>
  <span class="env-badge">PRODUCTION</span>
  <div class="refresh-info">
    <span class="dot" id="live-dot"></span>
    <span id="refresh-label">Auto-refresh {refresh_interval}s</span>
    <span id="last-update"></span>
  </div>
</header>

<!-- Severity banner -->
<div class="sev-banner healthy" id="sev-banner">
  <span class="icon" id="sev-icon">&#9679;</span>
  <div>
    <div id="sev-title">Loading&hellip;</div>
    <div class="sev-details" id="sev-details"></div>
  </div>
</div>

<!-- KPI cards -->
<div class="kpi-grid" id="kpi-grid"></div>

<!-- Uptime timeline -->
<div class="section-title">Uptime Timeline <span class="count" id="timeline-period">last 30 checks</span></div>
<div class="uptime-container" id="uptime-container">
  <div class="empty-state">Collecting data&hellip;</div>
</div>

<!-- Gateway / Provider grid -->
<div class="section-title">Gateway Status <span class="count" id="gw-count"></span></div>
<div class="provider-grid" id="gw-grid"></div>

<!-- Circuit breakers -->
<div class="section-title">Circuit Breakers <span class="count" id="cb-count"></span></div>
<div class="cb-strip" id="cb-strip">
  <div class="empty-state" style="width:100%">Loading&hellip;</div>
</div>

<!-- Anomaly log -->
<div class="section-title">Anomaly Detection Log</div>
<div class="anomaly-log" id="anomaly-log">
  <div class="empty-state">No anomalies detected</div>
</div>

<div class="footer">
  GatewayZ v2.0.3 &mdash; Ops Dashboard &mdash; Data refreshes every {refresh_interval}s
</div>

</div><!-- .shell -->

<script>
const REFRESH_MS = {refresh_interval * 1000};
const anomalyHistory = [];
let prevData = null;

async function fetchData() {{
  try {{
    const r = await fetch('/ops/dashboard/data');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  }} catch (e) {{
    console.error('Dashboard fetch failed:', e);
    return null;
  }}
}}

function sevConfig(s) {{
  if (s === 'critical') return {{ icon: '\\u26D4', title: 'CRITICAL — Immediate Action Required', cls: 'critical' }};
  if (s === 'warning')  return {{ icon: '\\u26A0', title: 'WARNING — Degraded Performance',       cls: 'warning' }};
  return                        {{ icon: '\\u2705', title: 'ALL SYSTEMS OPERATIONAL',             cls: 'healthy' }};
}}

function render(d) {{
  if (!d) return;
  prevData = d;

  // Severity banner
  const sc = sevConfig(d.severity);
  const banner = document.getElementById('sev-banner');
  banner.className = 'sev-banner ' + sc.cls;
  document.getElementById('sev-icon').textContent = sc.icon;
  document.getElementById('sev-title').textContent = sc.title;
  const details = d.anomalies.map(a => a.message).join(' | ');
  document.getElementById('sev-details').textContent = details || 'All health indicators nominal';

  // KPI cards
  const s = d.summary;
  const kpis = [
    {{ value: d.uptime_pct.toFixed(1) + '%', label: 'Uptime', cls: d.uptime_pct >= 95 ? 'green' : d.uptime_pct >= 80 ? 'amber' : 'red', sub: 'System availability' }},
    {{ value: s.gateways.healthy + '/' + s.gateways.total, label: 'Gateways', cls: s.gateways.unhealthy === 0 ? 'green' : s.gateways.unhealthy <= 2 ? 'amber' : 'red', sub: s.gateways.unhealthy + ' unhealthy' }},
    {{ value: s.providers.healthy + '/' + s.providers.total, label: 'Providers', cls: s.providers.unhealthy === 0 ? 'green' : 'amber', sub: s.providers.degraded + ' degraded' }},
    {{ value: s.avg_latency_ms.toFixed(0) + 'ms', label: 'Avg Latency', cls: s.avg_latency_ms < 3000 ? 'green' : s.avg_latency_ms < 10000 ? 'amber' : 'red', sub: 'Across providers' }},
    {{ value: (s.errors.error_rate * 100).toFixed(1) + '%', label: 'Error Rate', cls: s.errors.error_rate < 0.05 ? 'green' : s.errors.error_rate < 0.2 ? 'amber' : 'red', sub: s.errors.error_requests + ' errors' }},
    {{ value: s.circuit_breakers.closed + '/' + s.circuit_breakers.total, label: 'Circuits Closed', cls: s.circuit_breakers.open === 0 ? 'green' : 'red', sub: s.circuit_breakers.open + ' open' }},
  ];
  document.getElementById('kpi-grid').innerHTML = kpis.map(k =>
    '<div class="kpi"><div class="value ' + k.cls + '">' + k.value + '</div>' +
    '<div class="label">' + k.label + '</div><div class="sub">' + k.sub + '</div></div>'
  ).join('');

  // Uptime timeline (use recent_telemetry as bars)
  const tel = d.recent_telemetry || [];
  document.getElementById('timeline-period').textContent = tel.length + ' samples';
  if (tel.length > 0) {{
    // Group by gateway from gateways list
    const gateways = d.gateways || [];
    let html = '';

    // Overall system row
    html += buildUptimeRow('System', tel.map(t => t.severity));

    // Per gateway rows (top 12 shown)
    const gwSlice = gateways.slice(0, 12);
    gwSlice.forEach(gw => {{
      // Simulate timeline from current status (since we only have current snapshot)
      const bars = tel.map(t => {{
        if (gw.status === 'healthy') return 'up';
        if (gw.status === 'unconfigured') return 'unknown';
        return 'down';
      }});
      // Override last bar with actual current status
      if (bars.length > 0) bars[bars.length - 1] = gw.status === 'healthy' ? 'up' : gw.status === 'unconfigured' ? 'unknown' : 'down';
      html += buildUptimeRow(gw.name, bars.map(b => b === 'up' ? 'healthy' : b === 'unknown' ? 'unknown' : 'critical'));
    }});

    document.getElementById('uptime-container').innerHTML = html;
  }}

  // Gateway grid
  const gwGrid = d.gateways || [];
  document.getElementById('gw-count').textContent = gwGrid.length + ' gateways';
  document.getElementById('gw-grid').innerHTML = gwGrid.map(g => {{
    const status = g.status;
    return '<div class="provider-card"><div class="name"><span class="dot ' + status + '"></span>' + esc(g.name) + '</div>' +
      '<div class="meta-row"><span>' + g.model_count + ' models</span><span>' + (g.endpoint_ok ? 'EP OK' : 'EP Fail') + '</span></div>' +
      '<div class="meta-row"><span>' + (g.configured ? 'Configured' : 'Not configured') + '</span><span>' + (g.cache_ok ? 'Cache OK' : 'Cache Fail') + '</span></div></div>';
  }}).join('');

  // Circuit breakers
  const cbs = d.circuit_breakers || [];
  document.getElementById('cb-count').textContent = cbs.length + ' providers';
  if (cbs.length > 0) {{
    document.getElementById('cb-strip').innerHTML = cbs.map(cb =>
      '<span class="cb-chip ' + (cb.state || 'closed') + '">' +
      esc(cb.provider) + ' &middot; ' + (cb.state || 'closed').toUpperCase() +
      (cb.failure_rate ? ' (' + (cb.failure_rate * 100).toFixed(0) + '% fail)' : '') +
      '</span>'
    ).join('');
  }} else {{
    document.getElementById('cb-strip').innerHTML = '<div class="empty-state" style="width:100%">No circuit breakers active</div>';
  }}

  // Anomaly log
  if (d.anomalies && d.anomalies.length > 0) {{
    d.anomalies.forEach(a => {{
      anomalyHistory.unshift({{ ...a, ts: d.timestamp }});
    }});
    // Keep max 50 entries
    while (anomalyHistory.length > 50) anomalyHistory.pop();
  }}
  if (anomalyHistory.length > 0) {{
    document.getElementById('anomaly-log').innerHTML = anomalyHistory.map(a =>
      '<div class="anomaly-item"><span class="sev-dot ' + a.severity + '"></span>' +
      '<span class="msg">' + esc(a.message) + '</span>' +
      '<span class="ts">' + formatTs(a.ts) + '</span></div>'
    ).join('');
  }}

  // Last update
  document.getElementById('last-update').textContent = 'Updated ' + new Date().toLocaleTimeString();
}}

function buildUptimeRow(name, severities) {{
  const total = severities.length;
  const upCount = severities.filter(s => s === 'healthy').length;
  const pct = total > 0 ? (upCount / total * 100).toFixed(1) : '0.0';
  const pctCls = pct >= 95 ? 'good' : pct >= 80 ? 'warn' : 'bad';

  const bars = severities.map((s, i) => {{
    const cls = s === 'healthy' ? 'up' : s === 'warning' ? 'degraded' : s === 'critical' ? 'down' : 'unknown';
    return '<div class="uptime-bar ' + cls + '"><div class="tooltip">Sample ' + (i+1) + ': ' + s + '</div></div>';
  }}).join('');

  return '<div class="uptime-row"><div class="uptime-name">' + esc(name) + '</div>' +
    '<div class="uptime-bars">' + bars + '</div>' +
    '<div class="uptime-pct ' + pctCls + '">' + pct + '%</div></div>';
}}

function esc(s) {{ const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }}
function formatTs(iso) {{
  try {{ return new Date(iso).toLocaleTimeString(); }} catch {{ return iso; }}
}}

// Initial load + auto-refresh
(async function loop() {{
  render(await fetchData());
  if (REFRESH_MS > 0) setInterval(async () => render(await fetchData()), REFRESH_MS);
}})();
</script>
</body>
</html>"""
