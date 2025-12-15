"""
Pricing Audit Routes

Endpoints for viewing pricing audit dashboards, reports, and anomalies.

Endpoints:
- GET /pricing/audit/report - Get audit report for specified period
- GET /pricing/audit/anomalies - Get detected pricing anomalies
- GET /pricing/audit/model/{model_id} - Get model pricing history
- GET /pricing/audit/gateway/{gateway} - Get gateway pricing history
- GET /pricing/audit/comparisons/{model_id} - Compare model across gateways
- GET /pricing/audit/cost-impact/{model_id} - Calculate cost impact
- POST /pricing/audit/snapshot - Record pricing snapshot
- GET /pricing/audit/export - Export audit data
- GET /pricing/audit/providers - Audit provider APIs
- GET /pricing/audit/providers/{provider_name} - Audit specific provider
"""

import logging

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks

from src.services.pricing_audit_service import get_pricing_audit_service
from src.services.pricing_lookup import load_manual_pricing
from src.services.pricing_provider_auditor import (
    PricingProviderAuditor,
    run_provider_audit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pricing/audit", tags=["pricing-audit"])


@router.get("/report")
async def get_audit_report(
    days: int = Query(default=30, ge=1, le=365),
    api_key: str = Query(None),
):
    """
    Get comprehensive audit report for specified period.

    Args:
        days: Number of days to include (1-365, default 30)
        api_key: Optional API key for authentication

    Returns:
        Audit report with summary, statistics, anomalies, and recommendations
    """
    try:
        audit_service = get_pricing_audit_service()
        report = audit_service.generate_audit_report(days=days)
        return report
    except Exception as e:
        logger.error(f"Error generating audit report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/anomalies")
async def get_pricing_anomalies(
    threshold: float = Query(default=50.0, ge=0, le=1000),
    severity: str | None = Query(None),
    api_key: str = Query(None),
):
    """
    Get detected pricing anomalies (large variances for same model).

    Args:
        threshold: Variance percentage threshold (default 50%)
        severity: Filter by severity (critical, major, moderate, minor)
        api_key: Optional API key

    Returns:
        List of detected pricing anomalies
    """
    try:
        audit_service = get_pricing_audit_service()
        anomalies = audit_service.find_pricing_anomalies(variance_threshold_pct=threshold)

        # Format response
        result = []
        for model_id, comparisons in anomalies.items():
            for comparison in comparisons:
                if severity is None or comparison.variance_severity == severity:
                    result.append(
                        {
                            "model_id": model_id,
                            "gateway_a": comparison.gateway_a,
                            "gateway_b": comparison.gateway_b,
                            "prompt_price_a": comparison.prompt_difference,
                            "prompt_variance_pct": round(
                                comparison.prompt_variance_pct, 2
                            ),
                            "completion_variance_pct": round(
                                comparison.completion_variance_pct, 2
                            ),
                            "severity": comparison.variance_severity,
                        }
                    )

        return {
            "threshold_pct": threshold,
            "severity_filter": severity,
            "anomaly_count": len(result),
            "anomalies": sorted(
                result, key=lambda x: x["prompt_variance_pct"], reverse=True
            ),
        }
    except Exception as e:
        logger.error(f"Error retrieving anomalies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/model/{model_id}")
async def get_model_pricing_history(
    model_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    api_key: str = Query(None),
):
    """
    Get pricing history for a specific model.

    Args:
        model_id: Model identifier
        limit: Maximum records to return
        api_key: Optional API key

    Returns:
        Pricing history with all versions
    """
    try:
        audit_service = get_pricing_audit_service()
        history = audit_service.get_pricing_history(model_id=model_id)

        # Sort by timestamp descending, limit results
        history.sort(key=lambda x: x.timestamp, reverse=True)
        history = history[:limit]

        return {
            "model_id": model_id,
            "record_count": len(history),
            "history": [
                {
                    "timestamp": record.timestamp,
                    "gateway": record.gateway,
                    "prompt": round(record.prompt_price, 6),
                    "completion": round(record.completion_price, 6),
                    "request": round(record.request_price, 6),
                    "image": round(record.image_price, 6),
                    "context_length": record.context_length,
                    "source": record.source,
                }
                for record in history
            ],
        }
    except Exception as e:
        logger.error(f"Error retrieving model history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/gateway/{gateway}")
async def get_gateway_pricing_history(
    gateway: str,
    limit: int = Query(default=100, ge=1, le=1000),
    api_key: str = Query(None),
):
    """
    Get pricing history for all models on a specific gateway.

    Args:
        gateway: Gateway name
        limit: Maximum records to return
        api_key: Optional API key

    Returns:
        Pricing history for gateway
    """
    try:
        audit_service = get_pricing_audit_service()
        history = audit_service.get_pricing_history(gateway=gateway)

        # Sort by timestamp descending, limit results
        history.sort(key=lambda x: x.timestamp, reverse=True)
        history = history[:limit]

        # Group by model
        models_dict = {}
        for record in history:
            if record.model_id not in models_dict:
                models_dict[record.model_id] = []
            models_dict[record.model_id].append(
                {
                    "timestamp": record.timestamp,
                    "prompt": round(record.prompt_price, 6),
                    "completion": round(record.completion_price, 6),
                    "request": round(record.request_price, 6),
                    "image": round(record.image_price, 6),
                }
            )

        return {
            "gateway": gateway,
            "model_count": len(models_dict),
            "record_count": len(history),
            "models": models_dict,
        }
    except Exception as e:
        logger.error(f"Error retrieving gateway history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/comparisons/{model_id}")
async def compare_model_pricing(
    model_id: str,
    threshold: float = Query(default=10.0, ge=0, le=1000),
    api_key: str = Query(None),
):
    """
    Compare pricing for a model across all available gateways.

    Args:
        model_id: Model to compare
        threshold: Variance threshold for anomaly detection
        api_key: Optional API key

    Returns:
        Cross-gateway pricing comparison
    """
    try:
        audit_service = get_pricing_audit_service()
        comparisons = audit_service.compare_gateway_pricing(
            model_id, variance_threshold_pct=threshold
        )

        if not comparisons:
            return {
                "model_id": model_id,
                "comparison_count": 0,
                "message": "Model not found or only available on single gateway",
                "comparisons": [],
            }

        # Format response
        formatted = []
        for comp in comparisons:
            formatted.append(
                {
                    "gateway_a": comp.gateway_a,
                    "gateway_b": comp.gateway_b,
                    "prompt_variance_pct": round(comp.prompt_variance_pct, 2),
                    "completion_variance_pct": round(comp.completion_variance_pct, 2),
                    "prompt_difference": round(comp.prompt_difference, 6),
                    "completion_difference": round(comp.completion_difference, 6),
                    "is_anomaly": comp.is_anomaly,
                    "severity": comp.variance_severity,
                }
            )

        # Calculate best/worst
        sorted_by_variance = sorted(
            formatted, key=lambda x: x["prompt_variance_pct"], reverse=True
        )

        return {
            "model_id": model_id,
            "comparison_count": len(formatted),
            "threshold_pct": threshold,
            "worst_variance_pct": (
                sorted_by_variance[0]["prompt_variance_pct"]
                if sorted_by_variance
                else 0
            ),
            "comparisons": sorted_by_variance,
        }
    except Exception as e:
        logger.error(f"Error comparing pricing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cost-impact/{model_id}")
async def get_cost_impact(
    model_id: str,
    monthly_tokens: int = Query(default=1_000_000_000, ge=1),
    api_key: str = Query(None),
):
    """
    Calculate cost impact of pricing differences.

    Args:
        model_id: Model to analyze
        monthly_tokens: Monthly token volume for calculation
        api_key: Optional API key

    Returns:
        Cost impact analysis with savings opportunities
    """
    try:
        audit_service = get_pricing_audit_service()
        analysis = audit_service.get_cost_impact_analysis(
            model_id, monthly_tokens=monthly_tokens
        )

        if "error" in analysis:
            raise HTTPException(status_code=404, detail=analysis["error"])

        return analysis
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error calculating cost impact: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/snapshot")
async def record_pricing_snapshot(api_key: str = Query(None)):
    """
    Record current pricing snapshot for historical tracking.

    Args:
        api_key: Optional API key for admin authentication

    Returns:
        Snapshot metadata and filename
    """
    try:
        # Load current pricing
        pricing_data = load_manual_pricing()

        if not pricing_data:
            raise HTTPException(
                status_code=404, detail="No pricing data available"
            )

        # Record snapshot
        audit_service = get_pricing_audit_service()
        snapshot_file = audit_service.record_pricing_snapshot(pricing_data)
        audit_service.record_all_pricing(pricing_data)

        return {
            "status": "success",
            "snapshot_file": snapshot_file,
            "gateway_count": len(pricing_data) - 1,
            "model_count": sum(
                len(models) for gw, models in pricing_data.items() if gw != "_metadata"
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording snapshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
async def export_audit_data(
    format: str = Query(default="json", regex="^(json|csv)$"),
    api_key: str = Query(None),
):
    """
    Export all audit data in specified format.

    Args:
        format: Export format (json or csv)
        api_key: Optional API key

    Returns:
        Exported audit data
    """
    try:
        audit_service = get_pricing_audit_service()
        data = audit_service.export_audit_data(format=format)

        return {
            "format": format,
            "data_preview": data[:500] if len(data) > 500 else data,
            "total_size_bytes": len(data),
        }
    except Exception as e:
        logger.error(f"Error exporting data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
async def get_dashboard_data(
    days: int = Query(default=30, ge=1, le=365),
    api_key: str = Query(None),
):
    """
    Get complete dashboard data with all relevant metrics and alerts.

    Args:
        days: Period for analysis
        api_key: Optional API key

    Returns:
        Complete dashboard data with overview, anomalies, and recommendations
    """
    try:
        audit_service = get_pricing_audit_service()

        # Get audit report
        audit_report = audit_service.generate_audit_report(days=days)

        # Get anomalies
        anomalies = audit_service.find_pricing_anomalies(variance_threshold_pct=50)

        # Format anomalies for display
        formatted_anomalies = []
        for model_id, comparisons in anomalies.items():
            for comp in comparisons:
                formatted_anomalies.append(
                    {
                        "model_id": model_id,
                        "gateways": f"{comp.gateway_a} vs {comp.gateway_b}",
                        "variance_pct": round(comp.prompt_variance_pct, 1),
                        "severity": comp.variance_severity,
                    }
                )

        # Sort by variance
        formatted_anomalies.sort(key=lambda x: x["variance_pct"], reverse=True)

        return {
            "dashboard_version": "1.0",
            "period_days": days,
            "report_summary": audit_report["summary"],
            "top_anomalies": formatted_anomalies[:10],
            "recommendations": audit_report["recommendations"],
            "last_updated": audit_report["generated_at"],
        }
    except Exception as e:
        logger.error(f"Error generating dashboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers")
async def audit_provider_apis(
    background_tasks: BackgroundTasks = None,
    api_key: str = Query(None),
):
    """
    Audit all provider APIs and compare pricing against stored data.

    This endpoint triggers an async audit of provider APIs and returns the report.
    Can take 30-60 seconds depending on provider responsiveness.

    Args:
        background_tasks: FastAPI background tasks
        api_key: Optional API key

    Returns:
        Provider audit report with discrepancies and recommendations
    """
    try:
        logger.info("Starting provider API audit...")
        report = await run_provider_audit()

        return {
            "audit_type": "provider_api_audit",
            "status": "complete",
            "generated_at": report["generated_at"],
            "summary": report["summary"],
            "top_discrepancies": report.get("top_discrepancies", [])[:5],
            "recommendations": report.get("recommendations", []),
            "full_report_available": True,
        }
    except Exception as e:
        logger.error(f"Error auditing provider APIs: {e}")
        raise HTTPException(status_code=500, detail=f"Provider audit failed: {str(e)}")


@router.get("/providers/{provider_name}")
async def audit_specific_provider(
    provider_name: str,
    api_key: str = Query(None),
):
    """
    Audit a specific provider's pricing API.

    Args:
        provider_name: Provider to audit (deepinfra, featherless, near, alibaba-cloud, openrouter)
        api_key: Optional API key

    Returns:
        Audit results for specific provider
    """
    try:
        auditor = PricingProviderAuditor()
        manual_pricing = load_manual_pricing()

        # Map provider names to audit methods
        audit_methods = {
            "deepinfra": auditor.audit_deepinfra,
            "featherless": auditor.audit_featherless,
            "near": auditor.audit_nearai,
            "nearai": auditor.audit_nearai,
            "alibaba-cloud": auditor.audit_alibaba_cloud,
            "alibaba": auditor.audit_alibaba_cloud,
            "openrouter": auditor.audit_openrouter,
        }

        provider_lower = provider_name.lower()
        if provider_lower not in audit_methods:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider. Supported: {', '.join(audit_methods.keys())}",
            )

        # Run audit for this provider
        audit_result = await audit_methods[provider_lower]()

        # Compare with manual pricing
        discrepancies = auditor.compare_with_manual_pricing(audit_result, manual_pricing)

        return {
            "provider": provider_name,
            "audit_status": audit_result.status,
            "fetched_at": audit_result.fetched_at,
            "api_model_count": len(audit_result.models),
            "stored_model_count": len(manual_pricing.get(provider_lower, {})),
            "discrepancy_count": len(discrepancies),
            "discrepancies": [
                {
                    "model_id": d.model_id,
                    "field": d.field,
                    "stored_price": round(d.stored_price, 6),
                    "api_price": round(d.api_price, 6),
                    "difference_pct": round(d.difference_pct, 2),
                    "severity": d.impact_severity,
                }
                for d in sorted(
                    discrepancies, key=lambda x: x.difference_pct, reverse=True
                )
            ][:10],
            "error": audit_result.error_message if audit_result.status == "error" else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error auditing provider {provider_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
