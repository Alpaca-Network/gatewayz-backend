# Quick Reference: Credit Reservation Errors

## 🚀 Quick Start

### Raising the Error (Backend)

```python
from src.utils.exceptions import APIExceptions

raise APIExceptions.insufficient_credits_for_reservation(
    current_credits=user_credits,
    max_cost=max_cost,
    model_id=model_id,
    max_tokens=max_tokens,
    input_tokens=input_tokens,  # Optional
    request_id=request_id,  # Optional
)
```

### Handling the Error (Client - Python)

```python
if response.status_code == 402:
    error = response.json()["error"]

    # Get key info
    shortfall = error["context"]["credit_deficit"]
    suggestions = error["suggestions"]

    # Show to user
    print(f"❌ {error['message']}")
    for i, s in enumerate(suggestions, 1):
        print(f"{i}. {s}")
```

### Handling the Error (Client - JavaScript)

```javascript
if (response.status === 402) {
  const { error } = await response.json();

  // Get key info
  const { credit_deficit, requested_max_tokens } = error.context;

  // Find calculated suggestion
  const suggestion = error.suggestions.find(s =>
    s.includes('max_tokens to')
  );

  // Show to user
  alert(error.message);
  console.log('Suggestions:', error.suggestions);
}
```

---

## 📋 Error Structure

```json
{
  "error": {
    "type": "insufficient_credits",
    "code": "INSUFFICIENT_CREDITS",
    "status": 402,
    "message": "Insufficient credits for this request...",
    "detail": "Your request to {model} requires...",
    "suggestions": [
      "Add ${shortfall} or more in credits",
      "Try setting max_tokens to {N} or less",
      "Reduce max_tokens from {current}",
      "Use a less expensive model",
      "Visit https://gatewayz.ai/pricing"
    ],
    "context": {
      "current_credits": 0.05,
      "required_credits": 0.20,
      "credit_deficit": 0.15,
      "requested_model": "gpt-4o",
      "requested_max_tokens": 4096,
      "input_tokens": 150
    },
    "request_id": "req_abc123",
    "timestamp": "2026-01-22T10:30:00.000Z",
    "docs_url": "https://docs.gatewayz.ai/...",
    "support_url": "https://gatewayz.ai/support"
  }
}
```

---

## 🎯 Key Fields

| Field | Location | Description |
|-------|----------|-------------|
| **status** | `error.status` | Always `402` |
| **message** | `error.message` | Concise summary |
| **shortfall** | `error.context.credit_deficit` | Amount needed |
| **current balance** | `error.context.current_credits` | User's credits |
| **max cost** | `error.context.required_credits` | Maximum cost |
| **suggestions** | `error.suggestions[]` | Actionable steps |
| **request_id** | `error.request_id` | For support |

---

## 💡 Smart Suggestions

### 1. Exact Shortfall
```
"Add $0.1500 or more in credits to your account"
```

### 2. Calculated max_tokens
```
"Try setting max_tokens to 1024 or less to fit your available balance"
```

Formula: `suggested = current_max_tokens × (available / max_cost)`

### 3. Reduce max_tokens
```
"Reduce max_tokens from 4096 to lower the maximum possible cost"
```

### 4. Use Cheaper Model
```
"Use a less expensive model"
```

### 5. Add Credits Link
```
"Visit https://gatewayz.ai/pricing to add credits"
```

---

## 🔄 Example Retry Flow

### Step 1: Initial Request Fails

```bash
curl -X POST https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer gw_live_..." \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 4096
  }'
```

**Response: 402**
```json
{
  "error": {
    "message": "Insufficient credits. Maximum possible cost: $0.2000. Available: $0.0500. Shortfall: $0.1500.",
    "suggestions": [
      "Add $0.1500 or more in credits",
      "Try setting max_tokens to 1024 or less",
      ...
    ]
  }
}
```

### Step 2: Retry with Suggested max_tokens

```bash
curl -X POST https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer gw_live_..." \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 1024  # ← Reduced as suggested
  }'
```

**Response: 200 ✅**
```json
{
  "choices": [{
    "message": {"role": "assistant", "content": "Hello! How can I help..."},
    ...
  }],
  "usage": {
    "total_tokens": 150,
    "prompt_tokens": 10,
    "completion_tokens": 140
  }
}
```

**Actual Cost: $0.03** (Much less than max $0.05!)

---

## 🧪 Testing

```bash
# Run all credit reservation error tests
pytest tests/utils/test_credit_reservation_errors.py -v

# Run specific test
pytest tests/utils/test_credit_reservation_errors.py::TestInsufficientCreditsForReservation::test_basic_error_creation -v

# With coverage
pytest tests/utils/test_credit_reservation_errors.py --cov=src/utils --cov-report=html
```

---

## 📊 Monitoring

### Logs

```
[ChatHandler] Insufficient credits for user 12345:
  need $0.2000, have $0.0500
  request_id=req_abc123
  model=gpt-4o
  max_tokens=4096
```

### Metrics

```python
# Track error rate
credit_reservation_failures_total{model="gpt-4o"} 142

# Track shortfall distribution
credit_shortfall_bucket{le="0.1"} 45
credit_shortfall_bucket{le="1.0"} 123
```

---

## 🔍 Debugging Checklist

- [ ] Check `error.context.current_credits` - User's actual balance
- [ ] Check `error.context.required_credits` - Maximum possible cost
- [ ] Check `error.context.requested_max_tokens` - What was requested
- [ ] Check `error.request_id` - For log correlation
- [ ] Check `error.suggestions` - What to tell user
- [ ] Verify user is not on trial (trial users shouldn't see this)
- [ ] Check pricing for the model (might be incorrectly high)

---

## ❓ Common Questions

**Q: Why "maximum possible cost" instead of exact cost?**
A: We check BEFORE calling the provider, so we don't know actual output length yet.

**Q: Will users be charged the maximum cost?**
A: No! They're charged actual cost after completion. Max is just for the pre-check.

**Q: What if user doesn't specify max_tokens?**
A: We use model's default (e.g., 4096 for GPT-4o).

**Q: Can the suggestion be wrong?**
A: It's an estimate. Actual cost depends on real output length, which varies.

---

## 📚 Related Docs

- [Full Error Handling Guide](./CREDIT_RESERVATION_ERROR_HANDLING.md)
- [Credit Pre-flight Check System](./CREDIT_PREFLIGHT_CHECK.md)
- [Implementation Summary](./CREDIT_ERROR_HANDLING_IMPLEMENTATION.md)

---

## 🆘 Need Help?

1. **Check logs** for request_id
2. **Review context** in error response
3. **Check pricing** for the model
4. **Contact support** with request_id
