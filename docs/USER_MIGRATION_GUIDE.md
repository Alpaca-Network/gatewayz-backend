# Migration Guide: Unified Chat API (`/v1/chat`)

## Overview

We're consolidating all chat endpoints into a single, unified endpoint: **`/v1/chat`**

This new endpoint automatically detects your request format and returns responses in the matching format. It supports all providers, models, and features you're already using.

## Why Migrate?

- **Simpler API**: One endpoint instead of 5 different ones
- **Auto-format detection**: No need to worry about which endpoint to use
- **Better performance**: Optimized unified codebase
- **Future-proof**: All new features will be added to `/v1/chat`
- **Same functionality**: Everything you use today works exactly the same

## What's Changing?

### Old Endpoints (Deprecated)
These endpoints still work but will be removed on **June 1, 2025**:

- `/v1/chat/completions` (OpenAI format)
- `/v1/messages` (Anthropic format)
- `/v1/responses` (OpenAI Responses API)
- `/api/chat/ai-sdk` (Vercel AI SDK)
- `/api/chat/ai-sdk-completions` (Vercel AI SDK)

### New Endpoint (Recommended)
- `/v1/chat` - Supports all formats automatically

## Migration Examples

### If you're using OpenAI format

**Before:**
```bash
POST https://api.gatewayz.ai/v1/chat/completions
```

**After:**
```bash
POST https://api.gatewayz.ai/v1/chat
```

**That's it!** The request and response format stay exactly the same.

#### Example Code

**Python (OpenAI SDK)**
```python
from openai import OpenAI

client = OpenAI(
    base_url="https://api.gatewayz.ai/v1",  # Just change base_url
    api_key="your-gatewayz-api-key"
)

response = client.chat.completions.create(
    model="gpt-4",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
```

The OpenAI SDK automatically appends `/chat/completions`, which our server handles. The new endpoint works seamlessly!

**JavaScript/TypeScript (OpenAI SDK)**
```typescript
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: 'https://api.gatewayz.ai/v1',
  apiKey: 'your-gatewayz-api-key'
});

const response = await client.chat.completions.create({
  model: 'gpt-4',
  messages: [
    { role: 'user', content: 'Hello!' }
  ]
});
```

### If you're using Anthropic format

**Before:**
```bash
POST https://api.gatewayz.ai/v1/messages
```

**After:**
```bash
POST https://api.gatewayz.ai/v1/chat
```

**Everything else stays the same.**

#### Example Code

**Python**
```python
import requests

response = requests.post(
    "https://api.gatewayz.ai/v1/chat",  # Changed URL
    headers={
        "Authorization": "Bearer your-gatewayz-api-key",
        "Content-Type": "application/json"
    },
    json={
        "model": "claude-3-opus-20240229",
        "system": "You are a helpful assistant",
        "messages": [
            {"role": "user", "content": "Hello!"}
        ],
        "max_tokens": 1024
    }
)

print(response.json())
```

The endpoint auto-detects Anthropic format (because of the `system` field) and returns an Anthropic-formatted response.

### If you're using Responses API

**Before:**
```bash
POST https://api.gatewayz.ai/v1/responses
```

**After:**
```bash
POST https://api.gatewayz.ai/v1/chat
```

#### Example Code

**JavaScript**
```javascript
const response = await fetch('https://api.gatewayz.ai/v1/chat', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer your-gatewayz-api-key',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    model: 'gpt-4',
    input: [
      { role: 'user', content: 'Hello!' }
    ]
  })
});

const data = await response.json();
console.log(data);
```

The endpoint auto-detects Responses API format (because of the `input` field).

### If you're using Vercel AI SDK

**Before:**
```bash
POST https://api.gatewayz.ai/api/chat/ai-sdk
```

**After:**
```bash
POST https://api.gatewayz.ai/v1/chat
```

#### Example Code

**TypeScript (Vercel AI SDK)**
```typescript
import { OpenAIProvider } from '@ai-sdk/openai';
import { generateText } from 'ai';

const gatewayz = new OpenAIProvider({
  baseURL: 'https://api.gatewayz.ai/v1',
  apiKey: 'your-gatewayz-api-key'
});

const { text } = await generateText({
  model: gatewayz('gpt-4'),
  prompt: 'Hello!'
});
```

## Advanced: Explicit Format Override

If you want to explicitly specify the format (useful for testing), use the `format` field:

```json
{
  "format": "anthropic",  // Force Anthropic response format
  "model": "gpt-4",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ]
}
```

Valid formats: `"openai"`, `"anthropic"`, `"responses"`

## Format Auto-Detection

The endpoint automatically detects your format based on:

1. **Explicit `format` field** (highest priority)
2. **`input` field** → Responses API format
3. **`system` field** (string) → Anthropic format
4. **Default** → OpenAI format

## Migration Checklist

- [ ] Update your base URL to use `/v1/chat`
- [ ] Test with a few requests
- [ ] Verify responses match the old endpoint
- [ ] Deploy to your staging environment
- [ ] Test thoroughly
- [ ] Deploy to production
- [ ] Monitor for any issues

## Need Help?

- **Documentation**: https://docs.gatewayz.ai
- **Support**: support@gatewayz.ai
- **Discord**: https://discord.gg/gatewayz

## Timeline

- **Now - May 31, 2025**: Both old and new endpoints work
- **June 1, 2025**: Old endpoints will be removed
- **Recommended**: Migrate before April 1, 2025

## FAQs

### Q: Will my existing code break?
**A:** No! Old endpoints continue to work until June 1, 2025. You have 4+ months to migrate.

### Q: Do I need to change my request format?
**A:** No! Keep using the same request format. The new endpoint handles all formats.

### Q: Will responses change?
**A:** No! Responses will be in the exact same format as before.

### Q: What if I use multiple formats?
**A:** Perfect! The unified endpoint handles all formats. You can even mix formats in the same application.

### Q: Do I need to update my API key?
**A:** No! Use the same API key.

### Q: Will there be any downtime?
**A:** No! This is a seamless transition. Both endpoints work during the migration period.

### Q: What about streaming?
**A:** Streaming works exactly the same. Just change the URL.

### Q: What about function/tool calling?
**A:** All features work the same. Function calling, tools, streaming, etc.

### Q: Will this affect my billing?
**A:** No change to billing. Same pricing structure.

### Q: Can I test before migrating?
**A:** Yes! The new endpoint is live now. Test it thoroughly before switching over.

---

**Questions?** Reach out to support@gatewayz.ai - we're here to help!
