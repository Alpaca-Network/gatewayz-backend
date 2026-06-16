# Phase 0 · Step 2 — Canonical Provider-Adapter Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the implicit provider dispatch contract explicit and enforced — a typed `ProviderAdapter` interface + documented response shape + a conformance test every registered provider must satisfy + one reference adapter (`openai`) — with **zero behavior change**.

**Architecture:** Purely additive formalization. Today each provider exposes three loose, untyped module-level functions (`make_<p>_request*`, `process_<p>_response`, `make_<p>_request*_stream`) wired into `PROVIDER_ROUTING` as a `{request, process, stream}` dict, consumed by `ChatInferenceHandler`. This step adds a typed contract module, types the registry, supplies `OpenAIProviderAdapter` as the reference object-form implementation (pure delegation — no new logic), and a conformance test. The live dispatch path (`PROVIDER_ROUTING` functions) is unchanged; the new types are no-ops at runtime and the adapter class is additive.

**Tech Stack:** Python 3.10–3.12 (`typing.Protocol`, `runtime_checkable`, `TypedDict`), pytest, ruff/black/isort. `mypy` is warning-only in CI, so added annotations cannot block the build.

---

## Context for the implementer (read first)

This is **Step 2 of Phase 0** of *Gatewayz One* (spec `docs/superpowers/specs/2026-06-16-gatewayz-one-architecture-design.md`, §6.3 Inference Dispatch + §8.1). Scope was decided as **Option 1: contract + interface + one reference client**. Fat-client thinning (e.g. `google_vertex`, 1784 lines) is explicitly a **separate follow-on (0c-2)** — do NOT attempt it here.

**Verified current state (do not re-investigate):**

- Provider clients live in `src/services/providers/<name>_client.py`. Each exposes three module-level functions. Naming is *mostly* `make_<p>_request_openai` / `process_<p>_response` / `make_<p>_request_openai_stream`, but **`openai` and `anthropic` use shorter names** (`make_openai_request`, `process_openai_response`, `make_openai_request_stream` — see `src/services/providers/openai_client.py:46,74,104`).
- `src/handlers/provider_registry.py` declares `PROVIDER_FUNCTIONS` (slug → list of function names, lines 77–230), imports them via `_safe_import_provider` (returns **callable sentinels** on import failure, lines 28–70), and builds `PROVIDER_ROUTING` (lines 253–404) as `{slug: {"request": fn|None, "process": fn|None, "stream": fn|None}}`.
- **Enablement nuance:** the load loop (lines 239–245) **skips disabled providers**, so for a disabled provider `_loaded_functions.get(name)` returns **`None`** → its `PROVIDER_ROUTING` entry has `None` values. In tests, `ENABLED_PROVIDERS` defaults to `"openrouter"` (`src/config/config.py:483`), so most entries are `None` under test. The conformance test MUST treat an entry as valid if its three values are **all callable OR all `None`** (consistent), not "always callable".
- The de-facto contract `ChatInferenceHandler` depends on (`src/handlers/chat_handler.py`):
  - `request(messages: list[dict], model: str, **params) -> raw` where `raw` exposes `.choices[0].message.content` (str), `.choices[0].finish_reason`, `.usage.prompt_tokens|completion_tokens|total_tokens`.
  - `stream(messages, model, **params) -> Iterator[chunk]` — a **sync** generator of OpenAI-shaped delta chunks.
  - `process(raw) -> dict` shaped `{id, object, created, model, choices, usage}`. **The handler does NOT call `process()`** (it reads `raw` directly); only the anonymous raw-dispatch path in `chat.py` uses `process()`. Document this; do not "fix" it here.
  - `params` keys passed: `temperature, max_tokens, top_p, frequency_penalty, presence_penalty, stop, tools, tool_choice, response_format, user`.

