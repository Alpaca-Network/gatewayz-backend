# Bare Model ID Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `api.gatewayz.ai` accept a vendor-native model id (`claude-sonnet-4-6`) by resolving it to the catalog's canonical id (`anthropic/claude-sonnet-4-6`) at the admission boundary, and never answer a caller's unknown model with a 503 that blames us.

**Architecture:** Add one pure resolver (`resolve_catalog_model_id`) that maps an incoming model id to a canonical catalog id using, in precedence order: exact catalog hit → curated `model_aliases` row → unique suffix match over the deduped catalog. Call it once, at the top of `enforce_model_pricing_gate`, and write the resolved id back onto the request so pricing, routing, and billing all see the same canonical id. Re-grade the error taxonomy so an unresolvable id is a 400 that names the likely intended model, and 503 is reserved for a model the catalog *does* list but ops failed to price.

**Tech Stack:** Python 3.11 / FastAPI / pytest, Supabase (PostgREST) for `models` + `model_aliases`, existing in-process catalog caches.

**Spec:** `docs/superpowers/plans/2026-09-08-bare-model-id-resolution.md` (this document — the Problem and Evidence sections below are the spec) and the partner-facing bug report at https://claude.ai/code/artifact/b80c62e6-af4b-49c7-99ac-9b9d79df4d62

---

## Problem

FlashyOS's official CLI (`@flashyos/agent@0.16.1`) scaffolds Gatewayz clients with `model = 'claude-sonnet-4-6'`. We reported that as their typo. It is also our bug, and ours is the larger one: **the Anthropic API's own model ids are bare**, so any caller pointing the Anthropic SDK at our `/v1/messages` — the endpoint we sold Flashy Group on as "full Anthropic semantics" — sends a bare id and gets a 503.

Three defects, one root cause.

1. **No canonical resolution at admission.** `normalize_model_id_for_pricing` (`src/services/pricing/pricing.py:107`) reverses *provider-specific transforms* (`accounts/fireworks/models/X`, `@cf/...`); it does not map a vendor-native id to the catalog's `vendor/model` id. Nothing else does either.
2. **The rejection blames us.** An unresolved high-value id raises `ValueError` in `get_model_pricing` → `enforce_model_pricing_gate` returns **503 `service_unavailable` / `pricing_not_configured` / "Please contact support."** That text arrives on the caller's first request and reads as a Gatewayz outage.
3. **`HIGH_VALUE_MODEL_PATTERNS` has drifted, and exists twice.** The list at `src/services/pricing/pricing.py:688` (duplicated verbatim at `:931`) contains `claude-sonnet-4` but not `claude-sonnet-5`, `claude-opus-5`, `claude-haiku`, or `gemini-3`. That substring accident is the *only* reason `claude-sonnet-4-6` returns 503 while `claude-sonnet-5` returns 400 — two different codepaths for the same class of mistake.

## Evidence (probed against production 2026-09-07/08, 1-token requests)

| Endpoint | Model id sent | Result |
|---|---|---|
| `/v1/chat/completions` | `claude-sonnet-4-6` | **503** `pricing_not_configured` |
| `/v1/chat/completions` | `anthropic/claude-sonnet-4-6` | 200 |
| `/v1/chat/completions` | `claude-sonnet-5` | **400** `model_not_priced` |
| `/v1/chat/completions` | `totally-fake-model-xyz` | 400 `model_not_priced` |
| `/v1/chat/completions` | `gpt-4o-mini` (bare) | 429 rate limit — **cleared admission** |
| `/v1/messages` | `claude-sonnet-4-6` | **503** |
| `/v1/messages` | `claude-sonnet-4-5` | **503** |
| `/v1/messages` | `anthropic/claude-sonnet-4-6` | 200 |
| `/v1/messages` | `anthropic/claude-sonnet-5` | 200 |

Two things to read off that table. A bare **OpenAI** id clears admission while a bare **Anthropic** id 503s — the behavior is inconsistent by vendor, so "always prefix" is not a rule we actually enforce. And a genuinely nonexistent model and a real model named the vendor's way produce *different* status codes, with the worse code going to the more reasonable caller.

## Non-goals

- Fuzzy matching, edit distance, or "did you mean" over the full 11k-row catalog. Resolution is exact-suffix and refuses on ambiguity.
- Changing which models are priced, or any pricing value.
- Changing `models.canonical_id` semantics — that column feeds `model_quality_scores` joins (see `src/services/model_canonicalization.py:1-15`) and is out of scope.

## Global Constraints

- Python ≥ 3.11; `from __future__ import annotations` at the top of every new module (repo-wide convention).
- **Resolution must never widen access.** A resolved id is subject to every gate the literal id was: pricing, community-auth, subscription, rate limits. Resolution runs *before* `enforce_model_pricing_gate`, never instead of it.
- **Ambiguity fails closed.** Two or more catalog candidates for a bare id → 400 listing the candidates. Never guess.
- **Never bill under an unresolved id.** Whatever id the pricing gate admits is the id written back to `req.model`, so `calculate_cost` and the usage record use the same string.
- Cache reads only on the hot path — no new Supabase query per request.
- Error bodies keep the existing envelope shape: `{"error": {"message", "type", "code"}}` on `/v1/chat/completions`; `_anthropic_error(...)` on `/v1/messages`.
- Tests: `pytest`, no network, no live Supabase. Catalog and alias sources are monkeypatched.
- Commit style: `fix:` / `feat:` / `test:` prefix, one task per commit, no `Co-Authored-By` line.

---

### Task 1: The pure resolver

**Files:**
- Create: `src/services/model_resolution.py`
- Test: `tests/services/test_model_resolution.py`

