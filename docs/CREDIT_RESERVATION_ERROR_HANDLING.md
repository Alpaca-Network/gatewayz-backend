# Credit Reservation Error Handling

## Overview

The Gatewayz API implements **specialized HTTP error handling** for insufficient credit scenarios during pre-flight checks (credit reservation). This provides users with detailed, actionable information when they don't have enough credits to cover the maximum possible cost of a request.

## Error Code

**HTTP Status**: `402 Payment Required`
**Error Code**: `INSUFFICIENT_CREDITS`
**Error Type**: `insufficient_credits`

## When This Error Occurs

This error is raised **BEFORE** making a provider request when:

1. User is not on a free trial
2. User's current credits < maximum possible cost
3. Maximum possible cost = (estimated input tokens + max_tokens) × model pricing

## Error Response Structure

### Full Response Example

```json
{
  "error": {
    "type": "insufficient_credits",
    "code": "INSUFFICIENT_CREDITS",
    "status": 402,
    "message": "Insufficient credits for this request. Maximum possible cost: $0.2000. Available balance: $0.0500. Shortfall: $0.1500.",
    "detail": "Your request to gpt-4o requires up to $0.2000 in credits (based on max_tokens=4096), but you only have $0.0500 available. You need $0.1500 more credits to proceed.",
    "request_id": "req_a1b2c3d4e5f6",
    "timestamp": "2026-01-22T10:30:00.000Z",
    "suggestions": [
      "Add $0.1500 or more in credits to your account",
      "Try setting max_tokens to 1024 or less to fit your available balance",
      "Reduce max_tokens from 4096 to lower the maximum possible cost",
      "Use a less expensive model",
      "Visit https://gatewayz.ai/pricing to add credits"
    ],
    "context": {
      "current_credits": 0.05,
      "required_credits": 0.20,
      "credit_deficit": 0.15,
      "requested_model": "gpt-4o",
      "requested_max_tokens": 4096,
      "input_tokens": 150,
      "additional_info": {
        "reason": "pre_flight_check",
        "check_type": "credit_reservation",
        "max_possible_cost": 0.20,
        "note": "This is a conservative estimate. Actual cost may be lower based on actual token usage."
      }
    },
    "docs_url": "https://docs.gatewayz.ai/pricing-and-billing/credits",
    "support_url": "https://gatewayz.ai/support"
  }
}
```

## Error Fields

### Primary Fields

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"insufficient_credits"` |
| `code` | string | Always `"INSUFFICIENT_CREDITS"` |
| `status` | integer | Always `402` |
| `message` | string | Concise summary with key amounts |
| `detail` | string | Full explanation of the situation |
| `request_id` | string | Unique request identifier |
| `timestamp` | string | ISO 8601 timestamp |

### Suggestions Array

Actionable suggestions ordered by priority:

1. **Add exact shortfall** - "Add $X.XXXX or more in credits"
2. **Calculated max_tokens** - "Try setting max_tokens to N or less" (if applicable)
3. **Reduce max_tokens** - "Reduce max_tokens from N to lower cost"
4. **Use cheaper model** - "Use a less expensive model"
5. **Add credits link** - "Visit https://gatewayz.ai/pricing to add credits"

### Context Object

Detailed context about the reservation:

```json
{
  "current_credits": 0.05,           // User's available balance
  "required_credits": 0.20,          // Maximum possible cost
  "credit_deficit": 0.15,            // Shortfall amount
  "requested_model": "gpt-4o",       // Model being requested
  "requested_max_tokens": 4096,      // Max output tokens
  "input_tokens": 150,               // Estimated input tokens
  "additional_info": {
    "reason": "pre_flight_check",    // Why check failed
    "check_type": "credit_reservation",  // Type of check
    "max_possible_cost": 0.20,       // Maximum cost
    "note": "..."                     // Explanation note
  }
}
```

## Usage Examples

### Raising the Error (Backend)

```python
from src.utils.exceptions import APIExceptions