**Branch/worktree:** this work stacks on Phase 0 Step 1. Execute in a worktree on a branch based off `feat/phase0-dead-code-purge` (Step 1 is committed there, unmerged pending a parallel agent's `main` work). Do NOT execute in the shared `main` checkout.

**Local test/lint env:** use `<repo>/.venv/bin/python -m pytest <paths> -o addopts="" -p no:cacheprovider` (the `.venv` lacks `pytest-xdist`/`pytest-timeout`, so the `pytest.ini` `addopts` like `-n auto` fail). Lint tools (`ruff`/`black`/`isort`) are on the system PATH, not in `.venv`.

---

## File Structure

- **Create:** `src/services/providers/base.py` — the typed contract (`ProviderParams`, `ProviderRequestFn`/`ProviderProcessFn`/`ProviderStreamFn` aliases, `ProviderRouting` TypedDict, `ProviderAdapter` runtime-checkable Protocol, documented response shape).
- **Create:** `tests/services/test_provider_contract.py` — conformance + reference-adapter tests.
- **Modify:** `src/services/providers/openai_client.py` — add `OpenAIProviderAdapter` reference class (pure delegation to the existing three functions).
- **Modify:** `src/handlers/provider_registry.py` — annotate `PROVIDER_ROUTING: dict[str, ProviderRouting]` and import the type.

---

## Task 1: Define the canonical contract module

**Files:**
- Create: `src/services/providers/base.py`
- Test: `tests/services/test_provider_contract.py`

- [ ] **Step 1: Write the failing test for the contract types**

Create `tests/services/test_provider_contract.py`:
```python
"""Conformance tests for the canonical provider-adapter contract."""
import inspect

import pytest


def test_contract_module_exposes_types():
    from src.services.providers.base import (
        ProviderAdapter,
        ProviderParams,
        ProviderRouting,
    )

    # ProviderRouting is a TypedDict with exactly the three contract keys
    assert set(ProviderRouting.__annotations__.keys()) == {"request", "process", "stream"}
    # ProviderParams is a (total=False) TypedDict covering the handler's kwargs
    assert "temperature" in ProviderParams.__annotations__
    assert "max_tokens" in ProviderParams.__annotations__
    # ProviderAdapter is a runtime-checkable Protocol with the three methods
    assert hasattr(ProviderAdapter, "_is_runtime_protocol") or hasattr(
        ProviderAdapter, "_is_protocol"
    )
    for method in ("request", "stream", "process"):
        assert method in ProviderAdapter.__dict__ or hasattr(ProviderAdapter, method)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `<repo>/.venv/bin/python -m pytest tests/services/test_provider_contract.py -o addopts="" -p no:cacheprovider -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.providers.base'`.

- [ ] **Step 3: Create the contract module**

Create `src/services/providers/base.py`:
```python
"""Canonical provider-adapter contract.

Every provider client in ``src/services/providers/`` exposes three module-level
callables that are registered in ``PROVIDER_ROUTING`` (see
``src/handlers/provider_registry.py``). That contract used to be implicit and
untyped. This module makes it explicit so new providers have a precise target
to implement and a conformance test can enforce uniformity.

The contract (matches what ``ChatInferenceHandler`` depends on):

``request(messages, model, **params) -> raw``
    Non-streaming call. ``raw`` is an OpenAI-SDK-shaped response object exposing:
      ``raw.choices[0].message.content`` : str
      ``raw.choices[0].finish_reason``   : str | None
      ``raw.usage.prompt_tokens``        : int
      ``raw.usage.completion_tokens``    : int
      ``raw.usage.total_tokens``         : int

``stream(messages, model, **params) -> Iterator[chunk]``
    A **sync** generator yielding OpenAI-SDK-shaped delta chunks.

``process(raw) -> dict``
    Converts ``raw`` to the OpenAI dict shape
    ``{id, object, created, model, choices, usage}``. NOTE: the authenticated
    ``ChatInferenceHandler`` path reads ``raw`` directly and does NOT call
    ``process()``; only the anonymous raw-dispatch path in ``chat.py`` uses it.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol, TypedDict, runtime_checkable


class ProviderParams(TypedDict, total=False):
    """Optional generation parameters the handler forwards to a provider call."""

    temperature: float | None
    max_tokens: int | None
    top_p: float | None
    frequency_penalty: float | None
    presence_penalty: float | None
    stop: str | list[str] | None
    tools: list[dict[str, Any]] | None
    tool_choice: str | dict[str, Any] | None
    response_format: dict[str, Any] | None
    user: str | None


# Function-form aliases (the current registry stores bare functions).
ProviderRequestFn = Callable[..., Any]
ProviderProcessFn = Callable[[Any], dict[str, Any]]
ProviderStreamFn = Callable[..., Iterator[Any]]


class ProviderRouting(TypedDict):
    """A single ``PROVIDER_ROUTING`` entry: the three callables for a provider.

    Values are ``None`` when the provider is disabled (its client is not loaded).
    """

    request: ProviderRequestFn | None
    process: ProviderProcessFn | None
    stream: ProviderStreamFn | None


@runtime_checkable
class ProviderAdapter(Protocol):
    """Object-form contract for the reference adapter and future class adapters.

    Existing providers satisfy the function-form (``ProviderRouting``); new
    adapters may implement this object form. Both are valid contract shapes.
    """

    def request(self, messages: list[dict[str, Any]], model: str, **params: Any) -> Any: ...

    def stream(
        self, messages: list[dict[str, Any]], model: str, **params: Any
    ) -> Iterator[Any]: ...

    def process(self, response: Any) -> dict[str, Any]: ...
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `<repo>/.venv/bin/python -m pytest tests/services/test_provider_contract.py -o addopts="" -p no:cacheprovider -q`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/services/providers/base.py tests/services/test_provider_contract.py
git commit -m "feat(providers): add canonical provider-adapter contract types

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Add the registry conformance test

**Files:**
- Test: `tests/services/test_provider_contract.py` (append)

- [ ] **Step 1: Write the failing conformance tests**

Append to `tests/services/test_provider_contract.py`:
```python
def test_every_provider_declares_a_full_trio():
    """PROVIDER_FUNCTIONS: each provider declares one request, one process,
    and one (sync) stream function name."""
    from src.handlers.provider_registry import PROVIDER_FUNCTIONS

    for slug, fns in PROVIDER_FUNCTIONS.items():
        has_process = any(f.startswith("process_") for f in fns)
        has_stream = any(f.endswith("_stream") for f in fns)
        # a request fn is a make_* that is not a stream fn
        has_request = any(f.startswith("make_") and not f.endswith("_stream") for f in fns)
        assert has_process, f"{slug}: no process_* function declared"
        assert has_stream, f"{slug}: no *_stream function declared"
        assert has_request, f"{slug}: no non-stream make_* request function declared"


def test_provider_routing_entries_are_shape_consistent():
    """PROVIDER_ROUTING: every entry has exactly request/process/stream keys, and
    the three values are either all callable (enabled) or all None (disabled)."""
    from src.handlers.provider_registry import PROVIDER_ROUTING

    for slug, routing in PROVIDER_ROUTING.items():
        assert set(routing.keys()) == {"request", "process", "stream"}, f"{slug}: wrong keys"
        values = [routing["request"], routing["process"], routing["stream"]]
        all_callable = all(callable(v) for v in values)
        all_none = all(v is None for v in values)
        assert all_callable or all_none, (
            f"{slug}: mixed callable/None values {[type(v).__name__ for v in values]}"
        )
```

- [ ] **Step 2: Run to verify both pass against the current registry**

Run: `<repo>/.venv/bin/python -m pytest tests/services/test_provider_contract.py -o addopts="" -p no:cacheprovider -q`
Expected: PASS (3 tests). These describe the *existing* registry, so they pass immediately — they are guard rails that will FAIL if a future provider breaks the contract.

If `test_provider_routing_entries_are_shape_consistent` fails, a provider entry has a mixed shape — investigate that provider's `PROVIDER_FUNCTIONS` names before changing the test.

- [ ] **Step 3: Commit**

```bash
git add tests/services/test_provider_contract.py
git commit -m "test(providers): enforce provider trio + routing shape conformance

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Add the `openai` reference adapter (object form)

**Files:**
- Modify: `src/services/providers/openai_client.py` (add class after `process_openai_response`, ~line 140)
- Test: `tests/services/test_provider_contract.py` (append)

- [ ] **Step 1: Write the failing reference-adapter test**

Append to `tests/services/test_provider_contract.py`:
```python
def test_openai_reference_adapter_conforms_and_delegates(monkeypatch):
    """OpenAIProviderAdapter satisfies the ProviderAdapter protocol and delegates
    to the existing module-level functions without adding behavior."""
    from src.services.providers import openai_client
    from src.services.providers.base import ProviderAdapter

    adapter = openai_client.OpenAIProviderAdapter()
    assert isinstance(adapter, ProviderAdapter)  # runtime_checkable structural check

    calls = {}
    monkeypatch.setattr(
        openai_client, "make_openai_request", lambda messages, model, **kw: ("req", messages, model, kw)
    )
    monkeypatch.setattr(
        openai_client, "make_openai_request_stream", lambda messages, model, **kw: iter([("chunk", model)])
    )
    monkeypatch.setattr(openai_client, "process_openai_response", lambda resp: {"processed": resp})

    assert adapter.request([{"role": "user", "content": "hi"}], "gpt-4o", temperature=0.5) == (
        "req",
        [{"role": "user", "content": "hi"}],
        "gpt-4o",
        {"temperature": 0.5},
    )
    assert list(adapter.stream([], "gpt-4o")) == [("chunk", "gpt-4o")]
    assert adapter.process("raw") == {"processed": "raw"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `<repo>/.venv/bin/python -m pytest tests/services/test_provider_contract.py::test_openai_reference_adapter_conforms_and_delegates -o addopts="" -p no:cacheprovider -q`
Expected: FAIL — `AttributeError: module 'src.services.providers.openai_client' has no attribute 'OpenAIProviderAdapter'`.

- [ ] **Step 3: Add the reference adapter class**

In `src/services/providers/openai_client.py`, immediately after the `process_openai_response` function (after line 140) and before the `# Model Catalog Functions` section, insert:
```python
from typing import Any, Iterator

from src.services.providers.base import ProviderAdapter


class OpenAIProviderAdapter:
    """Reference implementation of the canonical :class:`ProviderAdapter`.

    Object-form wrapper over this module's three contract functions. Pure
    delegation — adds no behavior. Serves as the pattern future providers and
    the fat-client thinning effort (0c-2) follow. The live dispatch path still
    uses the module-level functions via ``PROVIDER_ROUTING``; this class is
    additive.
    """

    def request(self, messages: list[dict[str, Any]], model: str, **params: Any) -> Any:
        return make_openai_request(messages, model, **params)

    def stream(self, messages: list[dict[str, Any]], model: str, **params: Any) -> Iterator[Any]:
        return make_openai_request_stream(messages, model, **params)

    def process(self, response: Any) -> dict[str, Any]:
        return process_openai_response(response)


# Static check that the reference adapter satisfies the protocol at import time.
_REFERENCE_ADAPTER: ProviderAdapter = OpenAIProviderAdapter()
```

Note: the test monkeypatches `openai_client.make_openai_request` etc., so the adapter methods must reference those names at call time (they do — module-global lookups), which makes the monkeypatching effective.

- [ ] **Step 4: Run to verify it passes**

Run: `<repo>/.venv/bin/python -m pytest tests/services/test_provider_contract.py -o addopts="" -p no:cacheprovider -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/services/providers/openai_client.py tests/services/test_provider_contract.py
git commit -m "feat(providers): add OpenAIProviderAdapter reference implementation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Type the `PROVIDER_ROUTING` registry

**Files:**
- Modify: `src/handlers/provider_registry.py`

- [ ] **Step 1: Add the typed annotation**

In `src/handlers/provider_registry.py`, add the import near the top (after the existing `import logging` / `from fastapi import HTTPException` block, around line 16):
```python
from src.services.providers.base import ProviderRouting
```
Then change the `PROVIDER_ROUTING` assignment (currently `PROVIDER_ROUTING = {` at line 253) to:
```python
PROVIDER_ROUTING: dict[str, ProviderRouting] = {
```
Leave the dict contents unchanged.

- [ ] **Step 2: Verify the module still imports (no circular import)**

Run:
```bash
<repo>/.venv/bin/python -c "import src.handlers.provider_registry as r; print('routing entries:', len(r.PROVIDER_ROUTING))"
```
Expected: prints `routing entries: <N>` with no ImportError. (Risk: `provider_registry` → `providers.base` → must not import back into `provider_registry`. `base.py` imports only from `typing`, so there is no cycle.)

- [ ] **Step 3: Run the contract suite + a smoke import of the handler**

Run:
```bash
<repo>/.venv/bin/python -m pytest tests/services/test_provider_contract.py -o addopts="" -p no:cacheprovider -q
<repo>/.venv/bin/python -c "import src.handlers.chat_handler; import src.routes.chat; print('handler+route import OK')"
```
Expected: 4 passed; `handler+route import OK`.

- [ ] **Step 4: Commit**

```bash
git add src/handlers/provider_registry.py
git commit -m "refactor(providers): type PROVIDER_ROUTING as dict[str, ProviderRouting]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Final verification (suite + lint + scope)

**Files:** none (verification only).

- [ ] **Step 1: Run the broad test subtree that touches providers/handlers**

Run:
```bash
<repo>/.venv/bin/python -m pytest tests/services/ tests/conceptual_model/ -o addopts="" -p no:cacheprovider -q 2>&1 | tail -5
```
Expected: no NEW failures vs the pre-change baseline. (The local baseline has pre-existing failures — e.g. ~30 in `test_sentry_insights.py`, ~11 in the CM suite from a partial `.venv`. Compare failure COUNT to a `git stash`'d baseline if unsure; the count must not increase.)

- [ ] **Step 2: Verify no import breakage across the codebase**

Run:
```bash
<repo>/.venv/bin/python -c "import src.services.providers.base, src.services.providers.openai_client, src.handlers.provider_registry, src.handlers.chat_handler; print('all imports OK')"
```
Expected: `all imports OK`.

- [ ] **Step 3: Lint the changed/created files**

Run (system tools; pass paths explicitly — zsh does not word-split variables):
```bash
ruff check src/services/providers/base.py src/services/providers/openai_client.py src/handlers/provider_registry.py tests/services/test_provider_contract.py
black --check --line-length 100 src/services/providers/base.py src/services/providers/openai_client.py src/handlers/provider_registry.py tests/services/test_provider_contract.py
isort --check-only --profile black src/services/providers/base.py src/services/providers/openai_client.py src/handlers/provider_registry.py tests/services/test_provider_contract.py
```
Expected: ruff/isort pass. If black/isort flag a NEWLY created file, run them without `--check` on that file and amend the relevant commit. (Pre-existing non-compliance in files you only touched lightly — e.g. `provider_registry.py` if it was already non-compliant — is out of scope; verify against base before reformatting.)

- [ ] **Step 4: Confirm scope**

Run:
```bash
git diff --stat <base-branch>...HEAD
```
Expected: only these paths:
```
src/services/providers/base.py            (new)
src/services/providers/openai_client.py
src/handlers/provider_registry.py
tests/services/test_provider_contract.py  (new)
docs/superpowers/plans/2026-06-16-phase0-step2-canonical-provider-contract.md (new)
```
If any other file appears, investigate before considering the task done.

---

## Self-review notes (spec coverage)

- §6.3 "each provider = a thin adapter implementing `{request, stream, process}` against one canonical contract" → the contract is now an explicit, typed, conformance-tested interface (Tasks 1–4). The *thinning* of fat clients is deferred to 0c-2 per the agreed scope.
- "All policy/pricing/health logic lives outside the adapters" → unchanged here; the reference adapter is pure delegation (no policy/pricing added).
- No behavior change: live dispatch still runs the module-level functions via `PROVIDER_ROUTING`; new types are runtime no-ops; `OpenAIProviderAdapter` is additive and unused by the request path.
- No placeholders; every step shows the exact code/command. Name consistency: `ProviderParams`, `ProviderRouting`, `ProviderAdapter`, `OpenAIProviderAdapter`, `make_openai_request`/`make_openai_request_stream`/`process_openai_response` are used identically across all tasks.
- Known nuance captured: disabled-provider `PROVIDER_ROUTING` entries are all-`None`; the conformance test accepts all-callable OR all-`None` (Task 2).