**Interfaces:**
- Consumes: `src.services.cache.model_catalog_cache.get_cached_unique_models()` (returns `list[dict]` with an `id` key, or `None` when cold); `src.services.model_canonicalization.load_alias_map()` → `dict[str, str]`.
- Produces:
  - `class ModelResolution` — dataclass with fields `canonical_id: str | None`, `matched_by: str` (one of `"exact"`, `"alias"`, `"suffix"`, `"unresolved"`, `"ambiguous"`), `candidates: tuple[str, ...]`.
  - `def resolve_catalog_model_id(model_id: str) -> ModelResolution`
  - `def invalidate_resolution_index() -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_model_resolution.py
from __future__ import annotations

import pytest

from src.services import model_resolution
from src.services.model_resolution import resolve_catalog_model_id

CATALOG = [
    {"id": "anthropic/claude-sonnet-4-6"},
    {"id": "anthropic/claude-sonnet-5"},
    {"id": "openai/gpt-4o-mini"},
    {"id": "meta-llama/llama-3.1-8b-instruct"},
    {"id": "community/llama-3.1-8b-instruct"},
]


@pytest.fixture(autouse=True)
def _stub_sources(monkeypatch):
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: [r["id"] for r in CATALOG])
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    model_resolution.invalidate_resolution_index()
    yield
    model_resolution.invalidate_resolution_index()


def test_exact_catalog_id_passes_through_unchanged():
    r = resolve_catalog_model_id("anthropic/claude-sonnet-4-6")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6"
    assert r.matched_by == "exact"


def test_bare_vendor_id_resolves_to_the_single_catalog_match():
    r = resolve_catalog_model_id("claude-sonnet-4-6")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6"
    assert r.matched_by == "suffix"


def test_ambiguous_bare_id_refuses_and_reports_every_candidate():
    r = resolve_catalog_model_id("llama-3.1-8b-instruct")
    assert r.canonical_id is None
    assert r.matched_by == "ambiguous"
    assert r.candidates == ("community/llama-3.1-8b-instruct", "meta-llama/llama-3.1-8b-instruct")


def test_unknown_id_is_unresolved_with_no_candidates():
    r = resolve_catalog_model_id("totally-fake-model-xyz")
    assert r.canonical_id is None
    assert r.matched_by == "unresolved"
    assert r.candidates == ()


def test_resolution_is_case_insensitive_on_input_only():
    r = resolve_catalog_model_id("Claude-Sonnet-4-6")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6"


def test_free_suffix_is_preserved_through_resolution():
    r = resolve_catalog_model_id("claude-sonnet-4-6:free")
    assert r.canonical_id == "anthropic/claude-sonnet-4-6:free"


def test_curated_alias_beats_a_suffix_match(monkeypatch):
    monkeypatch.setattr(
        model_resolution, "_load_alias_map",
        lambda: {"llama-3.1-8b-instruct": "meta-llama/llama-3.1-8b-instruct"},
    )
    model_resolution.invalidate_resolution_index()
    r = resolve_catalog_model_id("llama-3.1-8b-instruct")
    assert r.canonical_id == "meta-llama/llama-3.1-8b-instruct"
    assert r.matched_by == "alias"


def test_empty_input_is_unresolved_and_does_not_raise():
    assert resolve_catalog_model_id("").canonical_id is None


def test_cold_catalog_leaves_the_id_untouched(monkeypatch):
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", list)
    model_resolution.invalidate_resolution_index()
    r = resolve_catalog_model_id("claude-sonnet-4-6")
    assert r.canonical_id is None
    assert r.matched_by == "unresolved"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gatewayz-backend && python -m pytest tests/services/test_model_resolution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.model_resolution'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/services/model_resolution.py
"""Resolve an incoming model id to a canonical catalog id.

Callers send vendor-native ids: the Anthropic SDK sends `claude-sonnet-4-6`,
not `anthropic/claude-sonnet-4-6`, and every scaffold generated against our
OpenAI-compatible surface does the same. Before this module, a bare Anthropic
id reached the pricing gate unresolved and was rejected as an *operator*
failure (503, "contact support") while a bare OpenAI id happened to route.

Resolution is deliberately conservative and fails closed: exact catalog hit,
then a curated `model_aliases` row, then a suffix match that must be UNIQUE.
Two candidates is a refusal, never a guess — guessing here routes a user's
prompt to a model they did not ask for and bills them for it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_FREE_SUFFIX = ":free"


@dataclass(frozen=True)
class ModelResolution:
    canonical_id: str | None
    matched_by: str
    candidates: tuple[str, ...] = field(default=())


_index_lock = threading.RLock()
_exact: set[str] | None = None
_by_suffix: dict[str, tuple[str, ...]] | None = None
_aliases: dict[str, str] | None = None


def _load_catalog_ids() -> list[str]:
    """Every canonical id in the deduped catalog. [] when the cache is cold."""
    from src.services.cache.model_catalog_cache import get_cached_unique_models

    rows = get_cached_unique_models() or []
    return [str(r["id"]) for r in rows if r.get("id")]


def _load_alias_map() -> dict[str, str]:
    from src.services.model_canonicalization import load_alias_map

    return load_alias_map()


def _build_index() -> None:
    global _exact, _by_suffix, _aliases
    ids = _load_catalog_ids()
    exact: set[str] = set()
    suffix: dict[str, list[str]] = {}
    for mid in ids:
        low = mid.lower()
        exact.add(low)
        bare = low.rsplit("/", 1)[-1]
        if bare != low:
            suffix.setdefault(bare, []).append(mid)
    _exact = exact
    _by_suffix = {k: tuple(sorted(v)) for k, v in suffix.items()}
    _aliases = _load_alias_map()


def _ensure_index() -> None:
    if _exact is None or _by_suffix is None or _aliases is None:
        with _index_lock:
            if _exact is None or _by_suffix is None or _aliases is None:
                _build_index()


def invalidate_resolution_index() -> None:
    """Drop the index; rebuilt lazily on the next resolve. Call on catalog sync."""
    global _exact, _by_suffix, _aliases
    with _index_lock:
        _exact = None
        _by_suffix = None
        _aliases = None


def resolve_catalog_model_id(model_id: str) -> ModelResolution:
    """Map `model_id` to a canonical catalog id, or explain why it can't."""
    if not model_id or not model_id.strip():
        return ModelResolution(None, "unresolved")

    raw = model_id.strip()
    free = raw.lower().endswith(_FREE_SUFFIX)
    core = raw[: -len(_FREE_SUFFIX)] if free else raw
    low = core.lower()

    def _finish(canonical: str, how: str) -> ModelResolution:
        return ModelResolution(canonical + (_FREE_SUFFIX if free else ""), how)

    _ensure_index()
    assert _exact is not None and _by_suffix is not None and _aliases is not None

    if low in _exact:
        return _finish(low, "exact")

    aliased = _aliases.get(low)
    if aliased:
        return _finish(aliased, "alias")

    candidates = _by_suffix.get(low, ())
    if len(candidates) == 1:
        logger.info("[MODEL_RESOLVE] '%s' -> '%s' (unique suffix)", raw, candidates[0])
        return _finish(candidates[0], "suffix")
    if len(candidates) > 1:
        logger.info("[MODEL_RESOLVE] '%s' is ambiguous across %s", raw, list(candidates))
        return ModelResolution(None, "ambiguous", candidates)

    return ModelResolution(None, "unresolved")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/gatewayz-backend && python -m pytest tests/services/test_model_resolution.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/services/model_resolution.py tests/services/test_model_resolution.py
git commit -m "feat(models): conservative bare-model-id resolver (exact/alias/unique-suffix)"
```

