# Gatewayz API Documentation Index

**Welcome to the Gatewayz API Documentation!** This index helps you navigate all documentation organized wiki-style by topic.

> 💡 **New to the project?** Start with the [Quick Start Guide](./getting-started/QUICK_START.md)

---

## 📂 Documentation Structure

```
docs/
├── getting-started/     → New users start here
├── deployment/          → Deploy and manage the application
├── development/         → Guides for developers
├── features/            → Feature-specific documentation
├── integrations/        → External service integrations
├── monitoring/          → Observability and tracking
├── security/            → Security guides and incident response
├── operations/          → Day-to-day operational guides
├── reference/           → Technical reference and troubleshooting
└── automation/          → Automated testing and deployment
```

---

## 🚀 Getting Started

**Start here if you're new to the project:**

| Document | Description |
|----------|-------------|
| [Quick Start](./getting-started/QUICK_START.md) | Get up and running in 15 minutes |
| [Complete Setup Guide](./getting-started/COMPLETE_SETUP_GUIDE.md) | Comprehensive setup walkthrough |
| [Environment Setup](./getting-started/ENVIRONMENT_SETUP.md) | Configure your development environment |
| [Project Overview](./getting-started/PROJECT_OVERVIEW.md) | Understand the codebase structure (AI context) |
| [Setup Guide](./getting-started/SETUP.md) | Local development setup |
| [Environment Reference](./getting-started/ENVIRONMENT.md) | Environment variables reference |

---

## 📦 Deployment

**Everything you need to deploy and manage the application:**

### Core Deployment
| Document | Description |
|----------|-------------|
| [Deployment Workflow](./deployment/DEPLOYMENT_WORKFLOW.md) | Complete deployment process (staging → production) |
| [Deployment Quick Ref](./deployment/DEPLOYMENT_QUICK_REF.md) | Quick deployment commands |
| [Railway Deployment](./deployment/RAILWAY_DEPLOYMENT.md) | Deploy to Railway platform |
| [Vercel Deployment](./deployment/VERCEL_DEPLOYMENT.md) | Deploy to Vercel serverless |
| [Deploy Migrations](./deployment/DEPLOY_MIGRATIONS.md) | Database migration deployment |

### Staging & Testing
| Document | Description |
|----------|-------------|
| [Staging Setup Complete](./deployment/STAGING_SETUP_COMPLETE.md) | **⭐ Start here for staging** |
| [Railway Staging Setup](./deployment/RAILWAY_STAGING_SETUP.md) | Detailed staging environment setup |
| [Staging API Security](./deployment/STAGING_API_SECURITY.md) | Secure your staging API |
| [Enable Staging Security](./deployment/ENABLE_STAGING_SECURITY.md) | Quick 5-min security setup |
| [Testing Environment](./deployment/TESTING_ENVIRONMENT.md) | Testing best practices |
| [Testing Quick Start](./deployment/TESTING_QUICKSTART.md) | 15-min testing setup |

### Railway Specific
| Document | Description |
|----------|-------------|
| [Railway Quick Start](./deployment/RAILWAY_QUICKSTART.md) | Fast Railway setup |
| [Railway Setup Checklist](./deployment/RAILWAY_SETUP_CHECKLIST.md) | Pre-deployment checklist |
| [Railway Autonomous Deployment](./deployment/RAILWAY_AUTONOMOUS_DEPLOYMENT.md) | Automated Railway deployments |
| [Railway Initial Setup](./deployment/RAILWAY_INITIAL_SETUP.md) | First-time Railway setup |
| [Railway Error Monitoring](./deployment/RAILWAY_ERROR_MONITORING_WORKFLOW.md) | Error monitoring workflow |
| [Railway Prometheus Setup](./deployment/RAILWAY_PROMETHEUS_SETUP.md) | Metrics on Railway |

### Auto-Deployment
| Document | Description |
|----------|-------------|
| [Auto Deployment Architecture](./deployment/AUTO_DEPLOYMENT_ARCHITECTURE.md) | Architecture overview |
| [Auto Deployment Setup](./deployment/AUTO_DEPLOYMENT_SETUP.md) | Setup guide |
| [Auto Deployment Getting Started](./deployment/AUTO_DEPLOYMENT_GETTING_STARTED.md) | Quick start |
| [Auto Deploy Quick Start](./deployment/AUTO_DEPLOY_QUICKSTART.md) | Fast setup |

### Configuration
| Document | Description |
|----------|-------------|
| [GitHub Secrets Setup](./deployment/GITHUB_SECRETS_SETUP.md) | Configure CI/CD secrets |
| [Supabase Migrations CI](./deployment/SUPABASE_MIGRATIONS_CI.md) | Automated migrations |

