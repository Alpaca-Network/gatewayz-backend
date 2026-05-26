# Cost Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut ~$250–300/mo SaaS+infra spend on gatewayz-backend by removing redundant observability backends, caching the `/models` catalog, converting the always-on health service to a cron job, lowering log volume, consolidating rate limiters, and trimming Vercel cold-start cost.

**Architecture:** Pure subtractive + caching. No new features, no schema changes (except optional Redis TTL keys). Sentry stays for errors; PostHog stays for analytics; Prometheus stays for metrics. Everything else (Pyroscope, Loki, Tempo, Arize, Braintrust) is removed or hard-defaulted off. `/models` results cached in Redis for 1h. Health-service container becomes a 30-min GitHub Actions cron.

**Tech Stack:** Python 3.10–3.12, FastAPI 0.115, Supabase, Redis 5, Sentry SDK, PostHog SDK, Prometheus client, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-05-25-cost-reduction-design.md`

**Parallelization:** Categories A, B, D, E, F, G are independent file sets — they can run as parallel team tasks. Category C (health-service) touches `health-service/` exclusively and is also independent.

---

## File Structure

| Category | Purpose | Files |
|----------|---------|-------|
| A | Remove redundant observability | `src/services/startup.py`, `src/routes/chat.py`, delete `src/services/pyroscope_config.py`, `src/services/tempo_otlp.py`, `src/services/braintrust_service.py`, `src/handlers/braintrust_logging.py`, `requirements.txt` |
| B | `/models` columnar select + Redis cache | `src/db/models_catalog_db.py`, `src/services/cache/model_catalog_cache.py`, `src/routes/catalog.py` |
| C | Health service → cron | `health-service/main.py`, `health-service/railway.toml`, `.github/workflows/health-monitor.yml` |
| D | Log volume | `src/config/logging_config.py`, `src/config/config.py` |
| E | Rate-limiter unification | `src/services/auth_rate_limiting.py` |
| F | Vercel cold start | `src/main.py`, `api/index.py` |
| G | Requirements prune | `requirements.txt`, `pyproject.toml` |

---

## Task A1: Remove Braintrust from chat route

**Files:**
- Modify: `src/routes/chat.py` (imports lines ~66, 72–120; usages lines ~1513, 3187–3205)
- Modify: `src/services/startup.py` (lines ~797–798)
- Delete: `src/services/braintrust_service.py`
- Delete: `src/handlers/braintrust_logging.py`
- Modify: `requirements.txt` (remove `braintrust==0.1.0`)

- [ ] **Step 1: Inspect every Braintrust call site**

Run: `grep -n "braintrust\|Braintrust\|traced" src/routes/chat.py`
Expected output: lines around 66, 72–120 (imports + fallback no-op defs), 1513 (`logger.start_span`), 3187–3205 (`log_to_braintrust`).

- [ ] **Step 2: Replace `@traced` and span context with no-op**

In `src/routes/chat.py`, remove the conditional `try/except ImportError` block (lines ~72–120) and replace with:

```python
# Braintrust removed for cost reduction (see docs/superpowers/specs/2026-05-25-cost-reduction-design.md)
def traced(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]
    def _wrap(fn):
        return fn
    return _wrap

def check_braintrust_available():
    return False

def braintrust_flush():
    return None
```

Remove the `with logger.start_span(...)` block around line 1513 — replace with direct execution of its body.

Remove the entire `await log_to_braintrust(...)` call at line 3187 and its arguments (lines 3187–3205).

Remove `from src.handlers.braintrust_logging import log_to_braintrust` (line 66).

- [ ] **Step 3: Remove init from startup.py**

In `src/services/startup.py`, delete the block at lines 797–798:

```python
from src.services.braintrust_service import initialize_braintrust
if initialize_braintrust(project="Gatewayz Backend"):
```

Replace with nothing (delete the surrounding `if BRAINTRUST_ENABLED:` if present).

- [ ] **Step 4: Delete files**

```bash
git rm src/services/braintrust_service.py src/handlers/braintrust_logging.py
```

- [ ] **Step 5: Remove from requirements**

In `requirements.txt`, delete the lines:

```
# Braintrust pinned to prevent version resolution issues
braintrust==0.1.0
```

- [ ] **Step 6: Verify no remaining references**

Run: `grep -rn "braintrust\|Braintrust" src/ tests/ 2>/dev/null | grep -v __pycache__`
Expected: zero hits.

- [ ] **Step 7: Boot-test the app**

Run: `python -c "from src.main import create_app; app = create_app(); print('OK')"`
Expected: `OK` (no ImportError).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: remove Braintrust integration (cost reduction)"
```

