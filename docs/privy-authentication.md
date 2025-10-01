# Privy Authentication Integration

## Overview

This API gateway now supports Privy authentication integration, allowing users to sign up and sign in using Google, Email, and GitHub authentication methods through Privy's frontend SDK.

## Authentication Flow

### 1. Frontend Integration with Privy

The frontend uses Privy's authentication SDK to handle user authentication. After successful authentication, Privy returns a user object with:

- **Email Authentication**: Email address
- **Google Authentication**: User's name and Gmail address  
- **GitHub Authentication**: GitHub username and display name
- **Privy User ID**: Unique identifier for each user account

### 2. Backend Endpoints

#### Authentication - `POST /auth`

Handles complete Privy authentication response from frontend. This is the main endpoint that processes the full user data from Privy's frontend SDK.

**Request Body:**
```json
{
  "id": "did:privy:cmfwykhyu00nrjp0bdn8drfta",
  "createdAt": "2025-09-23T19:38:46.000Z",
  "linkedAccounts": [
    {
      "subject": "109955238781595640390",
      "email": "koala1201au@gmail.com",
      "name": "Koala",
      "type": "google_oauth",
      "firstVerifiedAt": "2025-09-23T19:38:46.000Z",
      "latestVerifiedAt": "2025-09-24T17:00:40.000Z"
    },
    {
      "address": "koala1201au@gmail.com",
      "type": "email",
      "firstVerifiedAt": "2025-09-23T19:57:55.000Z",
      "latestVerifiedAt": "2025-10-01T09:53:23.000Z"
    }
  ],
  "email": {
    "address": "koala1201au@gmail.com"
  },
  "google": {
    "subject": "109955238781595640390",
    "email": "koala1201au@gmail.com",
    "name": "Koala"
  },
  "delegatedWallets": [],
  "mfaMethods": [],
  "hasAcceptedTerms": false,
  "isGuest": false
}
```

**Response:**
```json
{
  "success": true,
  "message": "User created and authenticated successfully",
  "user_id": 123,
  "api_key": "abc123...",
  "auth_method": "google",
  "privy_user_id": "did:privy:cmfwykhyu00nrjp0bdn8drfta",
  "is_new_user": true,
  "display_name": "Koala",
  "email": "koala1201au@gmail.com",
  "credits": 10,
  "timestamp": "2024-01-15T10:30:00Z"
}
```

#### Legacy Endpoints (Deprecated)

**Note**: The following endpoints are deprecated in favor of the new `/auth` endpoint:

##### Sign Up - `POST /signup` (Deprecated)

Creates a new user account with Privy authentication.

**Request Body:**
```json
{
  "privy_user_id": "privy_user_12345",
  "auth_method": "email|google|github",
  "email": "user@example.com",           // Required for email auth
  "username": "username",                // Optional, auto-generated if not provided
  "display_name": "Display Name",        // Optional, for Google/GitHub
  "gmail_address": "user@gmail.com",     // Required for Google auth
  "github_username": "githubuser"        // Required for GitHub auth
}
```

##### Sign In - `POST /signin` (Deprecated)

Authenticates an existing user with Privy ID.

**Request Body:**
```json
{
  "privy_user_id": "privy_user_12345",
  "auth_method": "email|google|github"
}
```