# In your handler/route
raise APIExceptions.insufficient_credits_for_reservation(
    current_credits=user_credits,
    max_cost=0.20,
    model_id="gpt-4o",
    max_tokens=4096,
    input_tokens=150,
    request_id="req_12345"
)
```

### Handling the Error (Client)

```python
import requests

response = requests.post(
    "https://api.gatewayz.ai/v1/chat/completions",
    headers={"Authorization": "Bearer gw_live_..."},
    json={
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 4096
    }
)

if response.status_code == 402:
    error = response.json()["error"]

    # Get key information
    shortfall = error["context"]["credit_deficit"]
    current_balance = error["context"]["current_credits"]
    max_tokens = error["context"]["requested_max_tokens"]

    # Show actionable suggestions
    for suggestion in error["suggestions"]:
        print(f"💡 {suggestion}")

    # Example output:
    # 💡 Add $0.1500 or more in credits to your account
    # 💡 Try setting max_tokens to 1024 or less to fit your available balance
    # 💡 Reduce max_tokens from 4096 to lower the maximum possible cost
```

### Handling in JavaScript/TypeScript

```typescript
try {
  const response = await fetch('https://api.gatewayz.ai/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Authorization': 'Bearer gw_live_...',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: 'gpt-4o',
      messages: [{ role: 'user', content: 'Hello' }],
      max_tokens: 4096,
    }),
  });

  if (response.status === 402) {
    const errorData = await response.json();
    const { error } = errorData;

    // Display user-friendly error
    console.error(`❌ ${error.message}`);

    // Show suggestions
    console.log('Suggestions:');
    error.suggestions.forEach((s: string, i: number) => {
      console.log(`  ${i + 1}. ${s}`);
    });

    // Get specific amounts
    const {
      current_credits,
      credit_deficit,
      requested_max_tokens,
    } = error.context;

    console.log(`Current balance: $${current_credits}`);
    console.log(`Shortfall: $${credit_deficit}`);
    console.log(`Requested max_tokens: ${requested_max_tokens}`);
  }
} catch (err) {
  console.error('Request failed:', err);
}
```

## Smart Suggestions

### Calculated max_tokens Recommendation

When `max_tokens > 100`, the error includes a **calculated suggestion** for a reduced max_tokens value:

```
Formula: suggested_max_tokens = current_max_tokens × (available_credits / max_cost)
```

**Example:**
- Current credits: $0.10
- Max cost: $0.40
- Current max_tokens: 4000
- Ratio: 0.10 / 0.40 = 0.25
- **Suggested max_tokens: 4000 × 0.25 = 1000**

Suggestion output:
```
"Try setting max_tokens to 1000 or less to fit your available balance"
```

### Why This Helps

Users can **immediately retry** with the suggested value:

```bash
# Original request (failed)
curl -X POST https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer gw_live_..." \
  -d '{
    "model": "gpt-4o",
    "messages": [...],
    "max_tokens": 4000
  }'
# Error: Need $0.40, have $0.10

# Retry with suggested value (succeeds)
curl -X POST https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer gw_live_..." \
  -d '{
    "model": "gpt-4o",
    "messages": [...],
    "max_tokens": 1000  # ✅ Within budget!
  }'
```

## Comparison with Generic Insufficient Credits Error

### Generic Error (Old)

```json
{
  "detail": "Insufficient credits. Current balance: $0.0500"
}
```

**Problems:**
- ❌ No maximum cost shown
- ❌ No shortfall calculation
- ❌ No suggestions
- ❌ No context about request
- ❌ Can't determine how to fix

### Credit Reservation Error (New)

```json
{
  "error": {
    "message": "Insufficient credits for this request. Maximum possible cost: $0.2000. Available balance: $0.0500. Shortfall: $0.1500.",
    "suggestions": [
      "Add $0.1500 or more in credits to your account",
      "Try setting max_tokens to 1024 or less to fit your available balance",
      ...
    ],
    "context": {
      "current_credits": 0.05,
      "required_credits": 0.20,
      "credit_deficit": 0.15,
      ...
    }
  }
}
```

**Benefits:**
- ✅ Shows exact maximum cost
- ✅ Calculates exact shortfall
- ✅ Provides actionable suggestions
- ✅ Includes request context
- ✅ Clear path to resolution

## Integration with Credit Pre-flight Check

This error is automatically raised by the `ChatInferenceHandler` when the pre-flight credit check fails:

```python
# In src/handlers/chat_handler.py

