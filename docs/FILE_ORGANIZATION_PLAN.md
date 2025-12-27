# Documentation Reorganization Plan

## Current State
- 18 .md files in root directory
- ~100+ .md files scattered in docs/
- Hard to navigate and find information

## New Structure

```
docs/
├── INDEX.md                    # Main navigation hub (wiki-style)
├── README.md                   # Project overview (current)
│
├── getting-started/            # For new users
│   ├── QUICK_START.md
│   ├── COMPLETE_SETUP_GUIDE.md
│   ├── ENVIRONMENT_SETUP.md
│   └── PROJECT_OVERVIEW.md (was CLAUDE.md)
│
├── deployment/                 # Existing, organized
│   ├── DEPLOYMENT_WORKFLOW.md
│   ├── RAILWAY_STAGING_SETUP.md
│   ├── STAGING_API_SECURITY.md
│   ├── ENABLE_STAGING_SECURITY.md
│   └── TESTING_ENVIRONMENT.md
│
├── development/                # Development guides
│   ├── TESTING_GUIDE.md
│   ├── PRE_PUSH_CHECKLIST.md
│   ├── DEVELOPER_WIKI.md
│   ├── TESTING_WORKFLOWS_LOCALLY.md
│   ├── LIVE_API_TESTING_GUIDE.md
│   ├── FAILOVER_TESTING_GUIDE.md
│   ├── MIGRATION_SYNC_GUIDE.md
│   └── SUPABASE_MIGRATIONS_CI.md
│
├── features/                   # Existing, organized
│
├── integrations/               # Existing, organized
│
├── monitoring/                 # Existing, organized
│
├── security/                   # Security docs
│   ├── SECURITY_INCIDENT_RESPONSE.md
│   ├── SECRETS_QUICK_REFERENCE.md
│   ├── API_KEY_SETUP.md
│   └── STAGING_API_SECURITY.md (link)
│
├── operations/                 # Daily operations
│   ├── OPERATIONS.md
│   ├── TESTING_MONITORING.md
│   ├── WEB_ACCESS_GUIDE.md
│   └── RAILWAY_SETUP_INDEX.md
│
├── reference/                  # Technical reference
│   ├── ARCHITECTURE.md
│   ├── API.md
│   ├── CLAUDE.md (codebase context)
│   ├── TROUBLESHOOTING.md
│   ├── GEMINI_ROUTING_FIX.md
│   ├── AUTH_TIMEOUT_FIXES.md
│   └── GITHUB_ACTIONS_FIXES.md
│
└── automation/                 # Automation
    ├── AUTO_DEPLOYMENT_ARCHITECTURE.md
    ├── OPENROUTER_AUTO_TESTING_GUIDE.md
    ├── AUTO_MERGE_IMPLEMENTATION.md
    └── APIDOG_RUNNER_SETUP.md
```

## Files to Move from Root

### Getting Started
- QUICK_START.md → docs/getting-started/
- CLAUDE.md → docs/getting-started/PROJECT_OVERVIEW.md

### Deployment
- DEPLOYMENT.md → docs/deployment/ (already has one, merge/update)
- STAGING_SETUP_COMPLETE.md → docs/deployment/

### Development
- PRE_PUSH_CHECKLIST.md → docs/development/
- LIVE_API_TESTING_GUIDE.md → docs/development/
- FAILOVER_TESTING_GUIDE.md → docs/development/
- MIGRATION_SYNC_GUIDE.md → docs/development/
- TESTING_MONITORING.md → docs/operations/

### Security
- SECURITY_INCIDENT_RESPONSE.md → docs/security/
- SECRETS_QUICK_REFERENCE.md → docs/security/

### Operations
- WEB_ACCESS_GUIDE.md → docs/operations/
- RAILWAY_SETUP_INDEX.md → docs/operations/

### Reference
- GEMINI_ROUTING_FIX.md → docs/reference/
- GITHUB_ACTIONS_AUDIT.md → docs/reference/GITHUB_ACTIONS_FIXES.md
- PM2_TEST_FINDINGS.md → docs/reference/

### Keep in Root
- README.md (main project readme)
- AGENTS.md (if used by tools)

### Files Already in Correct Location
- docs/deployment/* (organized)
- docs/features/* (organized)
- docs/monitoring/* (organized)
- docs/integrations/* (organized)

## Navigation System

Create `docs/INDEX.md` as the main navigation hub with:
- Clear categories
- Quick search section
- Links to all major docs
- Breadcrumb-style navigation

## Benefits

1. **Easy Navigation** - Clear categories, wiki-style
2. **Reduced Clutter** - Root directory clean
3. **Logical Grouping** - Related docs together
4. **Scalable** - Easy to add new docs
5. **Discoverable** - Clear hierarchy
