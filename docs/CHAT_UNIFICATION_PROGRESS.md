# Chat Endpoint Unification - Progress Report

## Summary

**Date**: 2026-01-20
**Completed**: Tasks 1-8 (Core Infrastructure)
**Remaining**: Tasks 9-16 (Endpoint Refactoring + Testing)

## ✅ Completed Work (Tasks 1-8)

### Phase 1-2: Foundation & Adapters (Tasks 1-5)

**Commits**:
- `a205d1da` - Tasks 1-4: Internal schemas + Base adapter + OpenAI & Anthropic adapters
- `3c2ffc9e` - Task 5: AI SDK adapter

**Created Files**:
```
src/schemas/internal/
├── __init__.py           # Exports all internal schemas
└── chat.py              # InternalChatRequest, InternalChatResponse, InternalMessage, etc.

src/adapters/
├── __init__.py
└── chat/
    ├── __init__.py      # Exports all adapters
    ├── base.py          # BaseChatAdapter interface
    ├── openai.py        # OpenAIChatAdapter (SSE streaming)
    ├── anthropic.py     # AnthropicChatAdapter (event-based streaming)
    └── ai_sdk.py        # AISDKChatAdapter (simplified OpenAI format)
```

**Key Features**:
- ✅ Internal schemas provide single source of truth for request/response format
- ✅ All adapters implement BaseChatAdapter interface
- ✅ Streaming support for all formats (SSE for OpenAI/AI SDK, events for Anthropic)
- ✅ Complete parameter support (tools, temperature, max_tokens, etc.)
- ✅ Gateway-specific metadata (cost tracking, provider info)

### Phase 3: Core Handler (Tasks 6-8)

**Commits**:
- `47f526d9` - Task 6: ChatInferenceHandler foundation
- `c929acaf` - Task 7: Non-streaming process() method
- `1eec1992` - Task 8: Streaming process_stream() method

**Created Files**:
```
src/handlers/
├── __init__.py          # Exports ChatInferenceHandler
└── chat_handler.py      # Complete unified handler (534 lines)
```

**Handler Methods**:

1. **`__init__(api_key, background_tasks)`** - Initialize with user context
2. **`_initialize_user_context()`** - Load user and validate trial access
3. **`_call_provider(provider, model, messages, **kwargs)`** - Route to provider client
4. **`_call_provider_stream(provider, model, messages, **kwargs)`** - Streaming provider call
5. **`_charge_user(cost, model, prompt_tokens, completion_tokens)`** - Credit deduction
6. **`_save_request_record(...)`** - Log to chat_completion_requests table
7. **`async process(request)`** - **Main non-streaming pipeline** (192 lines)
8. **`async process_stream(request)`** - **Main streaming pipeline** (202 lines)

**Pipeline Flow (process() method)**:
```
1. Initialize user context (get_user, validate_trial_access)
2. Transform model ID (apply_transformations)
3. Select provider with failover (ProviderSelector.execute_with_failover)
4. Call provider (_call_provider)
5. Extract token usage from response
6. Calculate cost (calculate_cost)
7. Charge user (_charge_user) - with trial support
8. Save request metadata (_save_request_record)
9. Return InternalChatResponse
```

**Pipeline Flow (process_stream() method)**:
```
1. Initialize user context
2. Transform model ID
3. Select provider (no failover for streaming yet)
4. Stream from provider (_call_provider_stream)
5. Yield InternalStreamChunk objects
6. Track tokens during stream
7. After stream: calculate cost, charge user, log
```

**Key Features**:
- ✅ Single implementation for ALL chat endpoints
- ✅ Provider routing with intelligent failover (non-streaming)
- ✅ Support for OpenRouter, Cerebras, Groq (easily extensible)
- ✅ Trial user detection with subscription override (defense-in-depth)
- ✅ Token estimation fallback for providers without usage data
- ✅ Comprehensive error handling and logging
- ✅ Background task support for async operations

