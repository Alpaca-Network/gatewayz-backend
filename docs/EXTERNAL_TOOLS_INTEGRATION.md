# External Tools Integration Guide

This guide covers integrating external tools (Apidog, Postman, etc.) with your Gatewayz API gateway.

## API Base URL for External Tools

When configuring external tools to communicate with your Gatewayz API, **always use:**

```
https://api.gatewayz.ai
```

### URL Components

| Component | Value |
|-----------|-------|
| **Protocol** | `https://` (always use HTTPS for production) |
| **Hostname** | `api.gatewayz.ai` |
| **Port** | Not needed (defaults to 443 for HTTPS) |
| **Path** | `/v1/` for OpenAI-compatible endpoints |

### Example Endpoints

```
# Health Check
GET https://api.gatewayz.ai/health

# Chat Completions (OpenAI-compatible)
POST https://api.gatewayz.ai/v1/chat/completions

# Model Catalog
GET https://api.gatewayz.ai/v1/models

# Image Generation
POST https://api.gatewayz.ai/v1/images/generations
```

## Apidog Integration

### Setup

1. **Configure API Base URL**
   - In Apidog: Settings → API Configuration
   - Set Base URL to: `https://api.gatewayz.ai`

2. **Authentication**
   - Method: `Bearer Token` or `API Key`
   - Header: `Authorization`
   - Value: Your Gatewayz API key (starts with `sk-`)

3. **CORS Headers**
   - Already configured on the API side
   - Origin: `https://docs.gatewayz.ai` is allowed

### Testing Health Endpoint

```bash
# In Apidog, create a request:
GET https://api.gatewayz.ai/health

# Expected Response (200 OK):
{
    "status": "healthy"
}
```

### Known Issues

#### DNS Resolver Error: "ELANREFUSED"

**Problem:** When Apidog's self-hosted runner tries to reach your API, it may fail with:
```
ELANREFUSED. DNS Resolver Error
```

**Reason:** Apidog has dynamic egress IPs and may not have DNS resolution for your hostname from their network environment.

**Solution:** See [APIDOG_DNS_RESOLVER_TROUBLESHOOTING.md](./APIDOG_DNS_RESOLVER_TROUBLESHOOTING.md)

## Postman Integration

### Setup

1. **Create Collection**
   - Name: "Gatewayz API"
   - Click "Variables" tab

2. **Set Variables**
   ```
   Variable Name: base_url
   Initial Value: https://api.gatewayz.ai
   Current Value: https://api.gatewayz.ai
   ```

3. **Add Authentication**
   - Go to collection → "Authorization"
   - Type: `Bearer Token`
   - Token: Your Gatewayz API key

4. **Create Request**
   ```
   GET {{base_url}}/health
   ```

### Import Collection

```bash
# Create a Postman collection file (postman_collection.json)
# Then import it into Postman
```

Example collection structure:
```json
{
  "info": {
    "name": "Gatewayz API",
    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
  },
  "item": [
    {
      "name": "Health Check",
      "request": {
        "method": "GET",
        "url": "{{base_url}}/health"
      }
    },
    {
      "name": "Chat Completion",
      "request": {
        "method": "POST",
        "url": "{{base_url}}/v1/chat/completions",
        "body": {
          "mode": "raw",
          "raw": "{\"model\": \"gpt-4\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}]}"
        }
      }
    }
  ]
}
```

## cURL / Command Line

### Basic Health Check
```bash
curl https://api.gatewayz.ai/health
```

### With Authentication
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/health
```

### Chat Completion Request
```bash
curl -X POST https://api.gatewayz.ai/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [
      {"role": "user", "content": "Hello, how are you?"}
    ]
  }'
```

## Network Requirements

### Prerequisites

For external tools to communicate with your API:

1. **DNS Resolution**
   - `api.gatewayz.ai` must be publicly resolvable
   - Resolve command: `nslookup api.gatewayz.ai`

2. **Network Connectivity**
   - HTTPS (port 443) must be accessible
   - No firewall blocking (unless allowlisting required)
   - Tool must have internet access

3. **TLS Certificate**
   - Valid HTTPS certificate on `api.gatewayz.ai`
   - Browsers/tools should accept it without warnings

4. **CORS Headers** (for browser-based tools)
   - Already configured for `https://docs.gatewayz.ai`
   - Other origins can be allowlisted upon request

