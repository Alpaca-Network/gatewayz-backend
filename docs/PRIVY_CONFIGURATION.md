# Privy Authentication Configuration

This document describes the required configuration for Privy authentication to work correctly with the Gatewayz frontend.

## Overview

Gatewayz uses [Privy](https://privy.io) for frontend authentication. Privy provides OAuth integration with providers like Google, GitHub, and Twitter. For the OAuth flow to work, the frontend's domain must be whitelisted in the Privy Dashboard.

## Error: "Must specify origin"

If users encounter the following error when attempting to log in via OAuth (Google, GitHub, etc.):

```
Error: Must specify origin
FetchError: [POST] "https://auth.privy.io/api/v1/oauth/init": 403
```

This error occurs because the frontend's origin (e.g., `https://beta.gatewayz.ai`) is not configured in the Privy Dashboard's allowed origins list.

## How to Fix

### Step 1: Access Privy Dashboard

1. Go to [Privy Dashboard](https://dashboard.privy.io)
2. Log in with your Privy account credentials
3. Select the Gatewayz application

### Step 2: Configure Allowed Origins

1. Navigate to **Settings** → **Client Settings** or **Allowed Origins**
2. Add all required origins:

   **Production:**
   - `https://gatewayz.ai`
   - `https://www.gatewayz.ai`
   - `https://beta.gatewayz.ai`

   **Staging:**
   - `https://staging.gatewayz.ai`

   **Development:**
   - `http://localhost:3000`
   - `http://localhost:3001`
   - `http://127.0.0.1:3000`
   - `http://127.0.0.1:3001`

3. Save the configuration

### Step 3: Verify Configuration

1. Wait a few minutes for changes to propagate
2. Attempt to log in again via OAuth
3. The login flow should now work correctly

## Why This Happens

Privy uses origin validation as a security measure to prevent unauthorized websites from using your Privy App ID. When a user attempts OAuth login:

1. The frontend makes a POST request to `https://auth.privy.io/api/v1/oauth/init`
2. The browser automatically includes the `Origin` header (e.g., `https://beta.gatewayz.ai`)
3. Privy checks if this origin is in the allowed origins list for your App ID
4. If not found, Privy returns a `403 Forbidden` error with "Must specify origin"

## Checklist for New Environments

When deploying to a new domain or environment, ensure:

- [ ] Domain is added to Privy Dashboard allowed origins
- [ ] CORS is configured in the backend (`src/main.py`)
- [ ] Frontend environment variables point to correct backend URL
- [ ] SSL certificate is valid for the domain

## Related Configuration

### Backend CORS Configuration

The backend also needs to allow requests from the frontend. This is configured in `src/main.py`:

```python
base_origins = [
    "https://beta.gatewayz.ai",
    "https://staging.gatewayz.ai",
    "https://api.gatewayz.ai",
    "https://docs.gatewayz.ai",
]
```

And in `src/constants.py`:

```python
FRONTEND_BETA_URL = "https://beta.gatewayz.ai"
FRONTEND_STAGING_URL = "https://staging.gatewayz.ai"
```

### Vercel Headers

Static CORS headers are also configured in `vercel.json`:

```json
{
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        {
          "key": "Access-Control-Allow-Origin",
          "value": "https://beta.gatewayz.ai"
        }
      ]
    }
  ]
}
```

## Support

If you continue to experience issues after configuring allowed origins:

1. Clear browser cache and cookies
2. Try in an incognito/private window
3. Check browser developer console for detailed error messages
4. Verify the Privy App ID matches between frontend and dashboard
5. Contact Privy support at [support.privy.io](https://support.privy.io)

---

**Last Updated**: December 2024