---

## 🔄 Remaining Work (Tasks 9-16)

### Phase 4: Endpoint Refactoring (Tasks 9-12)

**Goal**: Replace endpoint business logic with unified handler + adapter

#### Task 9: Refactor /chat/completions (src/routes/chat.py:1453-2750)

**Current State**: ~1300 lines with:
- Anonymous user support
- Braintrust tracing
- Performance tracking
- Chat history integration
- Auto web search
- Plan limit enforcement

**Refactoring Strategy**:

1. **Keep outer scaffolding** (anonymous handling, tracing, tracking)
2. **Replace core inference section** with handler + adapter
3. **Simplify response formatting** using adapter

**Code Pattern**:

```python
from src.adapters.chat import OpenAIChatAdapter
from src.handlers import ChatInferenceHandler

@router.post("/chat/completions", tags=["chat"])
async def chat_completions(
    req: ProxyRequest,
    background_tasks: BackgroundTasks,
    api_key: str | None = Depends(get_optional_api_key),
    session_id: int | None = Query(None),
    request: Request = None,
):
    # ... Keep existing setup (lines 1462-1598):
    # - Request ID generation
    # - Anonymous user validation
    # - Authenticated user validation
    # - Braintrust span initialization
    # - Performance tracker setup

    try:
        # ... Keep existing preprocessing (lines 1599-1799):
        # - Chat history fetch
        # - Auto web search trigger
        # - Plan limit pre-check

        # === UNIFIED HANDLER SECTION (REPLACES ~500 lines) ===

        # Step 1: Convert ProxyRequest to internal format
        adapter = OpenAIChatAdapter()
        internal_request = adapter.to_internal_request(req.dict())

        # Step 2: Process with unified handler
        handler = ChatInferenceHandler(api_key, background_tasks)

        if req.stream:
            # Streaming response
            async def stream_wrapper():
                try:
                    # Get internal stream
                    internal_stream = handler.process_stream(internal_request)

                    # Convert to external format
                    async for sse_chunk in adapter.from_internal_stream(internal_stream):
                        yield sse_chunk

                except Exception as e:
                    logger.error(f"Streaming error: {e}")
                    error_chunk = {"error": {"message": str(e)}}
                    yield f"data: {json.dumps(error_chunk)}\n\n"
                    yield "data: [DONE]\n\n"

            return StreamingResponse(
                stream_wrapper(),
                media_type="text/event-stream",
                headers={
                    "X-Accel-Buffering": "no",
                    "Cache-Control": "no-cache",
                },
            )
        else:
            # Non-streaming response
            internal_response = await handler.process(internal_request)
            external_response = adapter.from_internal_response(internal_response)

            # ... Keep existing postprocessing:
            # - Chat history save
            # - Braintrust logging
            # - Performance metrics

            return external_response

    except Exception as e:
        # ... Keep existing error handling
        raise
```

**Result**: ~1300 lines → ~300 lines (75% reduction)

**Deleted Code Sections**:
- ❌ Provider routing logic (~200 lines)
- ❌ Cost calculation (~50 lines)
- ❌ Credit deduction logic (~100 lines)
- ❌ Token usage extraction (~50 lines)
- ❌ Response formatting (~100 lines)

**Preserved Code Sections**:
- ✅ Anonymous user handling
- ✅ Braintrust tracing
- ✅ Performance tracking
- ✅ Chat history integration
- ✅ Auto web search
- ✅ Plan limits

---

#### Task 10: Refactor /messages (src/routes/messages.py:204-1000+)

**Current State**: ~800 lines (Anthropic Messages API endpoint)

**Refactoring Strategy**: Same as Task 9, but use `AnthropicChatAdapter`

