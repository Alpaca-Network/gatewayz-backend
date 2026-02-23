#!/bin/bash
# Script to create GitHub issues for Gatewayz Backend
# Usage: GH_TOKEN=<your-token> ./scripts/create-issues.sh
# Or: gh auth login first, then ./scripts/create-issues.sh

set -euo pipefail

REPO="Alpaca-Network/gatewayz-backend"

echo "Creating Issue #1: Fix Code Quality Checks..."

gh issue create \
  --repo "$REPO" \
  --title "Fix all code quality checks so lint CI workflow passes" \
  --label "code-quality,ci,chore" \
  --body "$(cat <<'EOF'
## Summary

The `Lint & Code Quality` CI workflow (`.github/workflows/lint.yml`) currently fails on PRs to `main`/`develop`. This issue tracks fixing **all** code quality violations so that Ruff, Black, and isort checks pass cleanly.

## Background

The lint workflow runs 4 tools:
1. **Ruff** — linter with rules: `E`, `W`, `F`, `I`, `B`, `C4`, `UP`
2. **Black** — code formatter (line-length 100, target py312)
3. **isort** — import sorting (profile: black)
4. **MyPy** — type checker (non-blocking, warnings only)

All config is centralized in `pyproject.toml`. Ruff currently has **23 ignored rules** for pre-existing violations and multiple **per-file ignores** across modules like `auth.py`, `catalog.py`, `pricing_audit_service.py`, `redis_metrics.py`, and others.

## Scope of Work

### Phase 1: Auto-fixable Issues
- [ ] Run `isort src/ tests/ --profile black` to fix all import ordering
- [ ] Run `black src/ tests/ --line-length 100 --target-version py312` to fix all formatting
- [ ] Run `ruff check src/ tests/ --fix` to auto-fix safe lint violations
- [ ] Verify no functional changes were introduced by auto-fixers

### Phase 2: Manual Fixes
- [ ] Fix remaining Ruff violations that cannot be auto-fixed
- [ ] Address `flake8-bugbear` (`B`) warnings (mutable default args, exception handling, etc.)
- [ ] Address `flake8-comprehensions` (`C4`) issues
- [ ] Address `pyupgrade` (`UP`) modernization suggestions
- [ ] Fix `pycodestyle` (`E`/`W`) issues not handled by Black

### Phase 3: Tighten Configuration
- [ ] Review and reduce the 23 ignored Ruff rules — re-enable rules where violations have been fixed
- [ ] Review per-file ignores and remove entries that are no longer needed
- [ ] Create `scripts/lint.sh` (referenced in `lint-pr-review.yml` but currently missing)
- [ ] Confirm the full CI pipeline passes: push to a branch and verify green checks

### Phase 4: Prevention
- [ ] Verify the `lint-autofix.yml` weekly workflow is functional
- [ ] Consider adding a `.pre-commit-config.yaml` for local enforcement
- [ ] Document the lint setup in contributing guidelines

## Acceptance Criteria

- [ ] `ruff check src/ tests/ --output-format=github` exits 0
- [ ] `black src/ tests/ --check --line-length 100 --target-version py312` exits 0
- [ ] `isort src/ tests/ --check-only --profile black` exits 0
- [ ] The `Lint & Code Quality` workflow passes on a PR to `develop`
- [ ] No functional regressions introduced

## Files & Config Reference

| File | Purpose |
|------|---------|
| `.github/workflows/lint.yml` | Main CI lint checks (Ruff, Black, isort, MyPy) |
| `.github/workflows/lint-pr-review.yml` | PR inline annotations & auto-comments |
| `.github/workflows/lint-autofix.yml` | Weekly auto-fix & PR creation |
| `pyproject.toml` | All tool configs (`[tool.ruff]`, `[tool.black]`, `[tool.isort]`, `[tool.mypy]`) |
EOF
)"

echo ""
echo "Creating Issue #2: Pricing System & Token Deduction Audit..."

gh issue create \
  --repo "$REPO" \
  --title "Complete audit of pricing system, token deduction, and usage tracking" \
  --label "billing,audit,high-priority" \
  --body "$(cat <<'EOF'
## Summary

Conduct a thorough end-to-end audit of the entire pricing system, credit/token deduction pipeline, and usage tracking to identify and fix inaccuracies, inconsistencies, and edge cases that could lead to revenue loss or incorrect billing.

## Motivation

The pricing and billing system has grown to span 10+ files across services, database layers, and configuration. Multiple pricing lookup strategies, disabled validations, known race conditions, and two separate pricing systems that can return different prices for the same model all present risk. A systematic audit is needed to ensure billing accuracy and data integrity.

## Architecture Overview

```
Request → Token Counting → Cost Calculation → Credit Deduction → Audit Logging
   ↓            ↓                ↓                    ↓                 ↓
chat.py   provider response  pricing.py         db/users.py       credit_transactions.py
          (prompt_tokens,    (5-tier fallback    (optimistic        (immutable audit
           completion_tokens  + validation)       locking)           trail)
           total_tokens)
```

## Key Files in Scope

