# Setting Up GitHub Wiki (Optional)

This guide shows how to set up a GitHub Wiki to host your documentation online with a searchable, organized interface.

## What is GitHub Wiki?

GitHub Wiki is a built-in feature that provides:
- ✅ Separate documentation space (not in main repo)
- ✅ Built-in search functionality
- ✅ Easy navigation with sidebar
- ✅ Markdown support
- ✅ Version control
- ✅ Public or private (based on repo settings)

---

## Option 1: Enable GitHub Wiki

### Step 1: Enable Wiki on Your Repository

1. Go to your GitHub repository: `https://github.com/Alpaca-Network/gatewayz-backend`
2. Click **Settings** tab
3. Scroll down to **Features** section
4. Check ✅ **Wikis**
5. Click **Save**

### Step 2: Initialize Wiki

1. Click **Wiki** tab in your repository
2. Click **Create the first page**
3. Title: `Home`
4. Content: Copy content from `docs/DEVELOPER_WIKI.md`
5. Click **Save Page**

### Step 3: Add More Pages

For each documentation file:

1. Click **New Page**
2. Title: Use the document name (e.g., "Testing Workflows Locally")
3. Content: Copy from the markdown file
4. Click **Save Page**

### Step 4: Create Sidebar Navigation

1. Click **Add custom sidebar**
2. Title: `_Sidebar`
3. Content: Create a navigation menu
4. Click **Save Page**

**Example Sidebar:**
```markdown
## 📚 Quick Navigation

### Getting Started
- [Home](Home)
- [Setup Guide](Setup-Guide)
- [Architecture](Architecture)

### Features
- [Model Health](Model-Health-Overview)
- [Pricing System](Pricing-System-Index)
- [Activity Logging](Activity-Logging)

### Deployment
- [Railway Quick Start](Railway-Quick-Start)
- [Vercel Deployment](Vercel-Deployment)

### CI/CD
- [Testing Workflows](Testing-Workflows-Locally)
- [Supabase Migrations](Supabase-Migrations-CI)

### Monitoring
- [Observability](Observability-Quick-Start)
- [Error Monitoring](Error-Monitoring)
```

---

## Option 2: Clone and Push Documentation (Advanced)

### Step 1: Clone Wiki Repository

```bash
# Wiki is a separate git repository
git clone https://github.com/Alpaca-Network/gatewayz-backend.wiki.git

cd gatewayz-backend.wiki
```

### Step 2: Copy Documentation Files

```bash
# Copy all docs from main repo
cp -r ../gatewayz-backend/docs/*.md .

# Organize into folders (optional)
mkdir -p Setup Features Deployment Monitoring
mv setup-*.md Setup/
mv *-QUICKSTART.md Features/
```

### Step 3: Create Home Page

```bash
# Create or edit Home.md
cat > Home.md << 'EOF'
# Gatewayz Backend - Developer Wiki

[Content from docs/DEVELOPER_WIKI.md]
EOF
```

### Step 4: Push to Wiki

```bash
git add .
git commit -m "Initial wiki setup with all documentation"
git push origin master
```

---

## Option 3: Use GitHub Pages (Recommended for Advanced Setup)

GitHub Pages creates a beautiful static documentation site.

### Step 1: Enable GitHub Pages

1. Go to **Settings** > **Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** or **gh-pages**
4. Folder: **/docs**
5. Click **Save**

### Step 2: Add Jekyll Configuration

Create `docs/_config.yml`:

```yaml
theme: jekyll-theme-cayman
title: Gatewayz Backend Documentation
description: Comprehensive developer documentation
show_downloads: false

# Navigation
navigation:
  - title: Home
    url: /
  - title: Setup
    url: /setup/
  - title: Features
    url: /features/
  - title: Deployment
    url: /deployment/
```

### Step 3: Create Index Page

Create `docs/index.md`:

```markdown
---
layout: default
title: Home
---

# Gatewayz Backend Documentation

[Content from DEVELOPER_WIKI.md]
```

### Step 4: Deploy

```bash
git add docs/_config.yml docs/index.md
git commit -m "Setup GitHub Pages"
git push origin main
```

Your docs will be available at:
`https://alpaca-network.github.io/gatewayz-backend/`

---

## Option 4: Use MkDocs (Most Professional)

MkDocs creates a beautiful, searchable documentation site.

### Step 1: Install MkDocs

```bash
pip install mkdocs mkdocs-material
```

