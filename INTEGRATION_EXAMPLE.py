"""
Integration Example: How to add cost tracking to chat.py

This file shows the exact code changes needed in src/routes/chat.py
to integrate the pricing calculator and cost tracking.
"""

# ============================================
# Step 1: Add imports at the top of chat.py
# ============================================

from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost
# Import the pricing calculator (already exists in project root)
import sys
from pathlib import Path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
from pricing_calculator import calculate_model_cost


# ============================================
# Step 2: Replace the existing _save_chat_completion_request function
# (Currently around line 662-758 in chat.py)
# ============================================

def _save_chat_completion_request(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    elapsed_ms: int,
    success: bool = True,
    error_message: str | None = None,
):
    """
    Save chat completion request with cost tracking

    ENHANCED VERSION: Saves detailed cost breakdown
    """
    try:
        if not hasattr(_save_chat_completion_request, "request_id"):
            # Get or create request_id (from context or generate new)
            _save_chat_completion_request.request_id = str(uuid.uuid4())

        request_id = _save_chat_completion_request.request_id

        # Get model pricing details for breakdown
        from src.services.pricing import get_model_pricing
        pricing_info = get_model_pricing(model)

        # Calculate detailed cost breakdown
        prompt_price = pricing_info.get("prompt", 0)
        completion_price = pricing_info.get("completion", 0)

        input_cost = prompt_tokens * prompt_price
        output_cost = completion_tokens * completion_price

        # Get user context (if available)
        user = getattr(_save_chat_completion_request, "user", None)
        api_key = getattr(_save_chat_completion_request, "api_key", None)

        # Save with cost tracking
        save_chat_completion_request_with_cost(
            request_id=request_id,
            model_name=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            processing_time_ms=int(elapsed_ms),
            cost_usd=cost,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            pricing_source="calculated",
            status="completed" if success else "failed",
            error_message=error_message if not success else None,
            user_id=user.get("id") if user else None,
            provider_name=provider,
            api_key_id=api_key.get("id") if api_key else None,
            is_anonymous=user is None
        )

        logger.debug(
            f"Saved chat completion request: {request_id}, "
            f"model={model}, cost=${cost:.6f}, "
            f"tokens={prompt_tokens}+{completion_tokens}"
        )

    except Exception as e:
        logger.error(f"Failed to save chat completion request: {e}", exc_info=True)
        # Don't raise - tracking should not break the main flow


# ============================================
# Step 3: Update the context in the request handlers
# (Add this where user/api_key context is available)
# ============================================

def example_chat_handler_update():
    """
    This shows how to pass context to the save function
    """
    # ... existing code to get user and api_key ...

    # Set context for the save function
    _save_chat_completion_request.user = user
    _save_chat_completion_request.api_key = api_key_obj
    _save_chat_completion_request.request_id = request_id

    # ... rest of your request handling ...

    # When you call the save function, it will use this context
    await _record_inference_metrics_and_health(
        provider=provider,
        model=model,
        # ...
    )


# ============================================
# Step 4: Alternative - Direct integration where cost is calculated
# (Around line 574 in chat.py where calculate_cost is called)
# ============================================

def alternative_integration_example():
    """
    Alternative: Calculate and save cost inline
    """
    # Old code:
    # cost = calculate_cost(model, prompt_tokens, completion_tokens)

    # New code with pricing calculator:
    from pricing_calculator import calculate_model_cost

    # Build model data structure
    model_pricing = get_model_pricing(model)
    model_data = {
        "id": model,
        "architecture": {"modality": "text->text"},
        "pricing": {
            "prompt": str(model_pricing.get("prompt", 0)),
            "completion": str(model_pricing.get("completion", 0))
        }
    }

    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens
    }

    # Calculate with full breakdown
    cost_breakdown = calculate_model_cost(provider, model_data, usage)

    cost = cost_breakdown["total_cost"]
    input_cost = cost_breakdown["prompt_cost"]
    output_cost = cost_breakdown["completion_cost"]

    # Save with detailed cost info
    save_chat_completion_request_with_cost(
        request_id=request_id,
        model_name=model,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        processing_time_ms=int(elapsed_ms),
        cost_usd=cost,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        pricing_source="pricing_calculator",
        # ... other params ...
    )


# ============================================
# Step 5: Register admin routes in main.py
# ============================================

def register_admin_routes_example():
    """
    Add this to src/main.py
    """
    from src.routes.admin_pricing_analytics import router as admin_pricing_router

    # In create_app() function:
    app.include_router(admin_pricing_router)


# ============================================
# Summary of Changes Needed
# ============================================

"""
1. Run database migration:
   supabase db push
   OR
   psql $DATABASE_URL -f supabase/migrations/20260115000001_add_cost_tracking_to_chat_completion_requests.sql

2. Add imports to chat.py:
   from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost
   from pricing_calculator import calculate_model_cost

3. Replace or update _save_chat_completion_request function to use:
   save_chat_completion_request_with_cost() instead of save_chat_completion_request()

4. Register admin routes in main.py:
   from src.routes.admin_pricing_analytics import router as admin_pricing_router
   app.include_router(admin_pricing_router)

5. Access admin analytics:
   GET /admin/pricing-analytics/summary
   GET /admin/pricing-analytics/models
   GET /admin/pricing-analytics/providers
   GET /admin/pricing-analytics/trend
   GET /admin/pricing-analytics/efficiency-report

6. Query database directly:
   SELECT * FROM model_usage_analytics ORDER BY total_cost_usd DESC LIMIT 10;

That's it! You now have full cost tracking and analytics.
"""