---

### Task 2: Resolve at the admission gate

**Files:**
- Modify: `src/security/inference_gates.py:22-88` (`enforce_model_pricing_gate`)
- Modify: `src/routes/chat.py:395-399` (call site)
- Test: `tests/security/test_inference_gates_resolution.py`

**Interfaces:**
- Consumes: `resolve_catalog_model_id` / `ModelResolution` from Task 1.
- Produces: `enforce_model_pricing_gate(model_id, request_id=None, api_key_mask=None) -> str` — **now returns the canonical id it admitted** (previously returned `None`). Callers must assign it back to `req.model`.

The gate returning the id (rather than mutating `req`) keeps it usable from `/v1/messages`, `/v1/images/generations` and any future endpoint that doesn't share the chat request object.

- [ ] **Step 1: Write the failing test**

```python
# tests/security/test_inference_gates_resolution.py
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.security import inference_gates
from src.services.model_resolution import ModelResolution


@pytest.fixture(autouse=True)
def _require_pricing(monkeypatch):
    from src.config import Config

    monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", True, raising=False)


def _stub(monkeypatch, resolution: ModelResolution, priced: set[str]):
    monkeypatch.setattr(
        inference_gates, "resolve_catalog_model_id", lambda _m: resolution
    )
    monkeypatch.setattr(
        "src.services.pricing.model_has_pricing", lambda m: m in priced
    )
    monkeypatch.setattr(
        "src.services.cache.model_capabilities_cache.is_free_model", lambda _m: False
    )


@pytest.mark.asyncio
async def test_bare_id_is_admitted_under_its_canonical_id(monkeypatch):
    _stub(
        monkeypatch,
        ModelResolution("anthropic/claude-sonnet-4-6", "suffix"),
        {"anthropic/claude-sonnet-4-6"},
    )
    admitted = await inference_gates.enforce_model_pricing_gate("claude-sonnet-4-6")
    assert admitted == "anthropic/claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_unresolvable_id_is_a_400_not_a_503(monkeypatch):
    _stub(monkeypatch, ModelResolution(None, "unresolved"), set())
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("totally-fake-model-xyz")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_not_found"
    assert "contact support" not in exc.value.detail["error"]["message"].lower()


@pytest.mark.asyncio
async def test_ambiguous_id_is_a_400_that_names_every_candidate(monkeypatch):
    _stub(
        monkeypatch,
        ModelResolution(
            None,
            "ambiguous",
            ("community/llama-3.1-8b-instruct", "meta-llama/llama-3.1-8b-instruct"),
        ),
        set(),
    )
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("llama-3.1-8b-instruct")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"]["code"] == "model_ambiguous"
    msg = exc.value.detail["error"]["message"]
    assert "meta-llama/llama-3.1-8b-instruct" in msg
    assert "community/llama-3.1-8b-instruct" in msg


@pytest.mark.asyncio
async def test_resolved_but_unpriced_model_stays_a_503_operator_alarm(monkeypatch):
    _stub(monkeypatch, ModelResolution("anthropic/claude-sonnet-4-6", "exact"), set())
    with pytest.raises(HTTPException) as exc:
        await inference_gates.enforce_model_pricing_gate("anthropic/claude-sonnet-4-6")
    assert exc.value.status_code == 503
    assert exc.value.detail["error"]["code"] == "pricing_not_configured"


@pytest.mark.asyncio
async def test_gate_disabled_returns_the_input_unchanged(monkeypatch):
    from src.config import Config

    monkeypatch.setattr(Config, "REQUIRE_MODEL_PRICING", False, raising=False)
    assert await inference_gates.enforce_model_pricing_gate("claude-sonnet-4-6") == (
        "claude-sonnet-4-6"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gatewayz-backend && python -m pytest tests/security/test_inference_gates_resolution.py -v`
Expected: FAIL — `AttributeError: module 'src.security.inference_gates' has no attribute 'resolve_catalog_model_id'`

- [ ] **Step 3: Write minimal implementation**

Replace the body of `enforce_model_pricing_gate` in `src/security/inference_gates.py`. Add the import at module scope, beneath the existing `from src.config import Config`:

```python
from src.services.model_resolution import resolve_catalog_model_id
```

Then the function:

```python
async def enforce_model_pricing_gate(
    model_id: str,
    request_id: str | None = None,
    api_key_mask: str | None = None,
) -> str:
    """
    Resolve `model_id` to a canonical catalog id and admit it, or raise.

    Returns the canonical id the caller MUST use downstream — routing and
    billing have to agree with what this gate priced, so the caller assigns
    the return value back onto the request.

    Raises:
        400 model_not_found  — no catalog model matches (the caller's mistake).
        400 model_ambiguous  — several match a bare name; we refuse to guess.
        400 model_not_priced — resolved, in catalog, deliberately unpriced.
        503 pricing_not_configured — resolved, high-value, pricing MISSING.
            Ours to fix, and the only branch that should page anyone.
    """
    if not Config.REQUIRE_MODEL_PRICING:
        return model_id

    import asyncio

    from src.services.pricing import model_has_pricing

    resolution = resolve_catalog_model_id(model_id)
    if resolution.canonical_id is None:
        if resolution.matched_by == "ambiguous":
            listed = ", ".join(f"'{c}'" for c in resolution.candidates)
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": (
                            f"Model '{model_id}' matches more than one model in the "
                            f"catalog ({listed}). Send the fully-qualified id."
                        ),
                        "type": "invalid_request_error",
                        "code": "model_ambiguous",
                    }
                },
            )
        logger.info(
            "Rejected unknown model (request_id=%s, model=%s, key=%s)",
            request_id,
            model_id,
            api_key_mask,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        f"Model '{model_id}' does not exist. "
                        f"See GET /v1/models for available model ids."
                    ),
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            },
        )

    resolved = resolution.canonical_id

    # Free models legitimately have no/zero pricing — exempt them so the
    # zero-price rejection in model_has_pricing only blocks PAID models whose
    # price is missing or zero (which would otherwise be served at a loss).
    try:
        from src.services.cache.model_capabilities_cache import is_free_model

        if await asyncio.to_thread(is_free_model, resolved):
            return resolved
    except Exception:  # noqa: BLE001 - free-check is best-effort
        pass

    try:
        has_pricing = await asyncio.to_thread(model_has_pricing, resolved)
    except ValueError as e:
        logger.error(
            "Rejected high-value unpriced request (request_id=%s, model=%s, "
            "resolved=%s, key=%s): %s",
            request_id,
            model_id,
            resolved,
            api_key_mask,
            e,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": (
                        f"Pricing for model '{resolved}' is not configured. "
                        f"Please contact support."
                    ),
                    "type": "service_unavailable",
                    "code": "pricing_not_configured",
                }
            },
        )

    if not has_pricing:
        logger.warning(
            "Rejected unpriced model request (request_id=%s, model=%s, "
            "resolved=%s, key=%s)",
            request_id,
            model_id,
            resolved,
            api_key_mask,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        f"Model '{resolved}' is not available for inference "
                        f"(no pricing configured)."
                    ),
                    "type": "invalid_request_error",
                    "code": "model_not_priced",
                }
            },
        )

    return resolved
```

- [ ] **Step 4: Assign the return value at the call site**

In `src/routes/chat.py`, replace lines 395-399:

```python
    req.model = await enforce_model_pricing_gate(
        req.model,
        request_id=request_id,
        api_key_mask=mask_key(api_key) if api_key else "anonymous",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/gatewayz-backend && python -m pytest tests/security/ -v`
Expected: PASS — the new file's 5 tests plus the existing `test_inference_gates.py` still green

- [ ] **Step 6: Commit**

```bash
git add src/security/inference_gates.py src/routes/chat.py tests/security/test_inference_gates_resolution.py
git commit -m "fix(admission): resolve bare model ids and 400 unknown models instead of 503"
```

---

### Task 3: Same resolution on the Anthropic surface

**Files:**
- Modify: `src/routes/messages.py` (the `enforce_model_pricing_gate` path and the error remap at `:396-410`)
- Test: `tests/routes/test_messages_model_resolution.py`

**Interfaces:**
- Consumes: the `-> str` return from Task 2.
- Produces: no new public symbols. `/v1/messages` accepts `claude-sonnet-4-6` and echoes the canonical id in the response `model` field.

`/v1/messages` is the endpoint Flashy Group integrates against, and it is where bare ids are the *norm* rather than a mistake — the Anthropic SDK has no concept of a `vendor/` prefix. This task also fixes the double-encoded body seen in production, where the upstream JSON error is stuffed into `message` as a string: `{"type":"error","error":{"type":"api_error","message":"{\"error\": {\"message\": \"Pricing for model ...\"}}"}}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/routes/test_messages_model_resolution.py
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from src.routes import messages as messages_route


def test_gate_detail_is_flattened_not_double_encoded():
    """A dict `detail` must become the Anthropic `message` string directly,
    never a JSON blob nested inside it."""
    exc = HTTPException(
        status_code=400,
        detail={
            "error": {
                "message": "Model 'nope' does not exist.",
                "type": "invalid_request_error",
                "code": "model_not_found",
            }
        },
    )
    message = messages_route._detail_message(exc)
    assert message == "Model 'nope' does not exist."
    with pytest.raises(json.JSONDecodeError):
        json.loads(message)


def test_plain_string_detail_passes_through():
    exc = HTTPException(status_code=400, detail="plain text reason")
    assert messages_route._detail_message(exc) == "plain text reason"


def test_model_not_found_maps_to_anthropic_invalid_request_error():
    assert messages_route._anthropic_error_type(400, "model_not_found") == (
        "invalid_request_error"
    )


def test_pricing_not_configured_is_not_advertised_as_retryable():
    # Anthropic SDKs retry overloaded_error; a missing pricing row is
    # deterministic, so retrying just multiplies the failure.
    assert messages_route._anthropic_error_type(503, "pricing_not_configured") == (
        "api_error"
    )


def test_genuine_upstream_capacity_503_stays_overloaded_error():
    assert messages_route._anthropic_error_type(503, None) == "overloaded_error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gatewayz-backend && python -m pytest tests/routes/test_messages_model_resolution.py -v`
Expected: FAIL — `AttributeError: module 'src.routes.messages' has no attribute '_detail_message'`

- [ ] **Step 3: Write minimal implementation**

Add both helpers to `src/routes/messages.py`, above the handler that currently builds `error_type`:

```python
def _detail_message(exc: HTTPException) -> str:
    """The human-readable string out of an HTTPException detail.

    Our gates raise `detail={"error": {"message": ..., "code": ...}}`. Passing
    that dict through `str()` produced a JSON blob nested inside the Anthropic
    envelope's own `message` field — unreadable in an SDK traceback.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
        if detail.get("message"):
            return str(detail["message"])
    return str(detail)


def _detail_code(exc: HTTPException) -> str | None:
    detail = exc.detail
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict):
            return inner.get("code")
    return None


def _anthropic_error_type(status_code: int, code: str | None) -> str:
    """Map our status + error code onto an Anthropic error type."""
    base = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        429: "rate_limit_error",
        # Provider capacity/budget exhaustion surfaces as 503 — Anthropic SDKs
        # retry overloaded_error.
        503: "overloaded_error",
    }.get(status_code, "api_error")
    # A 503 for missing pricing config is deterministic, not transient —
    # don't invite SDK retries on it.
    if base == "overloaded_error" and code == "pricing_not_configured":
        return "api_error"
    return base
```