---

## 🔧 Development

**Guides for developers working on the codebase:**

### Testing
| Document | Description |
|----------|-------------|
| [Pre-Push Checklist](./development/PRE_PUSH_CHECKLIST.md) | **Check before you push!** |
| [Testing Workflows Locally](./development/TESTING_WORKFLOWS_LOCALLY.md) | Test GitHub Actions locally |
| [Live API Testing Guide](./development/LIVE_API_TESTING_GUIDE.md) | Test against live endpoints |
| [Failover Testing Guide](./development/FAILOVER_TESTING_GUIDE.md) | Test failover scenarios |

### Database & Migrations
| Document | Description |
|----------|-------------|
| [Migration Sync Guide](./development/MIGRATION_SYNC_GUIDE.md) | Database migrations workflow |
| [Supabase Migrations CI](./development/SUPABASE_MIGRATIONS_CI.md) | Automated migrations in CI |

### Best Practices
| Document | Description |
|----------|-------------|
| [Developer Wiki](./development/DEVELOPER_WIKI.md) | Tips, tricks, and guidelines |
| [PR Test Status](./development/PR_TEST_STATUS.md) | PR testing status |

---

## 🎯 Features

**Documentation for specific features:**

### Core Features
| Document | Description |
|----------|-------------|
| [Pricing System Index](./features/PRICING_SYSTEM_INDEX.md) | **⭐ Pricing system overview** |
| [Pricing Quick Start](./features/PRICING_QUICK_START.md) | Get started with pricing |
| [Pricing Implementation](./features/PRICING_IMPLEMENTATION_SUMMARY.md) | Implementation details |
| [Pricing Audit System](./features/PRICING_AUDIT_SYSTEM.md) | Audit pricing changes |
| [Model Health Overview](./features/MODEL_HEALTH_OVERVIEW.md) | Track model availability |
| [Model Health Quick Start](./features/MODEL_HEALTH_QUICK_START.md) | Quick setup |
| [Referral System](./features/REFERRAL_SYSTEM.md) | User referral program |
| [Activity Logging](./features/ACTIVITY_LOGGING.md) | User activity tracking |

### Model Management
| Document | Description |
|----------|-------------|
| [Model Sync](./features/MODEL_SYNC.md) | Synchronize model catalog |
| [Model Sync Quick Start](./features/MODEL_SYNC_QUICKSTART.md) | Quick setup |
| [Private Models Filter](./features/PRIVATE_MODELS_FILTER.md) | Hide private models |
| [Model Health API Spec](./features/MODEL_HEALTH_API_SPEC.md) | API specification |
| [Model Health UI Mockups](./features/MODEL_HEALTH_UI_MOCKUPS.md) | UI designs |

### Payments & Billing
| Document | Description |
|----------|-------------|
| [Stripe Integration](./features/STRIPE.md) | Payment processing |
| [GPT-5 Pricing Reference](./features/GPT5_PRICING_REFERENCE.md) | GPT-5 pricing |
| [GPT-5.1 Implementation](./features/GPT51_IMPLEMENTATION_GUIDE.md) | GPT-5.1 guide |

### Additional Features
| Document | Description |
|----------|-------------|
| [Statsig Feature Flags](./features/STATSIG_FEATURE_FLAGS.md) | A/B testing and flags |
| [Release Tracking](./features/RELEASE_TRACKING.md) | Track releases |
| [Auto Merge Implementation](./features/AUTO_MERGE_IMPLEMENTATION.md) | Auto-merge PRs |
| [Auto Merge Quick Start](./features/AUTO_MERGE_QUICK_START.md) | Quick setup |
| [Legacy API Key Migration](./features/LEGACY_API_KEY_MIGRATION.md) | Migrate old keys |
| [Health Availability API](./features/HEALTH_AVAILABILITY_API.md) | Health check API |

---

## 🔌 Integrations

**External service integrations:**

### AI Providers
| Document | Description |
|----------|-------------|
| [Integration Guide](./integrations/INTEGRATION_GUIDE.md) | Add new providers |
| [Google Vertex Migration](./integrations/GOOGLE_VERTEX_MIGRATION.md) | Vertex AI setup |
| [Alibaba Cloud Integration](./integrations/ALIBABA_CLOUD_INTEGRATION.md) | Alibaba setup |
| [OneRouter Integration](./integrations/ONEROUTER_INTEGRATION.md) | OneRouter setup |
| [Novita SDK Integration](./integrations/NOVITA_SDK_INTEGRATION.md) | Novita SDK |

### Frontend & Services
| Document | Description |
|----------|-------------|
| [Frontend Model Health Integration](./integrations/FRONTEND_MODEL_HEALTH_INTEGRATION.md) | Frontend integration |