---

## Task A2: Remove Pyroscope

**Files:**
- Modify: `src/services/startup.py` (lines 57–95 status block + 284–286 init + 890–892 shutdown)
- Delete: `src/services/pyroscope_config.py`
- Modify: `requirements.txt` (remove pyroscope dep if present)

- [ ] **Step 1: Find Pyroscope references**

Run: `grep -rn "pyroscope\|Pyroscope" src/ requirements.txt 2>/dev/null`
Expected: hits in startup.py (lines 57, 79, 91, 284, 890), pyroscope_config.py, possibly requirements.txt.

- [ ] **Step 2: Remove from startup.py**

In `src/services/startup.py`:
- Delete lines that read `PYROSCOPE_ENABLED` env var (~line 57–58).
- Delete the status line for Pyroscope in the startup banner (~line 79, 91).
- Delete the init block (~lines 284–286).
- Delete the shutdown block (~lines 890–892).

- [ ] **Step 3: Delete file**

```bash
git rm src/services/pyroscope_config.py
```

- [ ] **Step 4: Remove dep**

Run: `grep -n "pyroscope" requirements.txt pyproject.toml`
If found, delete those lines.

- [ ] **Step 5: Verify**

Run: `grep -rn "pyroscope\|Pyroscope" src/ requirements.txt pyproject.toml 2>/dev/null | grep -v __pycache__`
Expected: zero hits.

- [ ] **Step 6: Boot-test + commit**

```bash
python -c "from src.main import create_app; create_app(); print('OK')"
git add -A
git commit -m "feat: remove Pyroscope profiling (cost reduction)"
```

---

## Task A3: Remove Tempo OTLP exporter

**Files:**
- Modify: `src/services/startup.py` (line 27 import, ~273 `init_tempo_otlp_fastapi(app)`, ~299–347 background init)
- Delete: `src/services/tempo_otlp.py`

- [ ] **Step 1: Find references**

Run: `grep -rn "tempo_otlp\|init_tempo\|TEMPO_" src/ 2>/dev/null | grep -v __pycache__`

- [ ] **Step 2: Remove from startup.py**

Delete:
- Line 27: `from src.services.tempo_otlp import init_tempo_otlp_fastapi`
- Status banner refs (~lines 63–64, 82, 95).
- Init call at ~line 273 (`init_tempo_otlp_fastapi(app)`).
- Background init function and its scheduling (~lines 299–347 — `async def init_tempo_exporter_background` and `_create_background_task(init_tempo_exporter_background(), ...)`).

- [ ] **Step 3: Delete file**

```bash
git rm src/services/tempo_otlp.py
```

- [ ] **Step 4: Remove TEMPO_* from Config**

Run: `grep -n "TEMPO_" src/config/config.py`. Delete those constants.

- [ ] **Step 5: Verify + commit**

```bash
grep -rn "tempo_otlp\|TEMPO_" src/ 2>/dev/null | grep -v __pycache__
# expected: zero hits

python -c "from src.main import create_app; create_app(); print('OK')"
git add -A
git commit -m "feat: remove Tempo OTLP exporter (cost reduction)"
```

---

## Task A4: Disable Arize by default

**Files:**
- Modify: `src/services/startup.py` (line 15 import, ~350–359 background init)
- Modify: `src/config/arize_config.py` (make `init_arize_otel()` early-return when disabled)
- Modify: `src/config/config.py` (default `ARIZE_ENABLED=false`)

- [ ] **Step 1: Find Arize gating in config**

Run: `grep -n "ARIZE" src/config/config.py src/config/arize_config.py`

- [ ] **Step 2: Set default to false**

In `src/config/config.py`, ensure `ARIZE_ENABLED` defaults to `False`. If a constant exists, set it; if it's `os.getenv("ARIZE_ENABLED", "true")`, change to `os.getenv("ARIZE_ENABLED", "false")`.

