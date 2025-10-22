# CI/CD Pipeline Architecture Diagram

## Complete Pipeline Visualization

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          DEVELOPER WORKFLOW                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ git commit
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        PRE-COMMIT HOOKS (Local)                          │
├─────────────────────────────────────────────────────────────────────────┤
│  ✓ Black (code formatting)                                              │
│  ✓ isort (import sorting)                                               │
│  ✓ Ruff (linting)                                                       │
│  ✓ Bandit (security)                                                    │
│  ✓ File checks (secrets, large files)                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ git push
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      GITHUB ACTIONS - CI WORKFLOW                        │
│                           (ci.yml)                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
        ┌───────────────┐ ┌─────────────┐ ┌──────────────┐
        │ Code Quality  │ │  Security   │ │  Run Tests   │
        │   Checks      │ │    Scan     │ │  (pytest)    │
        ├───────────────┤ ├─────────────┤ ├──────────────┤
        │ • Ruff        │ │ • Bandit    │ │ • Unit       │
        │ • Black       │ │ • Safety    │ │ • Integration│
        │ • isort       │ │             │ │ • Must Pass! │
        └───────┬───────┘ └──────┬──────┘ └──────┬───────┘
                │                │                │
                └────────────────┼────────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │  Build Verification    │
                    ├────────────────────────┤
                    │ • Import check         │
                    │ • Config validation    │
                    │ • Railway files exist  │
                    └────────────┬───────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │ Deployment Ready ✅    │
                    ├────────────────────────┤
                    │ All checks passed!     │
                    │ Ready for CD →         │
                    └────────────┬───────────┘
                                 │
                                 │ CI Passed ✅
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     GITHUB ACTIONS - CD WORKFLOW                         │
│                          (deploy.yml)                                    │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │  Check CI Status        │
                    ├─────────────────────────┤
                    │ Determine environment:  │
                    │ • main → production     │
                    │ • staging → staging     │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │ Pre-deployment Checks  │
                    ├────────────────────────┤
                    │ • Verify config files  │
                    │ • Validate Python      │
                    └────────────┬───────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │  Railway Deployment    │
                    ├────────────────────────┤
                    │ Auto-deploy via        │
                    │ GitHub integration     │
                    └────────────┬───────────┘
                                 │
                                 │ Wait 2 minutes
                                 ▼
                    ┌────────────────────────┐
                    │ Post-deployment Check  │
                    ├────────────────────────┤
                    │ • Health check /health │
                    │ • Retry 5x with 30s    │
                    │ • Verify HTTP 200      │
                    └────────────┬───────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   Deployment Status    │
                    ├────────────────────────┤
                    │ ✅ Success → Notify    │
                    │ ❌ Failed → Alert      │
                    └────────────┬───────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        RAILWAY AUTO-DEPLOY                               │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
    ┌───────────────────────┐   ┌───────────────────────┐
    │   Staging Deploy      │   │  Production Deploy    │
    ├───────────────────────┤   ├───────────────────────┤
    │ Branch: staging       │   │ Branch: main          │
    │ Env: APP_ENV=staging  │   │ Env: APP_ENV=prod     │
    │ URL: staging-api...   │   │ URL: api.gatewayz.ai  │
    │ Stripe: Test mode     │   │ Stripe: Live mode     │
    └───────────────────────┘   └───────────────────────┘
                    │                         │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   🚀 DEPLOYED! 🎉     │
                    └────────────────────────┘
```

## Manual Deployment Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    MANUAL DEPLOYMENT TRIGGER                             │
│                    (deploy-manual.yml)                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                 │
                    User triggers via GitHub UI
                    Selects: Environment (staging/production)
                            Skip tests? (yes/no)
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                    ▼                         ▼
        ┌───────────────────┐     ┌───────────────────┐
        │   Run Tests       │     │   Skip Tests      │
        │   (if selected)   │     │   (not recommended)│
        └─────────┬─────────┘     └─────────┬─────────┘
                  │                         │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │  Deploy to Railway     │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │   Health Check         │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │   Deployment Complete  │
                  └────────────────────────┘
```

## Environment Flow

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   feature/   │      │   staging    │      │     main     │
│   branches   │ ───▶ │   branch     │ ───▶ │   branch     │
└──────────────┘      └──────────────┘      └──────────────┘
       │                     │                      │
       │                     │                      │
       ▼                     ▼                      ▼
  CI Runs              CI + CD Runs           CI + CD Runs
  No Deploy            Deploy to              Deploy to
                       Staging                Production
```

## Deployment Decision Tree

```
                    Push to GitHub
                         │
                         ▼
            ┌────────────────────────┐
            │  Which branch?         │
            └────────────┬───────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   feature/*        staging            main
        │                │                │
        ▼                ▼                ▼
   Run CI only    Run CI + CD        Run CI + CD
   No deploy      Deploy staging     Deploy production
                         │                │
                         ▼                ▼
                  staging-api.xyz    api.gatewayz.ai
```

## Rollback Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    ROLLBACK PROCEDURES                           │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐   ┌──────────────┐   ┌──────────────────┐
│ Railway UI    │   │ Git Revert   │   │ Manual Deploy    │
├───────────────┤   ├──────────────┤   ├──────────────────┤
│ 1. Go to      │   │ 1. Revert    │   │ 1. Trigger       │
│    Dashboard  │   │    commit    │   │    workflow      │
│ 2. Find last  │   │ 2. Push      │   │ 2. Select prev   │
│    working    │   │ 3. CI + CD   │   │    commit        │
│    deploy     │   │    runs      │   │ 3. Deploy        │
│ 3. Redeploy   │   └──────────────┘   └──────────────────┘
└───────────────┘
```

## Safety Checkpoints

```
Code Changes
    │
    ▼
┌────────────────────────┐
│ 1. Pre-commit hooks    │ ─── Blocks commit if fails
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│ 2. CI Pipeline         │ ─── Blocks merge if fails
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│ 3. Branch Protection   │ ─── Requires CI pass + approval
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│ 4. CD Pipeline         │ ─── Only runs if CI passes
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│ 5. Health Check        │ ─── Verifies deployment
└───────────┬────────────┘
            │
            ▼
      Production 🚀
```

## Summary

**4 Layers of Protection:**

1. ✅ **Pre-commit hooks** - Catch issues before commit
2. ✅ **CI Pipeline** - Validate before merge
3. ✅ **Branch protection** - Enforce code review
4. ✅ **CD Pipeline** - Verify after deployment

**2 Deployment Paths:**

1. 🤖 **Automatic** - CI passes → Railway deploys
2. 🎮 **Manual** - Trigger via GitHub UI

**3 Environments:**

1. 💻 **Local** - Development
2. 🧪 **Staging** - Testing
3. 🚀 **Production** - Live users
