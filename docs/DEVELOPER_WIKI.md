# Gatewayz Backend - Developer Wiki

**Welcome to the Gatewayz Universal Inference API Developer Documentation Hub**

This wiki provides comprehensive documentation for developers working on the Gatewayz backend. Use this as your central resource for setup, features, deployment, and operations.

---

## 📚 Quick Navigation

- [🚀 Getting Started](#-getting-started)
- [⚙️ Setup & Configuration](#️-setup--configuration)
- [✨ Features & Functionality](#-features--functionality)
- [🚢 Deployment](#-deployment)
- [📊 Monitoring & Observability](#-monitoring--observability)
- [🔌 Integrations](#-integrations)
- [🛠️ Operations & Maintenance](#️-operations--maintenance)
- [🔄 CI/CD & Workflows](#-cicd--workflows)
- [📖 API Documentation](#-api-documentation)
- [🐛 Troubleshooting](#-troubleshooting)

---

## 🚀 Getting Started

**New to the project? Start here!**

| Document | Description |
|----------|-------------|
| [README](../README.md) | Main project overview and quick start |
| [CLAUDE.md](../CLAUDE.md) | Comprehensive codebase context for AI assistants |
| [Setup Guide](setup.md) | Local development setup instructions |
| [Complete Setup Guide](setup/COMPLETE_SETUP_GUIDE.md) | End-to-end setup walkthrough |
| [Environment Setup](setup/ENVIRONMENT_SETUP.md) | Environment variables and configuration |
| [Architecture Overview](architecture.md) | System architecture and design patterns |

### First-Time Setup Checklist

1. ✅ Clone repository
2. ✅ Install dependencies: `pip install -r requirements.txt`
3. ✅ Set up environment variables (see [Environment Setup](setup/ENVIRONMENT_SETUP.md))
4. ✅ Configure Supabase (see [Setup Guide](setup.md))
5. ✅ Run local server: `python src/main.py`
6. ✅ Test endpoints: `http://localhost:8000/health`

---

## ⚙️ Setup & Configuration

**Environment configuration and service integrations**

### Essential Setup

| Document | Description |
|----------|-------------|
| [Environment Variables](environment.md) | All environment variables explained |
| [API Key Setup](setup/API_KEY_SETUP.md) | API key generation and management |
| [Branch Protection Setup](setup/BRANCH_PROTECTION_SETUP.md) | GitHub branch protection rules |

### Service Authentication

| Document | Description |
|----------|-------------|
| [Google OAuth2 JWT Setup](setup/GOOGLE_OAUTH2_JWT.md) | JWT authentication for Google services |
| [Google OAuth2 Quick Start](setup/GOOGLE_OAUTH2_JWT_QUICKSTART.md) | Quick setup for Google authentication |
| [Google Vertex Setup](setup/GOOGLE_VERTEX_SETUP.md) | Google Vertex AI configuration |
| [Vertex AI Setup](setup/VERTEX_AI_SETUP.md) | Alternative Vertex AI setup guide |

---

## ✨ Features & Functionality

**Feature documentation and implementation guides**

### Core Features

| Document | Description |
|----------|-------------|
| [Activity Logging](features/ACTIVITY_LOGGING.md) | User activity tracking and audit logs |
| [Referral System](features/REFERRAL_SYSTEM.md) | Referral program implementation |
| [Stripe Integration](features/STRIPE.md) | Payment processing and subscriptions |
| [Statsig Feature Flags](features/STATSIG_FEATURE_FLAGS.md) | Feature flag management |
| [Release Tracking](features/RELEASE_TRACKING.md) | Version and release management |

### Model Management

| Document | Description |
|----------|-------------|
| [Model Health Overview](features/MODEL_HEALTH_OVERVIEW.md) | Model health tracking system |
| [Model Health Quick Start](features/MODEL_HEALTH_QUICK_START.md) | Quick setup for model health |
| [Model Health API Spec](features/MODEL_HEALTH_API_SPEC.md) | API specification |
| [Model Health UI Mockups](features/MODEL_HEALTH_UI_MOCKUPS.md) | Frontend UI designs |
| [Model Sync](features/MODEL_SYNC.md) | Model catalog synchronization |
| [Model Sync Quickstart](features/MODEL_SYNC_QUICKSTART.md) | Quick sync setup |
| [Private Models Filter](features/PRIVATE_MODELS_FILTER.md) | Filtering private models |
| [Health & Availability API](features/HEALTH_AVAILABILITY_API.md) | Model availability checking |

### Pricing System

| Document | Description |
|----------|-------------|
| [Pricing System Index](features/PRICING_SYSTEM_INDEX.md) | Main pricing documentation hub |
| [Pricing Quick Start](features/PRICING_QUICK_START.md) | Quick pricing setup |
| [Pricing Implementation Summary](features/PRICING_IMPLEMENTATION_SUMMARY.md) | Implementation details |
| [Pricing Audit System](features/PRICING_AUDIT_SYSTEM.md) | Audit and verification |
| [Pricing Audit Comparison](features/PRICING_AUDIT_DETAILED_COMPARISON.md) | Detailed comparison data |
| [GPT-5 Pricing Reference](features/GPT5_PRICING_REFERENCE.md) | GPT-5 specific pricing |
| [GPT-5.1 Implementation Guide](features/GPT51_IMPLEMENTATION_GUIDE.md) | GPT-5.1 integration |

### Automation

| Document | Description |
|----------|-------------|
| [Auto-Merge Implementation](features/AUTO_MERGE_IMPLEMENTATION.md) | Automated PR merging |
| [Auto-Merge Quick Start](features/AUTO_MERGE_QUICK_START.md) | Quick setup for auto-merge |
| [Legacy API Key Migration](features/LEGACY_API_KEY_MIGRATION.md) | Migrating old API keys |

---

## 🚢 Deployment

**Production deployment guides and configurations**

### Railway Deployment

| Document | Description |
|----------|-------------|
| [Railway Quick Start](deployment/RAILWAY_QUICKSTART.md) | ⭐ Start here for Railway |
| [Railway Setup Checklist](deployment/RAILWAY_SETUP_CHECKLIST.md) | Step-by-step checklist |
| [Railway Initial Setup](deployment/RAILWAY_INITIAL_SETUP.md) | First-time Railway setup |
| [Railway Deployment Guide](deployment/RAILWAY_DEPLOYMENT.md) | Complete deployment guide |
| [Railway Autonomous Deployment](deployment/RAILWAY_AUTONOMOUS_DEPLOYMENT.md) | Automated deployments |
| [Railway Error Monitoring](deployment/RAILWAY_ERROR_MONITORING_WORKFLOW.md) | Error monitoring setup |
| [Railway Prometheus Setup](deployment/RAILWAY_PROMETHEUS_SETUP.md) | Metrics with Prometheus |

### Auto-Deployment

| Document | Description |
|----------|-------------|
| [Auto-Deploy Quick Start](deployment/AUTO_DEPLOY_QUICKSTART.md) | ⭐ Quick deployment setup |
| [Auto-Deployment Architecture](deployment/AUTO_DEPLOYMENT_ARCHITECTURE.md) | Architecture overview |
| [Auto-Deployment Setup](deployment/AUTO_DEPLOYMENT_SETUP.md) | Detailed setup guide |
| [Auto-Deployment Getting Started](deployment/AUTO_DEPLOYMENT_GETTING_STARTED.md) | Getting started guide |

### Other Platforms

| Document | Description |
|----------|-------------|
| [Vercel Deployment](deployment/VERCEL_DEPLOYMENT.md) | Serverless deployment on Vercel |
| [General Deployment Guide](deployment/DEPLOYMENT.md) | Platform-agnostic deployment |
| [Deploy Migrations](deployment/DEPLOY_MIGRATIONS.md) | Database migration deployment |

---

## 📊 Monitoring & Observability

**Monitoring, logging, and performance tracking**

### Quick Starts

| Document | Description |
|----------|-------------|
| [Observability Quick Start](monitoring/OBSERVABILITY_QUICKSTART.md) | ⭐ Start here for monitoring |
| [Error Monitoring Quick Start](monitoring/ERROR_MONITORING_QUICKSTART.md) | Quick error tracking setup |
| [Performance Monitoring Quick Start](monitoring/PERFORMANCE_MONITORING_QUICKSTART.md) | Quick performance setup |

### Error Tracking

| Document | Description |
|----------|-------------|
| [Error Monitoring](monitoring/ERROR_MONITORING.md) | Complete error monitoring guide |
| [PostHog Error Tracking](monitoring/POSTHOG_ERROR_TRACKING.md) | PostHog integration |
| [Sentry Error Capture](monitoring/SENTRY_ERROR_CAPTURE_EXPANSION.md) | Sentry implementation |
| [Sentry Quick Reference](monitoring/SENTRY_ERROR_CAPTURE_QUICK_REF.md) | Sentry quick reference |

### Performance Monitoring

| Document | Description |
|----------|-------------|
| [Performance Monitoring](monitoring/PERFORMANCE_MONITORING.md) | Performance tracking system |
| [Performance Optimization](monitoring/PERFORMANCE_OPTIMIZATION.md) | Optimization strategies |

### Observability Platforms

| Document | Description |
|----------|-------------|
| [OpenTelemetry Setup](monitoring/OPENTELEMETRY_SETUP.md) | OpenTelemetry integration |
| [Prometheus Setup](monitoring/PROMETHEUS_SETUP.md) | Prometheus metrics |
| [Grafana Setup](monitoring/GRAFANA_FASTAPI_OBSERVABILITY_SETUP.md) | Grafana dashboards |

---

## 🔌 Integrations

**Third-party service integrations**

| Document | Description |
|----------|-------------|
| [Integration Guide](integrations/INTEGRATION_GUIDE.md) | General integration guide |
| [Alibaba Cloud Integration](integrations/ALIBABA_CLOUD_INTEGRATION.md) | Alibaba Cloud setup |
| [Google Vertex Migration](integrations/GOOGLE_VERTEX_MIGRATION.md) | Migrating to Vertex AI |
| [Frontend Model Health Integration](integrations/FRONTEND_MODEL_HEALTH_INTEGRATION.md) | Frontend integration guide |

---

## 🛠️ Operations & Maintenance

**Day-to-day operations and maintenance**

| Document | Description |
|----------|-------------|
| [Operations Guide](operations.md) | Operational procedures |
| [Auth Timeout Fixes](AUTH_TIMEOUT_FIXES.md) | Authentication timeout issues |

---

## 🔄 CI/CD & Workflows

**Continuous Integration and Deployment workflows**

### Workflow Testing

| Document | Description |
|----------|-------------|
| [Testing Workflows Locally](TESTING_WORKFLOWS_LOCALLY.md) | ⭐ Test with `act` locally |
| [Testing Workflows Quick Guide](../.github/TESTING_WORKFLOWS.md) | Quick reference card |

### Database Migrations

| Document | Description |
|----------|-------------|
| [Supabase Migrations CI](SUPABASE_MIGRATIONS_CI.md) | ⭐ Automated migrations setup |
| [Supabase Migrations Setup](../.github/SUPABASE_MIGRATIONS_SETUP.md) | Setup checklist |

### API Testing

| Document | Description |
|----------|-------------|
| [Apidog Runner Quick Start](APIDOG_RUNNER_QUICKSTART.md) | ⭐ API testing quick start |
| [Apidog Runner Setup](APIDOG_RUNNER_SETUP.md) | Detailed setup guide |
| [Apidog Runner Summary](APIDOG_RUNNER_IMPLEMENTATION_SUMMARY.md) | Implementation summary |

---

## 📖 API Documentation

**API endpoints and usage**

| Document | Description |
|----------|-------------|
| [API Reference](api.md) | Complete API documentation |
| [Architecture](architecture.md) | API architecture and design |

---

## 🐛 Troubleshooting

**Common issues and solutions**

| Document | Description |
|----------|-------------|
| [Troubleshooting Guide](troubleshooting.md) | Common issues and fixes |
| [Auth Timeout Fixes](AUTH_TIMEOUT_FIXES.md) | Authentication timeouts |

---

## 📦 Archive

**Historical documentation (for reference)**

| Document | Description |
|----------|-------------|
| [Implementation Summary](archive/IMPLEMENTATION_SUMMARY.md) | Historical implementation notes |

---

## 🔍 Search Tips

**Finding what you need:**

- **Setup issues?** → Check [Setup & Configuration](#️-setup--configuration)
- **New feature?** → Check [Features & Functionality](#-features--functionality)
- **Deploying?** → Check [Deployment](#-deployment) (start with Quick Starts)
- **Errors/monitoring?** → Check [Monitoring & Observability](#-monitoring--observability)
- **CI/CD issues?** → Check [CI/CD & Workflows](#-cicd--workflows)
- **API questions?** → Check [API Documentation](#-api-documentation)
- **Something broken?** → Check [Troubleshooting](#-troubleshooting)

---

## 🚀 Quick Links

**Most frequently accessed documents:**

1. [Testing Workflows Locally](TESTING_WORKFLOWS_LOCALLY.md) - Test GitHub Actions with `act`
2. [Supabase Migrations CI](SUPABASE_MIGRATIONS_CI.md) - Automated database migrations
3. [Railway Quick Start](deployment/RAILWAY_QUICKSTART.md) - Deploy to Railway
4. [Observability Quick Start](monitoring/OBSERVABILITY_QUICKSTART.md) - Set up monitoring
5. [Complete Setup Guide](setup/COMPLETE_SETUP_GUIDE.md) - Full development setup
6. [Model Health Quick Start](features/MODEL_HEALTH_QUICK_START.md) - Model health tracking
7. [Pricing Quick Start](features/PRICING_QUICK_START.md) - Pricing system setup
8. [API Reference](api.md) - API endpoints documentation

---

## 🤝 Contributing

**Adding new documentation?**

1. Create your `.md` file in the appropriate subdirectory:
   - `docs/setup/` - Setup and configuration
   - `docs/features/` - Feature documentation
   - `docs/deployment/` - Deployment guides
   - `docs/monitoring/` - Monitoring and observability
   - `docs/integrations/` - Third-party integrations

2. Add your document to this wiki index
3. Follow the existing documentation format
4. Include code examples where applicable
5. Add a "Quick Start" section for complex topics

---

## 📝 Documentation Standards

**When creating new docs:**

- ✅ Use clear, descriptive titles
- ✅ Include a table of contents for long docs
- ✅ Add code examples with syntax highlighting
- ✅ Provide both detailed and quick-start versions
- ✅ Include troubleshooting sections
- ✅ Keep the wiki index updated
- ✅ Cross-reference related documents

---

## 📊 Documentation Statistics

- **Total Documents**: 76+ markdown files
- **Categories**: 10 main sections
- **Quick Starts**: 15+ quick reference guides
- **Setup Guides**: 12+ configuration guides
- **Feature Docs**: 25+ feature implementations
- **Deployment Guides**: 13+ platform-specific guides

---

## 🔗 External Resources

- **GitHub Repository**: [gatewayz-backend](https://github.com/Alpaca-Network/gatewayz-backend)
- **Supabase Dashboard**: [supabase.com/dashboard](https://supabase.com/dashboard)
- **Railway Dashboard**: [railway.app](https://railway.app)
- **Statsig Dashboard**: [statsig.com](https://statsig.com)
- **PostHog Dashboard**: [posthog.com](https://posthog.com)

---

**Last Updated**: 2025-11-26
**Maintained By**: Gatewayz Development Team
**Questions?** Open an issue or consult the relevant documentation section above.