- [ ] **Step 3: Early-return in arize_config.py**

In `src/config/arize_config.py`, at the top of `init_arize_otel()`:

```python
def init_arize_otel():
    from src.config.config import Config
    if not getattr(Config, "ARIZE_ENABLED", False):
        logger.info("Arize disabled; skipping init")
        return False
    # ... existing body ...
```

- [ ] **Step 4: Verify boot + commit**

```bash
python -c "from src.main import create_app; create_app(); print('OK')"
git add -A
git commit -m "feat: disable Arize by default (cost reduction)"
```

---

## Task A5: Disable Loki by default

**Files:**
- Modify: `src/config/config.py` (default `LOKI_ENABLED=false`)
- Modify: `src/config/logging_config.py` (lines ~420–425 Loki handler init)
- Modify: `requirements.txt` (remove `python-snappy` if only used by Loki)

- [ ] **Step 1: Find LOKI gating**

Run: `grep -n "LOKI" src/config/config.py src/config/logging_config.py`

- [ ] **Step 2: Default to false**

In `src/config/config.py`, change `LOKI_ENABLED` default to `False`.

- [ ] **Step 3: Check if python-snappy has other consumers**

Run: `grep -rn "import snappy\|from snappy" src/ tests/ 2>/dev/null | grep -v __pycache__`
If zero hits, remove from `requirements.txt`.

- [ ] **Step 4: Verify + commit**

```bash
python -c "from src.main import create_app; create_app(); print('OK')"
git add -A
git commit -m "feat: disable Loki by default (cost reduction)"
```

---

## Task B1: Add Redis cache wrapper around `/models` catalog

**Files:**
- Modify: `src/db/models_catalog_db.py` (lines 102–121 `get_all_models_for_catalog`)
- Modify: `src/services/cache/model_catalog_cache.py` (ensure get/set/invalidate exist)
- Test: `tests/db/test_models_catalog_db_cache.py` (create)

- [ ] **Step 1: Read existing cache module**

Run: `cat src/services/cache/model_catalog_cache.py | head -80`

Confirm there are `get_catalog()`, `set_catalog()`, `invalidate()` functions. If they don't exist with these names, note actual names and use them in step 3.

- [ ] **Step 2: Write the failing test**

Create `tests/db/test_models_catalog_db_cache.py`:

```python
from unittest.mock import patch, MagicMock
import pytest


def test_get_all_models_for_catalog_uses_cache_on_second_call():
    """Second call within TTL should not hit Supabase."""
    fake_rows = [{"id": 1, "model_name": "test"}]

    with patch("src.db.models_catalog_db.get_client_for_query") as mock_client, \
         patch("src.services.cache.model_catalog_cache.get_catalog") as mock_get, \
         patch("src.services.cache.model_catalog_cache.set_catalog") as mock_set:

        mock_get.side_effect = [None, fake_rows]  # miss then hit

        chain = MagicMock()
        chain.execute.return_value = MagicMock(data=fake_rows)
        mock_client.return_value.table.return_value.select.return_value.order.return_value.range.return_value = chain

        from src.db.models_catalog_db import get_all_models_for_catalog
        a = get_all_models_for_catalog()
        b = get_all_models_for_catalog()

        assert a == fake_rows
        assert b == fake_rows
        # Supabase touched at most once
        assert mock_client.call_count <= 1
        mock_set.assert_called_once()
```

- [ ] **Step 3: Run test (expect FAIL)**

Run: `pytest tests/db/test_models_catalog_db_cache.py -v`
Expected: FAIL (no cache lookup currently).

- [ ] **Step 4: Wire cache into `get_all_models_for_catalog`**

In `src/db/models_catalog_db.py`, replace lines 102–121 with:

```python
def get_all_models_for_catalog(
    provider_id: int | None = None,
    is_active_only: bool = True,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    try:
        from src.services.cache.model_catalog_cache import get_catalog, set_catalog
        cache_key = f"catalog:v1:p={provider_id}:active={is_active_only}:l={limit}:o={offset}"
        cached = get_catalog(cache_key)
        if cached is not None:
            return cached

        supabase = get_client_for_query(read_only=True)
        query = supabase.table("models").select(
            "id,model_name,display_name,provider_id,context_length,"
            "input_cost,output_cost,modality,is_active,source_gateway,"
            "providers!inner(slug,name,site_url)"
        )
        if provider_id:
            query = query.eq("provider_id", provider_id)
        if is_active_only:
            query = query.eq("is_active", True)
        query = query.order("model_name").range(offset, offset + limit - 1)
        response = query.execute()
        rows = response.data or []
        set_catalog(cache_key, rows, ttl_seconds=3600)
        return rows
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        return []
```

- [ ] **Step 5: Run test (expect PASS)**

Run: `pytest tests/db/test_models_catalog_db_cache.py -v`
Expected: PASS.

- [ ] **Step 6: Run broader catalog tests**

Run: `pytest tests/ -k catalog -v 2>&1 | tail -30`
Expected: existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/db/models_catalog_db.py tests/db/test_models_catalog_db_cache.py
git commit -m "feat: cache /models catalog in Redis with 1h TTL and column-scoped select"
```

---

## Task B2: Tighten remaining wildcard selects in catalog

**Files:**
- Modify: `src/db/models_catalog_db.py` (lines 139, 185, 230)

- [ ] **Step 1: Replace each `select("*, providers!inner(*)")` with column list**

In `src/db/models_catalog_db.py`, in `get_model_by_id` (line ~139), `get_models_by_provider_slug` (~185), and the function at ~230, replace:

```python
.select("*, providers!inner(*)")
```

with:

```python
.select(
    "id,model_name,display_name,provider_id,context_length,"
    "input_cost,output_cost,modality,is_active,source_gateway,"
    "providers!inner(slug,name,site_url)"
)
```

- [ ] **Step 2: Run catalog tests**

Run: `pytest tests/ -k "catalog or model" -v 2>&1 | tail -30`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/db/models_catalog_db.py
git commit -m "perf: column-scoped selects across model catalog queries"
```

---

## Task C1: Convert health-service from always-on to one-shot CLI

**Files:**
- Modify: `health-service/main.py`
- Delete: `health-service/railway.toml`
- Create: `.github/workflows/health-monitor.yml`

- [ ] **Step 1: Read current health-service main**

Run: `wc -l health-service/main.py && head -120 health-service/main.py`

- [ ] **Step 2: Convert FastAPI server to one-shot run**

At the bottom of `health-service/main.py`, replace any `uvicorn.run(...)` or `app = FastAPI(...)` startup with:

```python
import asyncio

async def run_once():
    """Run a single health-check pass and exit."""
    from src.services.monitoring.intelligent_health_monitor import run_tiered_check
    await run_tiered_check()

if __name__ == "__main__":
    asyncio.run(run_once())
```

If `run_tiered_check` does not exist, search for the actual tiered-check entrypoint:

Run: `grep -n "def.*check\|async def.*tier" src/services/monitoring/intelligent_health_monitor.py | head`

Use whatever the actual entrypoint is — name it explicitly in the import.

- [ ] **Step 3: Delete railway.toml**

```bash
git rm health-service/railway.toml
```

- [ ] **Step 4: Create GitHub Actions cron**

Create `.github/workflows/health-monitor.yml`:

```yaml
name: Health Monitor (cron)

on:
  schedule:
    - cron: '*/30 * * * *'  # every 30 minutes
  workflow_dispatch:

jobs:
  health-check:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
      REDIS_URL: ${{ secrets.REDIS_URL }}
      ENVIRONMENT: production
      LOG_LEVEL: WARNING
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
      - run: pip install -r requirements.txt
      - run: python health-service/main.py
```

- [ ] **Step 5: Verify file parses as Python**

Run: `python -c "import ast; ast.parse(open('health-service/main.py').read()); print('OK')"`