Then replace the remap block at `src/routes/messages.py:396-410` with:

```python
        message = _detail_message(exc)
        error_type = _anthropic_error_type(exc.status_code, _detail_code(exc))
        raise _anthropic_error(exc.status_code, error_type, message) from exc
```

Finally, make the route assign the gate's return value the same way `chat.py` does, so a bare id is resolved before routing:

```python
    req.model = await enforce_model_pricing_gate(
        req.model,
        request_id=request_id,
        api_key_mask=mask_key(api_key) if api_key else "anonymous",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/gatewayz-backend && python -m pytest tests/routes/test_messages_model_resolution.py tests/routes/ -k "messages" -v`
Expected: PASS — 5 new tests green, existing `/v1/messages` tests unchanged

- [ ] **Step 5: Commit**

```bash
git add src/routes/messages.py tests/routes/test_messages_model_resolution.py
git commit -m "fix(messages): resolve bare Anthropic model ids, stop double-encoding error bodies"
```

---

### Task 4: Stop the high-value pattern list from drifting

**Files:**
- Modify: `src/services/pricing/pricing.py:688-712` and the verbatim duplicate at `:931-955`
- Test: `tests/services/test_high_value_patterns.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `def is_high_value_model(model_id: str, normalized_id: str = "") -> bool` in `src/services/pricing/pricing.py`, replacing both inline copies of `HIGH_VALUE_MODEL_PATTERNS`.

The list currently decides whether an unpriced model is a 503 (ours) or silently billed at the $0.00002 default. It contains `claude-sonnet-4` but not `claude-sonnet-5` — so today, the *older* model is protected and the current one is not. Widening the patterns is the small fix; the durable fix is treating any model the catalog lists as non-free as high-value, with the pattern list as the fallback for ids the catalog hasn't got.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_high_value_patterns.py
from __future__ import annotations

import pytest

from src.services.pricing.pricing import is_high_value_model


@pytest.mark.parametrize(
    "model_id",
    [
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-sonnet-5",
        "anthropic/claude-opus-5",
        "anthropic/claude-haiku-4-5-20251001",
        "openai/gpt-5",
        "openai/gpt-4o-mini",
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-pro-preview-09-2025",
    ],
)
def test_current_generation_frontier_models_are_high_value(model_id):
    assert is_high_value_model(model_id) is True


@pytest.mark.parametrize(
    "model_id",
    ["meta-llama/llama-3.1-8b-instruct:free", "some-vendor/tiny-7b", ""],
)
def test_commodity_and_free_models_are_not_high_value(model_id):
    assert is_high_value_model(model_id) is False


def test_normalized_id_is_also_consulted():
    # A provider-transformed id whose vendor identity only shows after
    # normalization must still be protected.
    assert is_high_value_model(
        "accounts/fireworks/models/claude-sonnet-5", "anthropic/claude-sonnet-5"
    ) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gatewayz-backend && python -m pytest tests/services/test_high_value_patterns.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_high_value_model'`

- [ ] **Step 3: Write minimal implementation**

Add near the top of `src/services/pricing/pricing.py`, after the imports:

```python
# Vendor families whose models are expensive enough that serving one at the
# $0.00002 default rate is a revenue incident, not a rounding error. Matched
# as substrings against both the raw and normalized id.
#
# Keep FAMILY-level, not generation-level: the previous list held
# "claude-sonnet-4" and therefore stopped protecting Anthropic the day
# claude-sonnet-5 shipped. A family entry cannot drift that way.
HIGH_VALUE_MODEL_PATTERNS: tuple[str, ...] = (
    "gpt-4",
    "gpt-5",
    "o1-",
    "o3-",
    "o4-",
    "claude-",          # every Claude generation, opus/sonnet/haiku alike
    "gemini-",          # every Gemini generation
    "command-r-plus",
    "mixtral-8x22b",
    "grok-",
)

# Free models are exempt: a :free id has no revenue to lose.
_FREE_MARKERS = (":free",)


def is_high_value_model(model_id: str, normalized_id: str = "") -> bool:
    """True when serving `model_id` unpriced would under-bill materially."""
    if not model_id:
        return False
    haystacks = (model_id.lower(), (normalized_id or "").lower())
    if any(marker in h for h in haystacks for marker in _FREE_MARKERS):
        return False
    return any(p in h for h in haystacks if h for p in HIGH_VALUE_MODEL_PATTERNS)
```

Then in **both** places (`:688-712` and `:931-955`), delete the local `HIGH_VALUE_MODEL_PATTERNS` list and the `is_high_value = any(...)` comprehension, and replace with:

```python
        is_high_value = is_high_value_model(model_id, normalized_model_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/gatewayz-backend && python -m pytest tests/services/test_high_value_patterns.py tests/services/ -k "pricing" -v`
Expected: PASS — 12 new tests green, existing pricing tests unchanged

- [ ] **Step 5: Commit**

```bash
git add src/services/pricing/pricing.py tests/services/test_high_value_patterns.py
git commit -m "fix(pricing): family-level high-value patterns, deduped into one helper"
```

---

### Task 5: Seed curated aliases and invalidate the index on catalog sync

**Files:**
- Create: `supabase/migrations/20260908000000_seed_vendor_native_model_aliases.sql`
- Modify: `src/services/cache/model_catalog_cache.py:1377-1398` (`invalidate_full_catalog`)
- Test: `tests/services/test_model_resolution_invalidation.py`

**Interfaces:**
- Consumes: `invalidate_resolution_index()` from Task 1.
- Produces: no new symbols. `model_aliases` gains rows pinning the vendor-native ids that must never depend on a suffix match being unique.

The resolver works with an empty alias table. These rows exist so that the ids our partners actually send stay pinned even if a re-host later introduces a second catalog entry with the same bare name — which would otherwise flip them from resolved to `model_ambiguous`.

- [ ] **Step 1: Write the migration**

