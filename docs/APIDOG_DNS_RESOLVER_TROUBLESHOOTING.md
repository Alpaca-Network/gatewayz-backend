# Apidog DNS Resolver Error - Troubleshooting Guide

## Issue: "ELANREFUSED. DNS Resolver Error"

When using Apidog's self-hosted documentation or runner to test your Gatewayz API, you may encounter:

```
ELANREFUSED.DNS Resolver Error
"The system couldn't resolve a domain name (e.g., example.com) into an IP address"
```

This occurs when Apidog tries to reach `api.gatewayz.ai` but the hostname cannot be resolved from Apidog's network infrastructure.

## Root Cause

Apidog's self-hosted services:
- Have **dynamic egress IP addresses** that change frequently
- Cannot be easily allowlisted due to this dynamic nature
- May be subject to network restrictions in certain environments
- May not have DNS resolution configured for external domains

## Solutions

### Solution 1: Coordinate with Apidog (Primary)

**Status:** In progress - Apidog team is investigating options

1. **Request IP Range Documentation**
   - Contact Apidog support to request their dynamic IP range
   - Ask if they can provide static IPs or a CIDR block
   - Request any environment-specific networking documentation

2. **Ensure Firewall Rules**
   - Once you have the IP range, work with your infrastructure team to allowlist it
   - Configure your API gateway to accept requests from these IPs
   - Test connectivity after allowlisting

### Solution 2: DNS Resolution Testing

If you have access to Apidog's environment, test DNS resolution:

```bash
# From Apidog's infrastructure
nslookup api.gatewayz.ai
dig api.gatewayz.ai +short
```

**Troubleshooting steps:**
1. Verify `api.gatewayz.ai` is publicly resolvable
2. Check that it resolves to the correct IP address
3. Ensure no regional DNS restrictions apply
4. Test from multiple locations if possible

### Solution 3: Proxy/Gateway Configuration

If direct DNS resolution won't work:

1. **Configure a proxy in Apidog:**
   - Set up an HTTP/HTTPS proxy that routes requests
   - Configure Apidog to use this proxy
   - Route traffic through your organization's gateway

2. **Use a VPN tunnel:**
   - Establish a VPN connection between Apidog and your network
   - Route API traffic through the tunnel
   - This ensures all Apidog IPs have consistent access

### Solution 4: API Gateway Allowlisting

**Option A: Allowlist by IP Range**
```bash
# In your firewall/API gateway configuration
ALLOWED_IPS = [
    "198.51.100.0/24",    # Apidog IP range (example)
    "203.0.113.0/24",     # Additional ranges as provided
]
```

**Option B: Allowlist by Domain**
```bash
# In your reverse proxy (Nginx, CloudFlare, etc.)
allow domain "*.apidog.io";
```

**Option C: Custom Authentication**
```bash
# Require Apidog to authenticate
APIDOG_API_KEY = "your-secret-key"
```

## Implementation Checklist

- [ ] Contact Apidog support for IP range information
- [ ] Document any provided IP ranges or alternatives
- [ ] Update firewall rules with Apidog IP ranges (once provided)
- [ ] Test DNS resolution: `nslookup api.gatewayz.ai`
- [ ] Test connectivity from Apidog environment
- [ ] Verify CORS headers are correct: `Access-Control-Allow-Origin: https://docs.gatewayz.ai`
- [ ] Verify API responds to `GET /health`
- [ ] Create test request in Apidog to verify end-to-end

## Testing the Fix

Once changes are made:

### 1. Test DNS Resolution
```bash
# From Apidog's environment (if accessible)
nslookup api.gatewayz.ai
# Expected output: Shows IP address of your API gateway
```

### 2. Test HTTP Connectivity
```bash
# From Apidog's environment
curl -v https://api.gatewayz.ai/health
# Expected output: HTTP 200 with {"status":"healthy"}
```

### 3. Test from Apidog UI
1. Go to your Apidog project
2. Create a simple test request to `GET /health`
3. Execute the request
4. Should receive `200 OK` response

### 4. Test CORS Headers
```bash
# Verify CORS headers are present
curl -X OPTIONS -H "Origin: https://docs.gatewayz.ai" \
  -v https://api.gatewayz.ai/health

# Should see:
# access-control-allow-origin: https://docs.gatewayz.ai
# access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS
# access-control-allow-headers: ...
```

## Network Configuration

### For Your Infrastructure Team

**Required Configuration:**

1. **DNS:**
   - Ensure `api.gatewayz.ai` is publicly resolvable
   - Should resolve to your API gateway endpoint (Vercel/Railway/etc.)

2. **Firewall/Network:**
   - Allow inbound HTTPS (port 443) for API requests
   - Once Apidog provides IP range, allowlist those IPs (if applicable)
   - Ensure no rate limiting blocks legitimate requests

3. **API Gateway (FastAPI):**
   - Already configured: CORS allows `https://docs.gatewayz.ai`
   - Already configured: Health check at `GET /health`
   - No additional API changes needed

### CORS Headers (Already Configured)

Your API already includes:
```python
# src/main.py - Line 108
"https://docs.gatewayz.ai",  # Added for documentation site access
```

Verified CORS headers:
```
access-control-allow-origin: https://docs.gatewayz.ai
access-control-allow-credentials: true
access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS
access-control-allow-headers: Content-Type, Authorization, Accept, Origin, ...
access-control-max-age: 600
```

## Current Status

| Item | Status | Notes |
|------|--------|-------|
| CORS Configuration | ✅ Complete | `docs.gatewayz.ai` allowed in `src/main.py:108` |
| API Health Check | ✅ Complete | `/health` endpoint responds with 200 |
| DNS Resolution | ⏳ Blocked | Apidog can't resolve hostname from their network |
| IP Allowlisting | ⏳ Waiting | Awaiting Apidog IP range information |
| Documentation | ✅ Complete | This guide + implementation checklist |

## Waiting On

1. **Apidog Team:** IP range or alternative networking solution
2. **Your Infrastructure Team:** Firewall rule updates (once IP range received)

## References

- **Apidog Docs:** https://docs.apidog.com/
- **Apidog Support:** support@apidog.com
- **Your API:** https://api.gatewayz.ai
- **Docs Site:** https://docs.gatewayz.ai
- **API Health:** https://api.gatewayz.ai/health

## Next Steps

1. **Immediately:** Follow up with Apidog for IP range or alternatives
2. **In Parallel:** Have your infrastructure team review this guide
3. **Upon Receipt of Info:** Update firewall rules with Apidog IP ranges
4. **Finally:** Re-test DNS resolution and API connectivity from Apidog

## Questions?

If you still see "ELANREFUSED DNS Resolver Error" after implementing these steps:

1. Verify the IP address that Apidog is connecting from
2. Check firewall logs for blocked requests
3. Test from a machine with public internet access
4. Contact Apidog support with the error details

---

**Last Updated:** 2025-11-25
**Gatewayz Version:** 2.0.3
**Status:** Documentation for Known Issue
**Tracking:** Branch `terragon/fix-dns-resolver-error-rsloui`