- [ ] **Step 6: Verify workflow YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/health-monitor.yml')); print('OK')"`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: convert health-service to GitHub Actions cron (cost reduction)"
```

---

## Task D1: Default LOG_LEVEL=WARNING in production

**Files:**
- Modify: `src/config/logging_config.py` (lines 384, 395)

- [ ] **Step 1: Read current init**

Run: `sed -n '375,405p' src/config/logging_config.py`

- [ ] **Step 2: Make level env-driven with prod default WARNING**

In `src/config/logging_config.py`, near the top of `configure_logging()`, add:

```python
import os
_env = os.getenv("ENVIRONMENT", "development").lower()
_default_level = "WARNING" if _env == "production" else "INFO"
_level_name = os.getenv("LOG_LEVEL", _default_level).upper()
_level = getattr(logging, _level_name, logging.INFO)
```

Then replace `root_logger.setLevel(logging.INFO)` (line 384) with `root_logger.setLevel(_level)`, and `console_handler.setLevel(logging.INFO)` (line 395) with `console_handler.setLevel(_level)`.

- [ ] **Step 3: Test dev default**

Run: `python -c "import os; os.environ.pop('LOG_LEVEL', None); os.environ['ENVIRONMENT']='development'; from src.config.logging_config import configure_logging; import logging; configure_logging(); print(logging.getLogger().level)"`
Expected: `20` (INFO).

- [ ] **Step 4: Test prod default**

Run: `python -c "import os; os.environ.pop('LOG_LEVEL', None); os.environ['ENVIRONMENT']='production'; from src.config.logging_config import configure_logging; import logging; configure_logging(); print(logging.getLogger().level)"`
Expected: `30` (WARNING).

- [ ] **Step 5: Commit**

```bash
git add src/config/logging_config.py
git commit -m "feat: default LOG_LEVEL=WARNING in production (cost reduction)"
```

---

## Task E1: Consolidate auth rate limiter onto Redis

**Files:**
- Modify: `src/services/auth_rate_limiting.py`

- [ ] **Step 1: Read current implementation**

Run: `wc -l src/services/auth_rate_limiting.py && cat src/services/auth_rate_limiting.py`

- [ ] **Step 2: Rewrite as thin Redis-backed wrapper**

Replace contents of `src/services/auth_rate_limiting.py` with:

```python
"""Auth rate limiting — Redis-backed sliding window.

Previously held per-IP `deque` state in process memory (O(n) iteration per check,
GC pressure under high IP cardinality). Now delegates to the unified sliding
window in `services.rate_limiting` with a dedicated key prefix.
"""
from __future__ import annotations

import logging
from src.services.rate_limiting import sliding_window_check

logger = logging.getLogger(__name__)

# Defaults: 10 attempts per 60s per IP per action
AUTH_LIMIT = 10
AUTH_WINDOW_SECONDS = 60


def check_auth_rate_limit(ip: str, action: str = "login") -> tuple[bool, int]:
    """Return (allowed, remaining)."""
    key = f"authrl:{action}:{ip}"
    return sliding_window_check(key=key, limit=AUTH_LIMIT, window_seconds=AUTH_WINDOW_SECONDS)