---

## 📊 Monitoring & Observability

**Track and monitor your application:**

### Error Tracking
| Document | Description |
|----------|-------------|
| [Error Monitoring](./monitoring/ERROR_MONITORING.md) | Sentry error tracking |
| [Error Monitoring Quick Start](./monitoring/ERROR_MONITORING_QUICKSTART.md) | Quick setup |
| [Sentry Auto Capture Guide](./monitoring/SENTRY_AUTO_CAPTURE_GUIDE.md) | Automatic error capture |
| [Sentry Error Capture Expansion](./monitoring/SENTRY_ERROR_CAPTURE_EXPANSION.md) | Advanced capture |
| [Sentry Error Capture Quick Ref](./monitoring/SENTRY_ERROR_CAPTURE_QUICK_REF.md) | Quick reference |
| [PostHog Error Tracking](./monitoring/POSTHOG_ERROR_TRACKING.md) | Alternative tracking |

### Performance
| Document | Description |
|----------|-------------|
| [Performance Monitoring](./monitoring/PERFORMANCE_MONITORING.md) | Track performance |
| [Performance Monitoring Quick Start](./monitoring/PERFORMANCE_MONITORING_QUICKSTART.md) | Quick setup |
| [Performance Optimization](./monitoring/PERFORMANCE_OPTIMIZATION.md) | Optimization tips |

### Metrics & Observability
| Document | Description |
|----------|-------------|
| [Observability Quick Start](./monitoring/OBSERVABILITY_QUICKSTART.md) | Get started fast |
| [Prometheus Setup](./monitoring/PROMETHEUS_SETUP.md) | Metrics collection |
| [Grafana FastAPI Observability](./monitoring/GRAFANA_FASTAPI_OBSERVABILITY_SETUP.md) | Grafana setup |
| [OpenTelemetry Setup](./monitoring/OPENTELEMETRY_SETUP.md) | Distributed tracing |

### Health & Model Monitoring
| Document | Description |
|----------|-------------|
| [Health Monitoring](./monitoring/HEALTH_MONITORING.md) | Service health checks |
| [Model Health Sentry Integration](./monitoring/MODEL_HEALTH_SENTRY_INTEGRATION.md) | Track model health |
| [Frontend Monitoring](./monitoring/FRONTEND_MONITORING.md) | Frontend monitoring |

### Caching & Storage
| Document | Description |
|----------|-------------|
| [Redis Cache Integration](./monitoring/REDIS_CACHE_INTEGRATION.md) | Redis caching |
| [Redis Metrics Status](./monitoring/REDIS_METRICS_STATUS.md) | Redis metrics |

---

## 🔒 Security

**Security guides and incident response:**

| Document | Description |
|----------|-------------|
| [Security Incident Response](./security/SECURITY_INCIDENT_RESPONSE.md) | **Handle security incidents** |
| [Staging API Security](./security/STAGING_API_SECURITY.md) | Secure staging environment |
| [Enable Staging Security](./security/ENABLE_STAGING_SECURITY.md) | 5-min security setup |
| [API Key Setup](./security/API_KEY_SETUP.md) | Secure API key management |
| [Secrets Quick Reference](./security/SECRETS_QUICK_REFERENCE.md) | Manage secrets safely |

---

## ⚙️ Operations

**Day-to-day operational guides:**

| Document | Description |
|----------|-------------|
| [Operations Guide](./operations/OPERATIONS.md) | Daily operations |
| [Testing & Monitoring](./operations/TESTING_MONITORING.md) | Monitor tests |
| [Web Access Guide](./operations/WEB_ACCESS_GUIDE.md) | Access management |
| [Railway Setup Index](./operations/RAILWAY_SETUP_INDEX.md) | Railway operations index |

---

## 📚 Reference

**Technical reference documentation:**

### Core Reference
| Document | Description |
|----------|-------------|
| [System Architecture](./reference/ARCHITECTURE.md) | System design overview |
| [API Reference](./reference/API.md) | API endpoint documentation |
| [Troubleshooting Guide](./reference/TROUBLESHOOTING.md) | Common issues and solutions |
| [Project Overview (AI Context)](./getting-started/PROJECT_OVERVIEW.md) | Codebase context for AI |

### Fixes & Patches
| Document | Description |
|----------|-------------|
| [Gemini Routing Fix](./reference/GEMINI_ROUTING_FIX.md) | Gemini API routing fix |
| [Auth Timeout Fixes](./reference/AUTH_TIMEOUT_FIXES.md) | Authentication timeout issues |
| [OpenRouter Auth Fix](./reference/OPENROUTER_AUTH_FIX.md) | OpenRouter auth issues |
| [GitHub Actions Fixes](./reference/GITHUB_ACTIONS_FIXES.md) | CI/CD fixes |
| [GitHub Actions Secrets Fix](./reference/GITHUB_ACTIONS_SECRETS_FIX.md) | Secrets management |
| [Statsig Integration Fix](./reference/STATSIG_INTEGRATION_FIX.md) | Statsig issues |
| [PM2 Test Findings](./reference/PM2_TEST_FINDINGS.md) | PM2 test results |

