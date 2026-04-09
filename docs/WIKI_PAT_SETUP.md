# Wiki Auto-Sync: PAT Setup Guide

The wiki auto-sync GitHub Action needs a Personal Access Token to push to the wiki repo. The default `GITHUB_TOKEN` doesn't have wiki write permissions.

**Who needs to do this**: A repo admin (someone with admin access to `Alpaca-Network/gatewayz-backend`).

**Time**: ~3 minutes.

---

## Step 1: Create a Fine-Grained Personal Access Token

1. Go to **https://github.com/settings/tokens?type=beta**
2. Click **"Generate new token"**
3. Fill in:
   - **Token name**: `gatewayz-wiki-sync`
   - **Expiration**: 90 days (or longer — you'll need to rotate it when it expires)
   - **Resource owner**: Select **Alpaca-Network**
   - **Repository access**: Click **"Only select repositories"** → select **gatewayz-backend**
   - **Permissions** → **Repository permissions**:
     - **Contents**: **Read and write**
     - Leave everything else as default (No access)
4. Click **"Generate token"**
5. **Copy the token** — you won't see it again

---

## Step 2: Add the Token as a Repository Secret

1. Go to **https://github.com/Alpaca-Network/gatewayz-backend/settings/secrets/actions**
2. Click **"New repository secret"**
3. Fill in:
   - **Name**: `WIKI_PAT`
   - **Secret**: Paste the token from Step 1
4. Click **"Add secret"**

---

## Step 3: Verify It Works

1. Go to **https://github.com/Alpaca-Network/gatewayz-backend/actions/workflows/sync-wiki.yml**
2. Click **"Run workflow"** → **"Run workflow"** (manual trigger)
3. Wait for it to complete (~30 seconds)
4. Check the wiki: **https://github.com/Alpaca-Network/gatewayz-backend/wiki/API-Mappings** — it should show "Auto-generated" at the top

---

## What This Enables

After setup, the wiki's API Mappings and Test Mapping pages automatically regenerate whenever code is merged to `main` that changes routes, tests, services, or database modules. No manual wiki edits needed for those pages.

---

## Token Rotation

When the token expires, repeat Steps 1-2 with a new token. The secret name (`WIKI_PAT`) stays the same — just update the value.