```

- [ ] **Step 3: Confirm `sliding_window_check` exists in rate_limiting.py**

Run: `grep -n "def sliding_window_check\|def.*sliding_window" src/services/rate_limiting.py`

If the function exists with a different name (e.g. `check_sliding_window`), update the import in step 2 to match.

If no equivalent exists, add a thin wrapper to `src/services/rate_limiting.py`:

```python
def sliding_window_check(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Check sliding window limit. Returns (allowed, remaining)."""
    from src.config.redis_config import get_redis_client
    import time
    r = get_redis_client()
    if r is None:
        return True, limit  # fail open if Redis unavailable
    now = int(time.time() * 1000)
    window_start = now - window_seconds * 1000
    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)
    pipe.zcard(key)
    pipe.zadd(key, {str(now): now})
    pipe.expire(key, window_seconds + 1)
    _, count, _, _ = pipe.execute()
    remaining = max(0, limit - int(count) - 1)
    return int(count) < limit, remaining
```

- [ ] **Step 4: Run existing tests**

Run: `pytest tests/ -k "auth_rate or rate_limit" -v 2>&1 | tail -30`
Expected: PASS or skipped (Redis unavailable in CI is fine — fail-open path).

- [ ] **Step 5: Verify no `deque` left in auth_rate_limiting**

Run: `grep -n "deque" src/services/auth_rate_limiting.py`
Expected: zero hits.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: consolidate auth rate limiter onto Redis sliding window"
```

---

## Task F1: Skip non-critical init on Vercel cold start

**Files:**
- Modify: `src/main.py` (Sentry init lines 31–93)

- [ ] **Step 1: Read Sentry init**

Run: `sed -n '25,100p' src/main.py`

- [ ] **Step 2: Lower sample rate on Vercel**

Inside the Sentry init block in `src/main.py`, find `traces_sample_rate=...` and `profiles_sample_rate=...`. Wrap them so they read 0 on Vercel:

```python
import os
_on_vercel = bool(os.getenv("VERCEL"))
_traces_rate = 0.0 if _on_vercel else float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
_profiles_rate = 0.0 if _on_vercel else float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.05"))
```

Then pass `_traces_rate` and `_profiles_rate` to `sentry_sdk.init(...)`.

- [ ] **Step 3: Boot test**

Run: `python -c "import os; os.environ['VERCEL']='1'; from src.main import create_app; create_app(); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add src/main.py
git commit -m "perf: zero Sentry sample rates on Vercel (cold start, cost reduction)"
```

---

## Task G1: Confirm heavy provider SDKs are not in base install

**Files:**
- Modify: `requirements.txt` (move heavy SDKs to a comment block or remove)
- Modify: `pyproject.toml` (sync `[providers]` extras)

- [ ] **Step 1: List heavy deps**

Run: `grep -nE "google-cloud-aiplatform|clarifai|huggingface-hub|novita-client" requirements.txt`

- [ ] **Step 2: Check actual usage in code on the Vercel path**

Run: `grep -rn "google.cloud.aiplatform\|import clarifai\|from clarifai\|huggingface_hub\|novita_client" src/ 2>/dev/null | grep -v __pycache__ | head -20`

If usages are all inside lazy `def`-scoped imports inside provider clients (typical pattern), these SDKs are not needed for cold start.

- [ ] **Step 3: Comment out heavy deps in requirements.txt**

In `requirements.txt`, replace the heavy provider SDK lines with:

```
# Heavy provider SDKs — install via `pip install -e .[providers]` for Railway/full deploy.
# Excluded from base requirements to keep Vercel bundle small.
# google-cloud-aiplatform==1.62.0
# clarifai==11.1.0
# huggingface-hub==0.23.0
# novita-client==0.5.0
```

Confirm `pyproject.toml` `[project.optional-dependencies].providers` already lists these — if not, add them there.

- [ ] **Step 4: Verify app still imports**

Run: `python -c "from src.main import create_app; create_app(); print('OK')"`
Expected: `OK` (provider clients lazy-import).

- [ ] **Step 5: Run smoke tests**

Run: `pytest tests/ -k "smoke or unit" -v 2>&1 | tail -20`
Expected: PASS (or expected-skip).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pyproject.toml
git commit -m "build: move heavy provider SDKs to optional extras (cold-start, cost reduction)"
```

---

## Task H1: Final verification

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -x --ignore=tests/conceptual_model 2>&1 | tail -30`
Expected: PASS.

- [ ] **Step 2: Run conceptual model spec tests**

Run: `pytest tests/conceptual_model/ 2>&1 | tail -10`
Expected: prior `cm_verified` count unchanged; `cm_gap` count may decrease if any of these changes closed a gap, but not increase.

- [ ] **Step 3: Boot the app and exercise `/health`**

Run: `python src/main.py &` then `sleep 3 && curl -fsS http://localhost:8000/health && kill %1`
Expected: 200 OK response.

- [ ] **Step 4: Diff stat**

Run: `git diff main --stat 2>&1 | tail -30`
Capture the line/file count delta and include in the final PR description.

---

## Self-Review

1. **Spec coverage:** A (A1-A5), B (B1-B2), C (C1), D (D1), E (E1), F (F1), G (G1) — all spec categories present.
2. **Placeholder scan:** Searched for "TBD/TODO/etc" — none present.
3. **Type consistency:** Cache function names assume `get_catalog`/`set_catalog`/`invalidate` in `model_catalog_cache.py`. Task B1 step 1 explicitly verifies actual names before use. Task E1 step 3 explicitly verifies `sliding_window_check` exists in `rate_limiting.py` and adds it if missing.