### GitHub & Wiki
| Document | Description |
|----------|-------------|
| [GitHub Wiki Setup](./reference/GITHUB_WIKI_SETUP.md) | Set up project wiki |

---

## 🤖 Automation

**Automated testing and deployment:**

### API Testing
| Document | Description |
|----------|-------------|
| [Apidog Runner Setup](./automation/APIDOG_RUNNER_SETUP.md) | API test automation |
| [Apidog Runner Quick Start](./automation/APIDOG_RUNNER_QUICKSTART.md) | Quick setup |
| [Apidog Implementation Summary](./automation/APIDOG_RUNNER_IMPLEMENTATION_SUMMARY.md) | Implementation details |

### OpenRouter Testing
| Document | Description |
|----------|-------------|
| [OpenRouter Auto Testing Guide](./automation/OPENROUTER_AUTO_TESTING_GUIDE.md) | Automated API tests |
| [OpenRouter Auto Validation](./automation/OPENROUTER_AUTO_VALIDATION.md) | Validation automation |

### Deployment Automation
| Document | Description |
|----------|-------------|
| [Auto Deployment Architecture](./deployment/AUTO_DEPLOYMENT_ARCHITECTURE.md) | See Deployment section |
| [Auto Merge Implementation](./features/AUTO_MERGE_IMPLEMENTATION.md) | See Features section |

---

## 🔍 Quick Search

**Looking for something specific?**

| I want to... | Go to... |
|--------------|----------|
| Set up my local environment | [Environment Setup](./getting-started/ENVIRONMENT_SETUP.md) |
| Deploy to production | [Deployment Workflow](./deployment/DEPLOYMENT_WORKFLOW.md) |
| Set up staging environment | [Staging Setup Complete](./deployment/STAGING_SETUP_COMPLETE.md) |
| Secure my staging API | [Enable Staging Security](./deployment/ENABLE_STAGING_SECURITY.md) |
| Add a new AI provider | [Integration Guide](./integrations/INTEGRATION_GUIDE.md) |
| Track errors with Sentry | [Error Monitoring](./monitoring/ERROR_MONITORING.md) |
| Handle a security incident | [Security Incident Response](./security/SECURITY_INCIDENT_RESPONSE.md) |
| Run database migrations | [Migration Sync Guide](./development/MIGRATION_SYNC_GUIDE.md) |
| Test the API | [Live API Testing Guide](./development/LIVE_API_TESTING_GUIDE.md) |
| Understand pricing system | [Pricing System Index](./features/PRICING_SYSTEM_INDEX.md) |
| Fix a common issue | [Troubleshooting Guide](./reference/TROUBLESHOOTING.md) |
| Check before pushing code | [Pre-Push Checklist](./development/PRE_PUSH_CHECKLIST.md) |

---

## 📖 Documentation Standards

### File Naming Convention
- Use `SCREAMING_SNAKE_CASE.md` for documentation files
- Use descriptive names (e.g., `RAILWAY_STAGING_SETUP.md` not `setup.md`)
- Use `INDEX.md` for navigation files

### Directory Structure
- Keep related docs together in subdirectories
- Use existing categories when possible
- Create new categories only when necessary

### Quick Starts
- Many sections have `QUICKSTART.md` or `QUICK_START.md` files
- These provide 5-15 minute setup guides
- Always link to detailed docs for more information

---

## 📞 Need Help?

1. **Search this index** for your topic
2. **Check the [Troubleshooting Guide](./reference/TROUBLESHOOTING.md)**
3. **Review the [Operations Guide](./operations/OPERATIONS.md)**
4. **Ask in GitHub Discussions**
5. **Create an issue for bugs**

---

## 🌐 External Resources

- [Railway Documentation](https://docs.railway.app/)
- [Supabase Documentation](https://supabase.com/docs)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [OpenRouter Documentation](https://openrouter.ai/docs)
- [Stripe Documentation](https://stripe.com/docs)

---

**Last Updated:** December 2024
**Total Documents:** 100+
**Categories:** 10

**Maintained by:** Gatewayz Development Team

---

> 💡 **Tip:** Bookmark this page for easy access to all documentation!

> 📝 **Contributing:** Found an error or want to improve the docs? See [Developer Wiki](./development/DEVELOPER_WIKI.md)