**Response:**
```json
{
  "user_id": 123,
  "privy_user_id": "privy_user_12345",
  "username": "username",
  "email": "user@example.com",
  "auth_method": "email",
  "api_key": "abc123...",
  "credits": 10,
  "is_new_user": false,
  "message": "Welcome back!",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

#### Create Authenticated API Key - `POST /create-authenticated`

Creates or retrieves an API key for an authenticated user.

**Query Parameters:**
- `privy_user_id` (required): Privy user ID from frontend
- `auth_method` (required): Authentication method used
- `key_name` (optional): Name for the API key (default: "Primary Key")

**Response:**
```json
{
  "user_id": 123,
  "privy_user_id": "privy_user_12345",
  "username": "username",
  "email": "user@example.com",
  "auth_method": "email",
  "api_key": "abc123...",
  "credits": 10,
  "is_new_user": false,
  "message": "API key created successfully!",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## Database Schema

The users table has been extended to support Privy authentication:

```sql
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    privy_user_id VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    auth_method VARCHAR(20) NOT NULL,
    api_key VARCHAR(255) UNIQUE,
    credits INTEGER DEFAULT 10,
    display_name VARCHAR(255),
    gmail_address VARCHAR(255),
    github_username VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

## Frontend Integration

### 1. Install Privy SDK

```bash
npm install @privy-io/react-auth
```

### 2. Initialize Privy Provider

```jsx
import { PrivyProvider } from '@privy-io/react-auth';

function App() {
  return (
    <PrivyProvider
      appId="your-privy-app-id"
      config={{
        loginMethods: ['email', 'google', 'github'],
        appearance: {
          theme: 'light',
          accentColor: '#676FFF',
        },
      }}
    >
      <YourApp />
    </PrivyProvider>
  );
}
```

### 3. Handle Authentication

```jsx
import { usePrivy } from '@privy-io/react-auth';

function AuthComponent() {
  const { ready, authenticated, user, login, logout } = usePrivy();

  const handleSignup = async () => {
    if (authenticated && user) {
      const authData = {
        privy_user_id: user.id,
        auth_method: user.linkedAccounts[0].type, // 'email', 'google', or 'github'
        email: user.email?.address,
        username: user.email?.address?.split('@')[0],
        display_name: user.google?.name,
        gmail_address: user.google?.email,
        github_username: user.github?.username
      };

      const response = await fetch('/api/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authData)
      });

      const result = await response.json();
      // Store API key in localStorage
      localStorage.setItem('api_key', result.api_key);
    }
  };

  const handleSignin = async () => {
    if (authenticated && user) {
      const authData = {
        privy_user_id: user.id,
        auth_method: user.linkedAccounts[0].type
      };

      const response = await fetch('/api/signin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authData)
      });

      const result = await response.json();
      // Store API key in localStorage
      localStorage.setItem('api_key', result.api_key);
    }
  };

  if (!ready) return <div>Loading...</div>;

  return (
    <div>
      {authenticated ? (
        <div>
          <p>Welcome, {user.email?.address}!</p>
          <button onClick={handleSignin}>Sign In</button>
          <button onClick={logout}>Logout</button>
        </div>
      ) : (
        <button onClick={login}>Login</button>
      )}
    </div>
  );
}
```

## API Usage

After authentication, users can use their API key to access protected endpoints:

```javascript
const apiKey = localStorage.getItem('api_key');

// Use API key in requests
const response = await fetch('/api/chat', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${apiKey}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    model: 'openai/gpt-4',
    messages: [{ role: 'user', content: 'Hello!' }]
  })
});
```

## Error Handling

### Common Error Responses

- **400 Bad Request**: Invalid authentication method or missing required fields
- **404 Not Found**: User not found during signin
- **500 Internal Server Error**: Database or server error

### Example Error Response

```json
{
  "detail": "Email is required for email authentication"
}
```

## Security Considerations

1. **API Key Storage**: Store API keys securely in localStorage or secure storage
2. **HTTPS**: Always use HTTPS in production
3. **Token Validation**: API keys are validated on each request
4. **Rate Limiting**: Implement rate limiting for authentication endpoints
5. **Audit Logging**: All authentication events are logged for security

## Testing

Use the provided test script to verify the authentication flow:

```bash
# Test scripts have been removed - use the API endpoints directly for testing
```

This will test:
- Email signup/signin
- Google signup/signin  
- GitHub signup/signin
- API key creation
- Error handling

## Migration from Legacy System

The legacy `/create` endpoint remains available for backward compatibility. New applications should use the Privy authentication flow:

1. **Legacy**: `POST /create` - Direct user registration
2. **New**: `POST /signup` + `POST /signin` - Privy authentication flow

Both systems can coexist, allowing for gradual migration.