async def _check_credit_sufficiency(self, model_id, messages, max_tokens):
    # Estimate maximum cost
    check_result = estimate_and_check_credits(
        model_id=model_id,
        messages=messages,
        user_credits=self.user.get("credits"),
        max_tokens=max_tokens,
    )

    # If insufficient, raise detailed error
    if not check_result["allowed"]:
        raise APIExceptions.insufficient_credits_for_reservation(
            current_credits=self.user.get("credits"),
            max_cost=check_result["max_cost"],
            model_id=model_id,
            max_tokens=check_result["max_output_tokens"],
            input_tokens=check_result.get("input_tokens"),
            request_id=self.request_id,
        )
```

## Testing

Comprehensive tests are available in:
```
tests/utils/test_credit_reservation_errors.py
```

Run tests:
```bash
pytest tests/utils/test_credit_reservation_errors.py -v
```

Test coverage:
- ✅ Error structure and fields
- ✅ Context metadata
- ✅ Suggestions generation
- ✅ Calculated max_tokens recommendations
- ✅ Message clarity
- ✅ Real-world scenarios
- ✅ Edge cases

## Best Practices

### For API Users

1. **Always check for 402 status** in error handling
2. **Parse the suggestions array** for actionable steps
3. **Extract shortfall amount** from context for "Add Credits" flows
4. **Show suggested max_tokens** as a quick retry option
5. **Log request_id** for support inquiries

### For Backend Developers

1. **Use the specialized error** for pre-flight checks
2. **Include input_tokens** when available for better estimates
3. **Always include request_id** for tracing
4. **Log warnings** before raising the error for monitoring
5. **Don't raise this error** for trial users (they have $0 cost)

## Error Monitoring

Monitor these errors in your observability platform:

```python
# Logged automatically by ChatInferenceHandler
logger.warning(
    f"[ChatHandler] Insufficient credits for user {user_id}: "
    f"need ${max_cost:.4f}, have ${user_credits:.4f}"
)
```

**Metrics to track:**
- `credit_reservation_failures_total` - Count of these errors
- `credit_shortfall_amount` - Distribution of shortfall amounts
- `suggested_max_tokens_usage` - How often users follow suggestions

## FAQ

### Q: Why does it show "maximum possible cost" instead of exact cost?

**A:** We calculate cost BEFORE calling the provider, so we don't know the exact output length yet. We use `max_tokens` as a conservative upper bound. The actual cost will likely be lower.

### Q: What if the user doesn't specify max_tokens?

**A:** We use the model's default maximum (e.g., 4096 for GPT-4o, 8192 for GPT-4). This ensures we always check against a realistic maximum.

### Q: Can users partially fund a request?

**A:** No. The pre-flight check requires sufficient credits for the **maximum possible** cost. This prevents situations where a request starts but can't be paid for after completion.

### Q: What about streaming requests?

**A:** Same check applies. We verify credits **before** the stream starts, preventing mid-stream payment failures.

### Q: How is this different from trial expiration?

**A:** Trial expiration (403) means the user's free trial ended. This error (402) means a paying user doesn't have enough credits for this specific request.

## Related Documentation

- [Credit Pre-flight Check System](./CREDIT_PREFLIGHT_CHECK.md)
- [Pricing and Billing](https://docs.gatewayz.ai/pricing-and-billing)
- [Error Handling Guide](https://docs.gatewayz.ai/errors)
- [API Reference](https://docs.gatewayz.ai/api-reference)

## Support

If you encounter this error unexpectedly:

1. Check your credit balance: https://gatewayz.ai/dashboard
2. Review the suggestions in the error response
3. Contact support: https://gatewayz.ai/support (include `request_id`)