```sql
-- supabase/migrations/20260908000000_seed_vendor_native_model_aliases.sql
--
-- Vendor-native model ids are what SDKs actually send: the Anthropic SDK has
-- no `vendor/` prefix, so /v1/messages callers send `claude-sonnet-4-6`.
-- resolve_catalog_model_id() would find these by unique suffix today; pinning
-- them means a future re-host entry sharing the bare name cannot turn a live
-- partner integration into a 400 model_ambiguous.
--
-- Alias keys are lowercase (load_alias_map lowercases on read).

insert into public.model_aliases (alias, canonical_id, notes)
values
  ('claude-sonnet-4-6',            'anthropic/claude-sonnet-4-6',            'vendor-native (Anthropic SDK)'),
  ('claude-sonnet-4-5',            'anthropic/claude-sonnet-4-5',            'vendor-native (Anthropic SDK)'),
  ('claude-sonnet-5',              'anthropic/claude-sonnet-5',              'vendor-native (Anthropic SDK)'),
  ('claude-opus-5',                'anthropic/claude-opus-5',                'vendor-native (Anthropic SDK)'),
  ('claude-haiku-4-5-20251001',    'anthropic/claude-haiku-4-5-20251001',    'vendor-native (Anthropic SDK)'),
  ('gpt-4o-mini',                  'openai/gpt-4o-mini',                     'vendor-native (OpenAI SDK)'),
  ('gpt-5',                        'openai/gpt-5',                           'vendor-native (OpenAI SDK)')
on conflict (alias) do update
  set canonical_id = excluded.canonical_id,
      notes        = excluded.notes;
```

- [ ] **Step 2: Verify every canonical_id in the migration exists in the live catalog**

Run:
```bash
curl -s https://api.gatewayz.ai/v1/models \
  | python3 -c "import json,sys; ids={m['id'] for m in json.load(sys.stdin)['data']}; \
     [print(('OK  ' if c in ids else 'MISS'), c) for c in [
       'anthropic/claude-sonnet-4-6','anthropic/claude-sonnet-4-5','anthropic/claude-sonnet-5',
       'anthropic/claude-opus-5','anthropic/claude-haiku-4-5-20251001',
       'openai/gpt-4o-mini','openai/gpt-5']]"
```
Expected: every line `OK`. Delete any `MISS` row from the migration before applying — an alias pointing at a nonexistent id turns a working 400 into a confusing one.

- [ ] **Step 3: Write the failing invalidation test**

```python
# tests/services/test_model_resolution_invalidation.py
from __future__ import annotations

from src.services import model_resolution


def test_catalog_invalidation_drops_the_resolution_index(monkeypatch):
    ids = ["anthropic/claude-sonnet-4-6"]
    monkeypatch.setattr(model_resolution, "_load_catalog_ids", lambda: list(ids))
    monkeypatch.setattr(model_resolution, "_load_alias_map", dict)
    model_resolution.invalidate_resolution_index()

    assert model_resolution.resolve_catalog_model_id("claude-sonnet-4-6").canonical_id == (
        "anthropic/claude-sonnet-4-6"
    )

    # A catalog sync adds a competing re-host of the same bare name.
    ids.append("near/claude-sonnet-4-6")

    # Stale index: still resolves, because nothing told it to rebuild.
    assert model_resolution.resolve_catalog_model_id("claude-sonnet-4-6").canonical_id is not None

    from src.services.cache.model_catalog_cache import invalidate_full_catalog

    invalidate_full_catalog()

    after = model_resolution.resolve_catalog_model_id("claude-sonnet-4-6")
    assert after.canonical_id is None
    assert after.matched_by == "ambiguous"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd ~/gatewayz-backend && python -m pytest tests/services/test_model_resolution_invalidation.py -v`
Expected: FAIL — final assertions fail; the stale index still returns `anthropic/claude-sonnet-4-6`

- [ ] **Step 5: Hook invalidation into the catalog cache**

In `src/services/cache/model_catalog_cache.py`, inside `invalidate_full_catalog()`, before its `return`:

```python
    # The bare-id resolution index is derived from this catalog — a sync that
    # adds or removes a model can change whether a bare name is unique.
    try:
        from src.services.model_resolution import invalidate_resolution_index

        invalidate_resolution_index()
    except Exception:  # noqa: BLE001 - never fail a cache invalidation on this
        logger.warning("resolution index invalidation failed", exc_info=True)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd ~/gatewayz-backend && python -m pytest tests/services/test_model_resolution_invalidation.py -v`
Expected: PASS — 1 passed

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/20260908000000_seed_vendor_native_model_aliases.sql \
        src/services/cache/model_catalog_cache.py \
        tests/services/test_model_resolution_invalidation.py
