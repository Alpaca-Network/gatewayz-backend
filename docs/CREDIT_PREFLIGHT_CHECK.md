# Credit Pre-Flight Check System

## Overview

The Gatewayz API now implements a **pre-flight credit check** system that verifies users have sufficient credits **BEFORE** making expensive provider requests. This follows OpenAI's model of using `max_tokens` to calculate the maximum possible cost.

## Problem Solved

### Before (Risky Behavior)
```
1. User has $0.01 in credits
2. ✅ Simple check: credits > 0 (passes)
3. ✅ Request sent to GPT-4 (costs $10.00)
4. ✅ Provider generates response
5. ❌ Credit deduction fails ($0.01 < $10.00)

Result: Gatewayz pays $10.00, user charged $0.00 → NET LOSS: -$10.00
```

### After (Safe Behavior)
```
1. User has $0.01 in credits
2. 📊 Estimate maximum cost using max_tokens
3. ❌ Pre-check fails ($0.01 < $10.00)
4. ⛔ Request blocked BEFORE calling provider
5. 👍 User gets clear error message

Result: Zero cost to Gatewayz, user knows exactly what's needed
```

## How It Works

### Step 1: User Specifies max_tokens

Following OpenAI's model, users can specify the maximum number of output tokens:

```json
{
  "model": "gpt-4o",
  "messages": [...],
  "max_tokens": 500
}
```

- Model will generate **up to 500 tokens**
- May generate fewer
- Will **never exceed** that limit

### Step 2: System Calculates Maximum Possible Cost

```python
# 1. Estimate input tokens from messages
input_tokens = estimate_message_tokens(messages)  # e.g., 100 tokens

# 2. Get max output tokens (from request or model default)
max_output_tokens = max_tokens or get_model_max_tokens(model)  # e.g., 500 tokens

# 3. Calculate MAXIMUM possible cost
max_cost = calculate_cost(model, input_tokens, max_output_tokens)  # e.g., $0.05
```

### Step 3: Verify Sufficient Credits

```python
if user_credits >= max_cost:
    # ✅ Allow request to proceed
    proceed_to_provider()
else:
    # ❌ Reject BEFORE calling provider
    raise HTTP 402 Payment Required {
        "max_cost": 0.05,
        "available_credits": 0.01,
        "shortfall": 0.04,
        "suggestion": "Add $0.04 in credits or reduce max_tokens from 500"
    }
```

### Step 4: Charge Actual Cost After Response

```python
# After getting response from provider
actual_tokens = response.usage.completion_tokens  # e.g., 350 tokens (not 500)
actual_cost = calculate_cost(model, input_tokens, actual_tokens)  # e.g., $0.035

# Charge actual cost (usually less than max)
deduct_credits(user, actual_cost)  # User charged $0.035, not $0.05
```

## Default max_tokens Values

When users don't specify `max_tokens`, the system uses model-specific defaults:

| Model | Default max_tokens |
|-------|-------------------|
| GPT-4 | 8192 |
| GPT-4 Turbo | 4096 |
| GPT-4o | 4096 |
| GPT-4o Mini | 16384 |
| GPT-3.5 Turbo | 4096 |
| Claude 3 Opus | 4096 |
| Claude 3 Sonnet | 4096 |
| Claude 3 Haiku | 4096 |
| Claude 3.5 Sonnet | 8192 |
| Claude Sonnet 4 | 8192 |
| Llama 3.1 | 128000 |
| Llama 3.2 | 128000 |
| Mistral | 8192 |
| Mixtral | 32768 |
| **Unknown models** | 4096 |

## API Error Response

When a user has insufficient credits, they receive a detailed error:

```json
{
  "error": {
    "message": "Insufficient credits. Maximum possible cost: $0.0500. Available: $0.0100. Shortfall: $0.0400. Tip: Reduce max_tokens from 500 to lower the cost.",
    "type": "insufficient_credits",
    "code": "payment_required"
  },
  "max_cost": 0.05,
  "available_credits": 0.01,
  "shortfall": 0.04,
  "max_tokens": 500
}
```

## Implementation Details

### Core Components

1. **`src/services/credit_precheck.py`**
   - `estimate_and_check_credits()` - Main entry point
   - `calculate_maximum_cost()` - Cost calculation
   - `get_model_max_tokens()` - Model defaults
   - `check_credit_sufficiency()` - Sufficiency verification

2. **`src/handlers/chat_handler.py`**
   - `_check_credit_sufficiency()` - Handler method
   - Integrated into `process()` (non-streaming)
   - Integrated into `process_stream()` (streaming)

3. **`src/utils/token_estimator.py`**
   - `estimate_message_tokens()` - Input token estimation

### Request Flow

```
┌─────────────────────────────────────────────────────────┐
│              User Request                               │
│  { model: "gpt-4o", messages: [...], max_tokens: 500 } │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│         Step 1: Authenticate & Load User                │
│         user = get_user(api_key)                        │
│         credits = user.credits                          │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│         Step 2: PRE-FLIGHT CREDIT CHECK ✨              │
│                                                          │
│   input_tokens = estimate_message_tokens(messages)      │
│   max_output = max_tokens or model_default              │
│   max_cost = calculate_cost(model, input, max_output)   │
│                                                          │
│   IF credits < max_cost:                                │
│       ❌ REJECT (HTTP 402)                              │
│   ELSE:                                                 │
│       ✅ PROCEED                                        │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│         Step 3: Call Provider                           │
│         response = provider.chat.completions.create()   │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│         Step 4: Charge Actual Cost                      │
│                                                          │
│   actual_tokens = response.usage.completion_tokens      │
│   actual_cost = calculate_cost(model, input, actual)    │
│   deduct_credits(user, actual_cost)                     │
└──────────────────┬──────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│         Return Response to User                         │
└─────────────────────────────────────────────────────────┘
```