| File | Role | Lines |
|------|------|-------|
| `src/services/pricing.py` | Main pricing lookup (5-tier fallback), cost calculation | ~512 |
| `src/services/pricing_lookup.py` | Manual JSON + DB pricing queries | ~673 |
| `src/services/pricing_normalization.py` | Per-token format standardization (30+ providers) | ~317 |
| `src/services/pricing_validation.py` | Bounds/spike validation (**currently disabled**) | ~416 |
| `src/services/credit_handler.py` | Centralized credit/trial handling, retries | ~700 |
| `src/services/daily_usage_limiter.py` | Daily usage caps ($1/day default) | ~181 |
| `src/db/users.py` | Credit deduction (dual-field), balance management | ~836 |
| `src/db/credit_transactions.py` | Immutable transaction audit trail | ~634 |
| `src/db/chat_completion_requests_enhanced.py` | Request cost tracking & backfill | ~250 |
| `src/config/usage_limits.py` | Trial/daily limit constants | ~50 |

## Identified Issues to Investigate

### Critical (Revenue Risk)

- [ ] **Two pricing systems returning different prices**: `pricing.py` uses `model_id` (canonical); `pricing_lookup.py` uses `gateway` + `model_id`. These are not consistently cross-referenced and can return different prices for the same model.
- [ ] **Pricing bounds validation is DISABLED**: `pricing_validation.py` min/max bounds and spike checks were temporarily disabled (2026-02-03) for "database migration." This allows pricing errors to propagate to production. TODO comment says "Re-enable after initial pricing sync completes" — needs resolution.
- [ ] **Streaming request billing failures swallowed**: When streaming, the response is already sent before credit deduction (background task). If deduction fails, it's only logged — no retry queue, no automatic recovery.
- [ ] **Trial flag override logic can misfire**: `credit_handler.py` checks 4 subscription indicators and overrides trial status if 1+ match. Could charge paid users incorrectly if stale trial flag + partial subscription data.
- [ ] **Default pricing used with no cap**: Unknown models fall back to $0.00002/token with a Sentry alert, but there's no limit on how many requests use default pricing before blocking.

### High Priority (Data Integrity)

- [ ] **No request-to-deduction mapping**: `chat_completion_requests` has `cost_usd` but is NOT linked to `credit_transactions`. Can't trace which request caused which deduction. Reconciliation requires manual correlation.
- [ ] **Free requests have no audit trail**: Deductions < $0.000001 are skipped (`db/users.py` ~line 714). These orphaned tokens get no logging and could be exploited.
- [ ] **Format detection is heuristic-based**: `pricing_normalization.py` uses price magnitude thresholds to auto-detect per-token vs per-1M format. Can misclassify by 1000x.
- [ ] **Provider pricing format config contradicts comments**: `PROVIDER_PRICING_FORMATS` has entries where the comment says one thing but the config says another (e.g., Vercel gateway: comment says "returns per-token" but configured as `PER_1M_TOKENS`).
- [ ] **No pricing versioning**: No timestamps on pricing records. Can't detect or audit when prices changed.

### Medium Priority (Operational)

- [ ] **Daily usage limit race condition**: Known issue documented in code (`db/users.py` ~line 768). Concurrent requests can bypass the $1/day limit because the check is not atomic with deduction.
- [ ] **No token count validation**: Token counts come directly from provider responses with no cross-validation or sanity checks.
- [ ] **Subscription allowance always depleted first**: No logic for allowance expiry dates. Purchased credits held indefinitely while allowance depletes.
- [ ] **Admin bypass skips all audit logging**: Admin-tier users skip credit deduction entirely with no record.
- [ ] **Redis metrics not transactional with DB**: Metrics recorded after DB update — if Redis fails, metrics are missing; if deduction fails after metrics, they're phantom entries.

## Audit Deliverables

### Phase 1: Discovery & Documentation
- [ ] Map the complete billing flow from request to transaction for all endpoint types (chat, messages, images)
- [ ] Document all pricing data sources and their precedence
- [ ] Identify all code paths where credits can be deducted, skipped, or lost
- [ ] Catalog every hardcoded pricing value and magic number

### Phase 2: Validation & Fixes
- [ ] Re-enable pricing bounds validation with correct thresholds
- [ ] Fix the two-pricing-system inconsistency (single source of truth)
- [ ] Add request-to-transaction linking (foreign key or correlation ID)
- [ ] Add audit trail for sub-threshold requests (currently orphaned)
- [ ] Fix provider pricing format contradictions (verify with actual API responses)
- [ ] Add pricing version tracking (timestamp on pricing records)

### Phase 3: Safety & Monitoring
- [ ] Add reconciliation tooling: compare `chat_completion_requests.cost_usd` totals against `credit_transactions` totals
- [ ] Add alerting for default-pricing usage exceeding threshold
- [ ] Add retry queue for failed streaming credit deductions
- [ ] Add integration tests for the full billing flow (request → tokens → cost → deduction → audit)
- [ ] Document the cost reconciliation procedure (SOP)

### Phase 4: Edge Cases & Hardening
- [ ] Handle free models from all providers (not just OpenRouter `:free` suffix)
- [ ] Add minimum token sanity checks
- [ ] Address the daily limit race condition (Redis-based atomic check-and-deduct)
- [ ] Add subscription allowance expiry logic
- [ ] Validate no integer overflow on large token counts * price

## Acceptance Criteria

- [ ] All pricing lookups return consistent results regardless of entry point
- [ ] Pricing validation re-enabled with production-safe thresholds
- [ ] Every credit deduction has a traceable link to its originating request
- [ ] Reconciliation report shows 0 discrepancy between request costs and transaction amounts
- [ ] Failed deductions have automatic retry mechanism
- [ ] Integration test suite covers the full billing pipeline
- [ ] No requests use default pricing without alerting + eventual blocking
EOF
)"

echo ""
echo "Done! Both issues created successfully."