**Code Pattern**:
```python
from src.adapters.chat import AnthropicChatAdapter
from src.handlers import ChatInferenceHandler

@router.post("/messages", tags=["messages"])
async def messages_endpoint(
    req: MessagesRequest,  # Anthropic format
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
):
    # Convert Anthropic → Internal
    adapter = AnthropicChatAdapter()
    internal_request = adapter.to_internal_request(req.dict())

    # Process with unified handler
    handler = ChatInferenceHandler(api_key, background_tasks)

    if req.stream:
        # Streaming with Anthropic events
        async def stream_wrapper():
            internal_stream = handler.process_stream(internal_request)
            async for event_line in adapter.from_internal_stream(internal_stream):
                yield event_line

        return StreamingResponse(stream_wrapper(), media_type="text/event-stream")
    else:
        # Non-streaming
        internal_response = await handler.process(internal_request)
        return adapter.from_internal_response(internal_response)
```

**Result**: ~800 lines → ~150 lines (81% reduction)

---

#### Task 11: Refactor /responses (src/routes/chat.py:2750+)

**Similar to Task 9** - Use OpenAIChatAdapter (or create custom adapter if format differs)

---

#### Task 12: Refactor AI SDK Endpoints (src/routes/ai_sdk.py:221-1006)

**Current State**: 2 endpoints, ~800 lines total
- `/api/chat/ai-sdk`
- `/api/chat/ai-sdk-completions`

**Refactoring Strategy**: Use `AISDKChatAdapter`

**Code Pattern**:
```python
from src.adapters.chat import AISDKChatAdapter
from src.handlers import ChatInferenceHandler

@router.post("/api/chat/ai-sdk", tags=["ai-sdk"])
@router.post("/api/chat/ai-sdk-completions", tags=["ai-sdk"])
async def ai_sdk_chat_completion(
    req: AISDKChatRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
):
    adapter = AISDKChatAdapter()
    internal_request = adapter.to_internal_request(req.dict())

    handler = ChatInferenceHandler(api_key, background_tasks)

    if req.stream:
        async def stream_wrapper():
            internal_stream = handler.process_stream(internal_request)
            async for chunk in adapter.from_internal_stream(internal_stream):
                yield chunk
        return StreamingResponse(stream_wrapper(), media_type="text/event-stream")
    else:
        internal_response = await handler.process(internal_request)
        return adapter.from_internal_response(internal_response)
```

**Result**: ~800 lines → ~100 lines (87% reduction)

---

### Phase 5: Testing (Tasks 13-16)

#### Task 13: Unit Tests for Schemas (tests/schemas/internal/test_chat.py)

**Coverage**:
- InternalChatRequest validation
- InternalChatResponse validation
- InternalMessage with different roles
- InternalUsage validation
- Serialization/deserialization
- Edge cases

#### Task 14: Unit Tests for Adapters (tests/adapters/chat/)

**Files**:
- `test_openai.py`
- `test_anthropic.py`
- `test_ai_sdk.py`

**Coverage**:
- `to_internal_request()` accuracy
- `from_internal_response()` accuracy
- Streaming conversion
- Format-specific quirks (Anthropic system message, etc.)

#### Task 15: Unit Tests for Handler (tests/handlers/test_chat_handler.py)

**Coverage**:
- `process()` with mocked providers
- `process_stream()` with async streaming
- Provider routing
- Cost calculation accuracy
- Credit charging
- Error handling

#### Task 16: Integration Tests (tests/integration/test_unified_chat.py)

**Coverage**:
- End-to-end tests for all endpoints
- Streaming and non-streaming
- Response format validation
- Credit deduction verification

---

## Benefits of Unified Architecture

### Before (Duplicated Logic)
```
/chat/completions:    ~1300 lines (provider routing, pricing, charging, logging)
/messages:            ~800 lines  (provider routing, pricing, charging, logging)
/responses:           ~500 lines  (provider routing, pricing, charging, logging)
/api/chat/ai-sdk:     ~800 lines  (provider routing, pricing, charging, logging)
───────────────────────────────────────────────────────────────────────────────
TOTAL:                ~3400 lines of duplicated business logic
```