### Step 2: Initialize MkDocs

```bash
cd /path/to/gatewayz-backend
mkdocs new .
```

### Step 3: Configure MkDocs

Edit `mkdocs.yml`:

```yaml
site_name: Gatewayz Backend Documentation
site_description: Comprehensive developer documentation
site_author: Gatewayz Team
site_url: https://alpaca-network.github.io/gatewayz-backend/

theme:
  name: material
  palette:
    primary: indigo
    accent: indigo
  features:
    - navigation.tabs
    - navigation.sections
    - toc.integrate
    - search.suggest
    - search.highlight

nav:
  - Home: index.md
  - Getting Started:
      - Setup: setup.md
      - Architecture: architecture.md
  - Features:
      - Model Health: features/MODEL_HEALTH_OVERVIEW.md
      - Pricing: features/PRICING_SYSTEM_INDEX.md
  - Deployment:
      - Railway: deployment/RAILWAY_QUICKSTART.md
      - Vercel: deployment/VERCEL_DEPLOYMENT.md
  - CI/CD:
      - Testing Workflows: TESTING_WORKFLOWS_LOCALLY.md
      - Migrations: SUPABASE_MIGRATIONS_CI.md
  - Monitoring:
      - Observability: monitoring/OBSERVABILITY_QUICKSTART.md
  - API: api.md

plugins:
  - search
  - tags

markdown_extensions:
  - admonition
  - codehilite
  - toc:
      permalink: true
```

### Step 4: Build and Serve

```bash
# Serve locally
mkdocs serve

# Build static site
mkdocs build

# Deploy to GitHub Pages
mkdocs gh-deploy
```

---

## Comparison of Options

| Feature | GitHub Wiki | GitHub Pages | MkDocs |
|---------|-------------|--------------|--------|
| **Setup Difficulty** | ⭐ Easy | ⭐⭐ Medium | ⭐⭐⭐ Advanced |
| **Search** | ✅ Built-in | ⚠️ Limited | ✅ Advanced |
| **Navigation** | ✅ Good | ⚠️ Manual | ✅ Excellent |
| **Customization** | ⚠️ Limited | ⭐⭐ Medium | ⭐⭐⭐ Full |
| **Version Control** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Mobile Friendly** | ✅ Yes | ⚠️ Depends | ✅ Yes |
| **Offline Access** | ❌ No | ❌ No | ✅ Yes |
| **Auto-Deploy** | ✅ Yes | ✅ Yes | ✅ Yes |

---

## Recommended Setup for Gatewayz

### For Quick Setup: Use GitHub Wiki
- ✅ Enable Wiki in repository settings
- ✅ Copy `docs/DEVELOPER_WIKI.md` as Home page
- ✅ Create sidebar navigation
- ✅ Add key documents as pages

### For Professional Setup: Use MkDocs Material
- ✅ Install MkDocs with Material theme
- ✅ Configure `mkdocs.yml` with navigation
- ✅ Deploy to GitHub Pages with `mkdocs gh-deploy`
- ✅ Get beautiful, searchable documentation site

---

## Current Setup (Already Done)

✅ **Markdown-based Documentation** in `docs/` folder
✅ **Developer Wiki Index**: `docs/DEVELOPER_WIKI.md`
✅ **Documentation Navigator**: `./docs-nav.sh`
✅ **Quick References**: Multiple `*_QUICKSTART.md` files

**What you have now:**
- All docs in version control
- Easy to edit and update
- Accessible via GitHub web interface
- Can be viewed locally

**To make it better:**
- Enable GitHub Wiki for online access
- Or set up MkDocs for professional site
- Or use GitHub Pages for simple static site

---

## Quick Commands

```bash
# View documentation locally
./docs-nav.sh wiki

# Search documentation
./docs-nav.sh search deployment

# List all docs
./docs-nav.sh list

# Open specific doc
./docs-nav.sh open DEVELOPER_WIKI.md
```

---

## Next Steps

1. **Decide on hosting method:**
   - GitHub Wiki (easiest)
   - GitHub Pages (simple)
   - MkDocs (most professional)

2. **Enable and configure:**
   - Follow steps above for chosen method

3. **Maintain documentation:**
   - Keep `docs/` folder as source of truth
   - Update wiki/pages when docs change
   - Use CI/CD to auto-deploy (optional)

---

**Recommendation**: Start with the current markdown setup (which is excellent), and add GitHub Wiki or Pages when you want public-facing documentation.