## Edge Cases Handled

### 1. User doesn't specify max_tokens
```python
# Use model's default maximum
max_output_tokens = get_model_max_tokens(model) or 4096
```

### 2. User specifies max_tokens = 0
```python
# Treat as not specified, use model default
if max_tokens is None or max_tokens <= 0:
    max_output_tokens = get_model_max_tokens(model)
```

### 3. Trial users
```python
# Trial users bypass credit checks entirely
if is_trial:
    return {"allowed": True, "max_cost": 0.0}
```

### 4. Streaming requests
```python
# Same pre-flight check for streaming
# Check happens BEFORE stream starts
await self._check_credit_sufficiency(model, messages, max_tokens)
```

### 5. User has exactly enough credits
```python
# Equal is sufficient (>=, not >)
if user_credits >= max_cost:
    return {"allowed": True}
```

## Benefits

### 1. Revenue Protection
- ✅ No uncollectable charges
- ✅ No revenue loss from failed deductions
- ✅ Protects against abuse

### 2. Better User Experience
- ✅ Fail fast (no wasted waiting for expensive generation)
- ✅ Clear error messages with exact amounts needed
- ✅ Actionable suggestions (reduce max_tokens or add credits)

### 3. Cost Control for Users
- ✅ Users can control costs via max_tokens
- ✅ Predictable maximum cost before request
- ✅ Never surprised by charges

### 4. System Efficiency
- ✅ Reduces wasted provider calls
- ✅ Prevents payment processing errors
- ✅ Cleaner transaction logs

## Testing

Run the comprehensive test suite:

```bash
pytest tests/services/test_credit_precheck.py -v
```

Tests cover:
- Model default max_tokens lookup
- Maximum cost calculation
- Credit sufficiency checking
- Trial user bypass
- Integration scenarios
- Edge cases

## Examples

### Example 1: Sufficient Credits

**Request:**
```json
{
  "model": "gpt-4o",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 100
}
```

**User Credits:** $5.00

**Pre-check:**
- Input: ~5 tokens
- Max output: 100 tokens
- Max cost: ~$0.002
- Check: $5.00 >= $0.002 ✅

**Result:** Request proceeds

**Actual Usage:**
- Input: 5 tokens
- Output: 8 tokens
- Actual cost: $0.0003

**Charged:** $0.0003 (not $0.002)

---

### Example 2: Insufficient Credits

**Request:**
```json
{
  "model": "gpt-4",
  "messages": [{"role": "user", "content": "Write a long essay"}],
  "max_tokens": 8000
}
```

**User Credits:** $0.10

**Pre-check:**
- Input: ~15 tokens
- Max output: 8000 tokens
- Max cost: ~$0.50
- Check: $0.10 >= $0.50 ❌

**Result:** Request blocked

**Error:**
```json
{
  "error": {
    "message": "Insufficient credits. Maximum possible cost: $0.5000. Available: $0.1000. Shortfall: $0.4000. Tip: Reduce max_tokens from 8000 to lower the cost.",
    "type": "insufficient_credits",
    "code": "payment_required"
  }
}
```

---

### Example 3: User Reduces max_tokens

**First Attempt (Blocked):**
```json
{
  "model": "gpt-4o",
  "max_tokens": 4096  // Too expensive
}
```
Error: "Need $0.20, have $0.05"

**Second Attempt (Success):**
```json
{
  "model": "gpt-4o",
  "max_tokens": 500  // Reduced
}
```
Max cost: $0.03 < $0.05 ✅ Request proceeds

---

### Example 4: Trial User (Always Allowed)

**Request:**
```json
{
  "model": "gpt-4",
  "max_tokens": 8000
}
```

**User:** Trial user with $0.00 credits

**Pre-check:** Trial user bypass ✅

**Result:** Request proceeds, $0 charged

## Migration Notes

### Backward Compatibility

✅ **Fully backward compatible** - No breaking changes:

1. `max_tokens` was already accepted (optional)
2. Now it's used for pre-flight checks
3. Existing integrations continue working
4. Better error messages for users

### What Changed

**Before:**
- Simple check: `credits > 0`
- Could start expensive requests
- Deduction failures after generation

**After:**
- Smart check: `credits >= max_cost`
- Blocks expensive requests early
- No deduction failures (pre-checked)

## Future Enhancements

Potential improvements:

1. **User-specific limits**
   - Allow users to set personal max_cost limits
   - Notify before exceeding budget

2. **Auto-adjust max_tokens**
   - Suggest optimal max_tokens based on credits
   - "You have $0.05, max recommended max_tokens: 500"

3. **Credit reservation**
   - Reserve credits during request
   - Release difference after actual usage

4. **Analytics dashboard**
   - Show users their cost predictions
   - Historical max vs actual cost comparison

## Support

For questions or issues:
- GitHub Issues: https://github.com/Alpaca-Network/gatewayz-backend/issues
- Documentation: https://docs.gatewayz.ai