### After (Unified Handler)
```
ChatInferenceHandler:     534 lines (SINGLE implementation)
Adapters (3):            ~700 lines (format conversion only)
Refactored endpoints:    ~550 lines (thin wrappers)
───────────────────────────────────────────────────────────────────────────────
TOTAL:                  ~1784 lines (48% code reduction)
```

### Key Improvements
- ✅ **Single source of truth** for chat inference logic
- ✅ **No duplicated code** across endpoints
- ✅ **Easier maintenance** - bug fixes apply to ALL endpoints
- ✅ **Consistent behavior** - all endpoints work identically
- ✅ **Easier testing** - test handler once, not 4 endpoints
- ✅ **Easy to add providers** - update handler, not 4 endpoints
- ✅ **Easy to add features** - update handler, not 4 endpoints

---

## Next Steps

### To Resume Work

1. **Start with Task 9** (refactor /chat/completions):
   ```bash
   git checkout -b feature/refactor-chat-completions
   # Edit src/routes/chat.py (lines 1453-2750)
   # Replace core inference with handler + adapter
   # Test thoroughly
   git commit -m "feat: Refactor /chat/completions to use unified handler (Task 9)"
   ```

2. **Continue with Tasks 10-12** (other endpoints)

3. **Add comprehensive tests** (Tasks 13-16)

### Testing Checklist

Before marking endpoints as "done":
- [ ] Non-streaming requests work
- [ ] Streaming requests work
- [ ] Anonymous users work (for /chat/completions)
- [ ] Trial users work
- [ ] Paid users work
- [ ] Cost calculation is correct
- [ ] Credit deduction works
- [ ] Request logging works
- [ ] Error handling works
- [ ] All existing features preserved (history, web search, etc.)

---

## Files Modified So Far

```
Created:
  src/schemas/internal/__init__.py
  src/schemas/internal/chat.py
  src/adapters/__init__.py
  src/adapters/chat/__init__.py
  src/adapters/chat/base.py
  src/adapters/chat/openai.py
  src/adapters/chat/anthropic.py
  src/adapters/chat/ai_sdk.py
  src/handlers/__init__.py
  src/handlers/chat_handler.py

To Modify (Tasks 9-12):
  src/routes/chat.py (lines 1453-2750, 2750+)
  src/routes/messages.py (lines 204-1000+)
  src/routes/ai_sdk.py (lines 221-1006)

To Create (Tasks 13-16):
  tests/schemas/internal/test_chat.py
  tests/adapters/chat/test_openai.py
  tests/adapters/chat/test_anthropic.py
  tests/adapters/chat/test_ai_sdk.py
  tests/handlers/test_chat_handler.py
  tests/integration/test_unified_chat.py
```

---

## GitHub Issues

- #862 ✅ Task 1: Create Internal Schemas
- #863 ✅ Task 2: Create Base Adapter
- #864 ✅ Task 3: Create OpenAI Adapter
- #865 ✅ Task 4: Create Anthropic Adapter
- #866 ✅ Task 5: Create AI SDK Adapter
- #867 ✅ Task 6: ChatInferenceHandler Foundation
- #868 ✅ Task 7: Implement Non-Streaming Handler
- #869 ✅ Task 8: Implement Streaming Handler
- #870 ⏳ Task 9: Refactor /chat/completions Endpoint
- #871 ⏳ Task 10: Refactor /messages Endpoint
- #872 ⏳ Task 11: Refactor /responses Endpoint
- #873 ⏳ Task 12: Refactor AI SDK Endpoints
- #874 ⏳ Task 13: Unit Tests for Schemas
- #875 ⏳ Task 14: Unit Tests for Adapters
- #876 ⏳ Task 15: Unit Tests for Handler
- #877 ⏳ Task 16: Integration Tests

---

**Status**: 8/16 tasks complete (50%), core infrastructure ready for endpoint refactoring
