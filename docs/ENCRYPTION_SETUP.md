# API Key Encryption Setup

## Overview

The Gatewayz backend supports encrypted storage of API keys using Fernet (AES-128) encryption. This document explains how to configure encryption keys for production deployments.

## Current Status

**⚠️ Warning**: The production deployment currently has encryption **disabled** due to missing environment variables. API keys are being stored without encryption until the required configuration is applied.

From Railway logs:
```
⚠️ Encryption unavailable; proceeding without encrypted fields: No encryption keys configured
```

## Configuration Required

To enable API key encryption, you need to set the following environment variables:

### 1. `KEY_VERSION`

The current active encryption key version. Use an integer (e.g., `1`, `2`, `3`).

**Example:**
```bash
KEY_VERSION=1
```

### 2. `KEYRING_<version>`

The actual encryption key for each version. This should be a Fernet-compatible key (44 characters, base64-encoded).

**Example:**
```bash
KEYRING_1=your-base64-encoded-fernet-key-here==
```

## Generating Encryption Keys

### Method 1: Using Python (Recommended)

```python
from cryptography.fernet import Fernet

# Generate a new Fernet key
key = Fernet.generate_key()
print(key.decode())
```

### Method 2: Using Command Line

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This will output a key like:
```
# Output will be something like (this is just format example):
# AbCdEf1234567890+/=...<44-character-base64-string>
```

## Setting Up Encryption for Production

### Railway Deployment

1. **Go to Railway Dashboard** → Your Project → Variables

2. **Add the following environment variables:**
   ```
   KEY_VERSION=1
   KEYRING_1=<your-generated-fernet-key>
   ```

3. **Redeploy** the service for changes to take effect

4. **Verify** in logs that you see:
   ```
   ✅ Encryption enabled with key version 1
   ```
   Instead of:
   ```
   ⚠️ Encryption unavailable; proceeding without encrypted fields
   ```

### Vercel Deployment

1. **Go to Vercel Dashboard** → Project Settings → Environment Variables

2. **Add environment variables** for all environments (Production, Preview, Development):
   ```
   KEY_VERSION=1
   KEYRING_1=<your-generated-fernet-key>
   ```

3. **Redeploy** to apply changes

### Docker Deployment

Add to your `.env` file or docker-compose configuration:

```bash
KEY_VERSION=1
KEYRING_1=<your-generated-fernet-key>
```

## Key Rotation

To rotate encryption keys while maintaining access to old encrypted data:

### Step 1: Generate New Key

```python
from cryptography.fernet import Fernet
new_key = Fernet.generate_key()
print(f"KEYRING_2={new_key.decode()}")
```

### Step 2: Add New Key to Environment

```bash
KEY_VERSION=2              # Increment version
KEYRING_1=<old-key>       # Keep old key for decryption
KEYRING_2=<new-key>       # Add new key for encryption
```

### Step 3: Re-encrypt Existing Data

Run the key rotation script (future development):
```bash
python scripts/database/rotate_encryption_keys.py --from-version 1 --to-version 2
```

## Security Best Practices

### ✅ DO:
- **Generate unique keys** for each environment (production, staging, development)
- **Store keys securely** in your secret management system (Railway Secrets, AWS Secrets Manager, etc.)
- **Rotate keys regularly** (every 90-180 days recommended)
- **Keep backup** of old keys until all data is re-encrypted
- **Use different keys** per environment to prevent cross-environment access

### ❌ DON'T:
- **Never commit keys** to version control (git)
- **Never share keys** via email, Slack, or other insecure channels
- **Never use the same key** across different environments
- **Never delete old keys** until you're sure all data has been re-encrypted

## Verification

After configuring encryption, verify it's working:

### 1. Check Application Logs

Look for this log message on startup:
```
✅ Encryption enabled with key version 1
```

### 2. Test API Key Creation

Create a new API key via the API or dashboard. Check the database:

```sql
SELECT key_hash, encrypted_key FROM api_keys_new ORDER BY created_at DESC LIMIT 1;
```

The `encrypted_key` field should contain an encrypted value (starting with `gAAAAA`), not plaintext.

### 3. Check Railway Logs

After deployment, verify no encryption warnings appear:
```bash
# Should NOT see this anymore:
⚠️ Encryption unavailable; proceeding without encrypted fields
```

## Troubleshooting

### "Encryption unavailable" Warning Still Appears

**Cause**: Environment variables not set correctly.

**Solution**:
1. Double-check variable names (exact match: `KEY_VERSION` and `KEYRING_1`)
2. Verify variables are set in the correct environment (Production vs Preview)
3. Redeploy the service after adding variables
4. Check deployment logs for any configuration errors

### Invalid Fernet Key Error

**Cause**: Key is not properly base64-encoded or wrong length.

**Solution**:
```python
# Regenerate a valid key:
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### Can't Decrypt Old Keys After Rotation

**Cause**: Old keyring removed before re-encrypting data.

**Solution**:
1. Restore old keyring: `KEYRING_1=<old-key>`
2. Keep both keys active until all data is migrated
3. Run re-encryption script before removing old keys

## Migration from Unencrypted to Encrypted

If you're enabling encryption for the first time on an existing deployment with unencrypted keys:

### Option 1: Gradual Migration (Recommended)

1. **Enable encryption** (set `KEY_VERSION` and `KEYRING_1`)
2. **New keys** will be encrypted automatically
3. **Old keys** remain unencrypted but continue to work
4. **Optional**: Run migration script to encrypt existing keys

### Option 2: Force Re-creation

1. **Notify users** that API keys will be rotated
2. **Enable encryption**
3. **Delete old unencrypted keys**
4. **Users regenerate** new encrypted keys

## Related Files

- **Encryption Implementation**: `src/security/security.py`
- **API Key Storage**: `src/db/api_keys.py`
- **Secure API Key Creation**: `src/db_security.py`
- **Configuration**: `src/config/config.py`

## Support

For issues or questions about encryption setup:
- **GitHub Issues**: https://github.com/Alpaca-Network/gatewayz-backend/issues
- **Documentation**: `/docs`
- **Team Contact**: [Your support channel]

---

**Last Updated**: 2025-12-26