git commit -m "feat(models): pin vendor-native aliases, rebuild resolution index on catalog sync"
```

---

### Task 6: Prove it end-to-end and keep it proven

**Files:**
- Create: `tests/routes/test_bare_model_id_e2e.py`
- Modify: `~/orchestrator/checks/gatewayz_flashy.py` (separate repo — commit there)

**Interfaces:**
- Consumes: everything above.
- Produces: `BARE_CANARY_MODEL = "claude-haiku-4-5-20251001"` in the orchestrator check, and a `bare_id_admitted` boolean in that check's `metrics`.

The daily canary currently sends `anthropic/claude-haiku-4-5-20251001` (`checks/gatewayz_flashy.py:29`) — a *prefixed* id, which is exactly why this defect ran green in the 7am report the whole time it was live. Adding a bare-id probe is what stops the next regression being found by a partner.

- [ ] **Step 1: Write the failing e2e test**

```python
# tests/routes/test_bare_model_id_e2e.py
"""Both inference surfaces must accept a vendor-native model id.

Transport-level: FastAPI TestClient with the provider call stubbed. This is
the regression that FlashyOS's generated scaffold hit in production.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "src.services.model_resolution._load_catalog_ids",
        lambda: ["anthropic/claude-sonnet-4-6", "openai/gpt-4o-mini"],
    )
    monkeypatch.setattr("src.services.model_resolution._load_alias_map", dict)
    monkeypatch.setattr(
        "src.services.pricing.model_has_pricing",
        lambda m: m in {"anthropic/claude-sonnet-4-6", "openai/gpt-4o-mini"},
    )
    from src.services import model_resolution

    model_resolution.invalidate_resolution_index()
    return TestClient(app)


def test_chat_completions_accepts_a_bare_anthropic_id(client, auth_headers, stub_provider):
    resp = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["model"] == "anthropic/claude-sonnet-4-6"


def test_messages_accepts_a_bare_anthropic_id(client, auth_headers, stub_provider):
    resp = client.post(
        "/v1/messages",
        headers={**auth_headers, "anthropic-version": "2023-06-01"},
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["model"] == "anthropic/claude-sonnet-4-6"


def test_unknown_model_is_a_400_on_both_surfaces(client, auth_headers):
    chat = client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={"model": "nope-9000", "max_tokens": 1,
              "messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code == 400
    assert chat.json()["error"]["code"] == "model_not_found"

    msg = client.post(
        "/v1/messages",
        headers={**auth_headers, "anthropic-version": "2023-06-01"},
        json={"model": "nope-9000", "max_tokens": 1,
              "messages": [{"role": "user", "content": "hi"}]},
    )
    assert msg.status_code == 400
    assert msg.json()["error"]["type"] == "invalid_request_error"
    assert "contact support" not in msg.json()["error"]["message"].lower()
```

Reuse the repo's existing `auth_headers` and `stub_provider` fixtures — check `tests/conftest.py` for their exact names and add the import or fixture aliases the file needs. If no provider stub exists, add one to this file that patches the same seam the neighbouring `tests/routes/` suites patch.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/gatewayz-backend && python -m pytest tests/routes/test_bare_model_id_e2e.py -v`
Expected: FAIL before Tasks 1-3 are merged; PASS after. If the whole plan is executed in order this test should pass immediately — in that case, temporarily revert the Task 2 call-site edit, confirm it fails, and restore.

- [ ] **Step 3: Run the full suite**

Run: `cd ~/gatewayz-backend && python -m pytest tests/ -x -q`
Expected: PASS — no regressions. Pay attention to any test asserting a 503 for an unknown model; that assertion encoded the old behavior and must be updated to 400 with a comment explaining the change, not deleted.

- [ ] **Step 4: Add the bare-id probe to the daily report**

In `~/orchestrator/checks/gatewayz_flashy.py`, beneath `CANARY_MODEL` at line 29:

```python
# The prefixed canary passed all through the window in which every BARE id
# 503'd — a green canary is only evidence for the id shape it sends. Probe
# both, because partners send the bare one.
BARE_CANARY_MODEL = "claude-haiku-4-5-20251001"
```

Then add a second probe alongside the existing `/v1/messages` canary, recording `metrics["bare_id_admitted"] = True/False` and appending to `issues` when a bare id is rejected:

```python
    bare_status = _post_canary(BARE_CANARY_MODEL, key)
    metrics["bare_id_admitted"] = bare_status == 200
    if bare_status != 200:
        issues.append(
            f"bare model id '{BARE_CANARY_MODEL}' rejected with {bare_status} — "
            f"vendor-native ids are what partner SDKs send"
        )
```

- [ ] **Step 5: Verify the check renders**

Run: `cd ~ && python3 -m orchestrator.runner --dry-run`
Expected: the GATEWAYZ/FLASHY section prints `bare_id_admitted: True` with no new issue lines.

- [ ] **Step 6: Commit both repos**

```bash
cd ~/gatewayz-backend
git add tests/routes/test_bare_model_id_e2e.py
git commit -m "test(e2e): bare vendor-native model ids on both inference surfaces"

cd ~/orchestrator
git add checks/gatewayz_flashy.py
git commit -m "feat(gatewayz): canary a bare model id, not only a prefixed one"
```

---

### Task 7: Ship it and tell the partner

**Files:**
- Modify: `~/gatewayz/docs/plans/flashy-kit/STAGE1-KIT.md`
- Modify: `~/gatewayz/PRD-flashy-integration.md`

**Interfaces:**
- Consumes: the deployed behavior from Tasks 1-6.
- Produces: no code. A verified production claim and a corrected partner statement.

- [ ] **Step 1: Open the PR**

```bash
cd ~/gatewayz-backend
git push -u origin HEAD
gh pr create --title "fix: accept vendor-native model ids; 400 unknown models instead of 503" \
  --body "$(cat <<'EOF'
## Problem
`/v1/chat/completions` and `/v1/messages` reject vendor-native model ids
(`claude-sonnet-4-6`) with **503 service_unavailable / "contact support"** —
the shape every Anthropic SDK sends, and the shape FlashyOS's official
`@flashyos/agent` scaffold generates. Bare OpenAI ids already cleared
admission, so the behavior was inconsistent by vendor.

## Fix
- `resolve_catalog_model_id()` — exact → curated alias → unique suffix, fails
  closed on ambiguity.
- Resolution runs at `enforce_model_pricing_gate`, which now returns the
  canonical id so routing and billing agree with what was priced.
- Error taxonomy: unknown → 400 `model_not_found`, ambiguous → 400
  `model_ambiguous`, unpriced-in-catalog → 400 `model_not_priced`. 503 is now
  reserved for a catalogued model ops failed to price.
- `/v1/messages` error bodies no longer double-encode the upstream JSON.
- `HIGH_VALUE_MODEL_PATTERNS` moved to family level (`claude-`, `gemini-`) and
  deduped — the old list held `claude-sonnet-4` and stopped protecting
  Anthropic when `claude-sonnet-5` shipped.
- Daily canary now probes a bare id; the prefixed-only canary was green
  throughout this outage.

## Verification
Production probe table and full evidence:
docs/superpowers/plans/2026-09-08-bare-model-id-resolution.md
EOF
)"
```

- [ ] **Step 2: Verify against production after deploy**

Run:
```bash
set -a; . ~/.gatewayz-flashy.env; set +a
for m in claude-sonnet-4-6 anthropic/claude-sonnet-4-6 nope-9000; do
  printf "%-32s " "$m"
  curl -s -m 60 -o /tmp/v.json -w "%{http_code} " https://api.gatewayz.ai/v1/messages \
    -H "content-type: application/json" -H "anthropic-version: 2023-06-01" \
    -H "authorization: Bearer $GATEWAYZ_API_KEY" \
    -d "{\"model\":\"$m\",\"max_tokens\":1,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
  head -c 160 /tmp/v.json; echo
done
```
Expected: `claude-sonnet-4-6` → **200**; `anthropic/claude-sonnet-4-6` → 200; `nope-9000` → **400** with `invalid_request_error` and no "contact support".

- [ ] **Step 3: Correct the partner-facing kit**

In `~/gatewayz/docs/plans/flashy-kit/STAGE1-KIT.md`, replace any statement that model ids must be provider-prefixed with:

> Model ids may be sent either fully-qualified (`anthropic/claude-sonnet-4-6`)
> or vendor-native (`claude-sonnet-4-6`); vendor-native ids resolve to the
> catalog entry when exactly one model carries that name. `GET /v1/models`
> lists the fully-qualified ids, which are what the response echoes back.

Add to the verified-facts list in `~/gatewayz/PRD-flashy-integration.md`:

> **Unknown model → 400 `model_not_found`** (was 503 `pricing_not_configured`
> until 2026-09-08). 503 on this endpoint now means only that a catalogued
> model is missing a pricing row — an alarm on us, never a caller mistake.

- [ ] **Step 4: Commit the docs**

```bash
cd ~/gatewayz
git add docs/plans/flashy-kit/STAGE1-KIT.md PRD-flashy-integration.md
git commit -m "docs(flashy): vendor-native model ids accepted; 400 replaces 503 on unknown models"
```

- [ ] **Step 5: Send the follow-up to Flashy Labs (human step — Joaquim)**

Their scaffold's one-line prefix change is still worth taking, but it is no
longer required for their generated client to work. Tell them:

> Both id shapes now work against `api.gatewayz.ai`, so
> `flashyos init --inference gatewayz` produces a working client as-is. The
> underlying issue was ours: vendor-native ids are what the Anthropic SDK
> sends, and we were rejecting them with a 503 that read like an outage on our
> side. Fixed and verified in production on [DATE]. If you still want the
> scaffold to name the fully-qualified id, `anthropic/claude-sonnet-4-6` is
> what `GET /v1/models` returns and what the response echoes.

---

## Self-Review

**Spec coverage.** Defect 1 (no resolution) → Tasks 1, 2, 3, 5. Defect 2 (503 blames us) → Tasks 2, 3. Defect 3 (pattern drift + duplication) → Task 4. The evidence table's `/v1/messages` rows → Task 3. Regression protection → Task 6. Partner correction → Task 7. No requirement is unassigned.

**Placeholder scan.** One deliberate deferral in Task 6 Step 1: the fixture names (`auth_headers`, `stub_provider`) must be read from the repo's `tests/conftest.py` rather than invented, and the step says so explicitly. One date placeholder in Task 7 Step 5, filled at send time.

**Type consistency.** `resolve_catalog_model_id` returns `ModelResolution` in Tasks 1, 2, 5 and is monkeypatched with the same type in Task 2's stub. `enforce_model_pricing_gate` returns `str` in Task 2 and is consumed as `str` in Tasks 2 and 3. `is_high_value_model(model_id, normalized_id="")` has one signature, used at both former pattern-list sites. `invalidate_resolution_index()` is defined in Task 1 and called in Task 5.

**Known risk.** Suffix resolution over a ~11k-row catalog will find more ambiguity than this plan's fixtures show. That is the intended behavior — ambiguity produces a 400 naming the candidates, which is strictly better than today's 503 — but expect the Task 5 alias table to grow as real partner traffic reveals which bare names collide. Watch the `[MODEL_RESOLVE]` ambiguity log line for the first week after deploy.

---

## Execution notes (2026-09-08) — where reality differed from the plan

Implemented on branch `fix/bare-model-id-resolution`, PR
[#2291](https://github.com/Alpaca-Network/gatewayz-backend/pull/2291). Five
things the plan got wrong or missed, recorded so the next reader trusts the
code over this document:

1. **Resolution had to be made strictly additive.** As planned, an unresolved
   id was a 400. The existing `tests/services/test_zero_price_gating.py`
   immediately failed, and it was right to: with a cold or unreachable catalog
   the index is empty, so *every* request would have returned "that model does
   not exist" — a cache outage escalated into a total API outage. Added
   `index_is_empty()`; a fully-qualified id, and any id while the index is
   cold, now falls through to the pricing checks unchanged. Only a **bare**
   name checked against a **populated** index can produce `model_not_found`.

2. **Three copies of the pattern list, not two.** A third lived at the
   error-path guard (`get_model_pricing`'s `except` branch), matching on the
   raw id only. All three now call `is_high_value_model()`.

3. **`/v1/messages` needed no resolution call of its own.** It delegates to
   `chat_completions`, so Task 2 fixed it. Task 3 reduced to the error-shaping
   work — which turned out to matter more than expected: the gateway's detail
   dict was `json.dumps`'d *into* the Anthropic envelope's `message` field.

4. **A harness trap made stubs vacuous.** `src/services/pricing/` is a package
   containing `pricing.py`, so
   `monkeypatch.setattr("src.services.pricing.model_has_pricing", ...)`
   patches the **submodule** while
   `from src.services.pricing import model_has_pricing` reads the **package**
   attribute. Tests written that way pass against real pricing data. Patch
   `sys.modules["src.services.pricing"]` instead. Both new test files carry
   this note; the fix cut their runtime from 18s to 1.6s, which is what
   exposed it.

5. **`anthropic/claude-sonnet-4-5` does not exist.** Task 5 Step 2's
   verification caught it — the catalog carries only
   `anthropic/claude-sonnet-4-5-20250929`. It was dropped from the migration,
   and the bare form correctly returns 400. The production probe in the
   Evidence table that showed `claude-sonnet-4-5` → 503 was therefore a
   *genuinely* unknown model, not a resolution failure.

**Test result:** 3847 passed, 0 failed. An earlier full run had one failure in
`tests/services/test_model_catalog_sync_delisting.py`; it passes in isolation,
passed on the repeat full run, and has no logical coupling to these changes —
but a single clean run on `main` is not proof it was pre-existing, so it is
recorded here rather than dismissed.

**Not done:** Task 7 Steps 2 and 5 — production verification and the note to
Flashy Labs both wait on this PR deploying.