### Firewall Considerations

If your organization has strict firewall rules:

1. **Allowlist the API domain:**
   ```
   *.gatewayz.ai (or specifically api.gatewayz.ai)
   ```

2. **For Apidog specifically:**
   - Request IP range information from Apidog support
   - Once received, allowlist those IPs
   - See [APIDOG_DNS_RESOLVER_TROUBLESHOOTING.md](./APIDOG_DNS_RESOLVER_TROUBLESHOOTING.md)

## Authentication

### API Key Format

Gatewayz API keys follow this format:
```
sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

### Header Format

```
Authorization: Bearer sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

### Getting an API Key

1. Sign up at https://gatewayz.ai
2. Go to Settings → API Keys
3. Create a new API key
4. Copy and save securely (shown only once)

## Troubleshooting

### 404 Not Found

```
GET https://api.gatewayz.ai/invalid-endpoint
Response: 404
```

**Solution:** Check the endpoint path. Valid endpoints:
- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/images/generations`

### 401 Unauthorized

```
Response: 401 Unauthorized
```

**Solution:** Check your API key:
- Is it included in the Authorization header?
- Is it in the correct format: `Bearer sk-...`?
- Has it expired?
- Is it a valid key from your account?

### 403 Forbidden

```
Response: 403 Forbidden
```

**Solution:** Your API key may be:
- Associated with an inactive account
- Restricted by IP allowlist (if configured)
- Rate limited (too many requests)

### CORS Error (from browser)

```
Access-Control-Allow-Origin header missing
```

**Solution:**
- Using HTTP instead of HTTPS? Use `https://api.gatewayz.ai`
- Need a different origin allowlisted? Contact support@gatewayz.ai

### Connection Timeout

```
Error: Connection timeout
```

**Solution:**
- Check DNS: `nslookup api.gatewayz.ai`
- Check internet connectivity
- Firewall blocking access? Check with your IT team
- Try from a different network to isolate the issue

### DNS Resolution Failed (ELANREFUSED)

```
ELANREFUSED. DNS Resolver Error
```

**Solution:** See [APIDOG_DNS_RESOLVER_TROUBLESHOOTING.md](./APIDOG_DNS_RESOLVER_TROUBLESHOOTING.md)

This is a known issue with Apidog's dynamic network environment. Workarounds and solutions are documented.

## Testing Integration

### Step 1: Verify DNS
```bash
nslookup api.gatewayz.ai
# Should resolve to an IP address
```

### Step 2: Test Connectivity
```bash
curl -v https://api.gatewayz.ai/health
# Should return HTTP 200
```

### Step 3: Test in Your Tool
1. Create a request to `GET https://api.gatewayz.ai/health`
2. Execute the request
3. Should see: `{"status":"healthy"}`

### Step 4: Test with Authentication
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.gatewayz.ai/health
# Should still return HTTP 200
```

## Common Integrations

### Zapier
- Webhook URL: `https://api.gatewayz.ai/v1/chat/completions`
- Auth: Bearer token in header
- Method: POST
- Body: JSON format (see API docs)

### Make (formerly Integromat)
- Webhook URL: `https://api.gatewayz.ai/v1/chat/completions`
- Auth: Bearer token in header
- Method: POST

### n8n
- Webhook URL: `https://api.gatewayz.ai/v1/chat/completions`
- Auth: Bearer token in header
- Method: POST

### Custom Scripts
See cURL examples above for reference on how to build requests.

## API Documentation

For complete API documentation:
- **OpenAI-Compatible Endpoints:** https://api.gatewayz.ai/docs
- **Swagger UI:** https://api.gatewayz.ai/docs
- **ReDoc:** https://api.gatewayz.ai/redoc

## Support

- **Documentation:** https://gatewayz.ai
- **Email Support:** support@gatewayz.ai
- **API Status:** https://api.gatewayz.ai/health
- **Issues:** Report at support@gatewayz.ai

---

**Last Updated:** 2025-11-25
**Gatewayz Version:** 2.0.3
**API Version:** v1 (OpenAI-compatible)
