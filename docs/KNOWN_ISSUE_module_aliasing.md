# Known issue: relocated modules can load twice

**Status:** diagnosed, not fixed. An attempted fix was reverted — see "Why it is not fixed yet".

**Impact:** latent. Causes three intermittent test failures today; the production exposure is real but currently unhit.

---

## What happens

`src/services/__init__.py` installs a meta-path finder so legacy imports keep working after modules were relocated into sub-packages:

```python
from src.services.prometheus_remote_write import init_prometheus_remote_write
# actually lives at src.services.metrics.prometheus_remote_write
```

The finder imports the real module, aliases it into `sys.modules` under the old name, then returns:

```python
return importlib.machinery.ModuleSpec(fullname, None)   # loader=None
```

A spec with **no loader** makes CPython construct a *second* module object from the same source file and overwrite the alias. The result is two live copies of the module, each with its own module-level globals.

Reproduce:

```python
import importlib
b = importlib.import_module("src.services.pricing.pricing_lookup")   # canonical FIRST
a = importlib.import_module("src.services.pricing_lookup")           # legacy second
assert a is b   # fails
```

Order matters: legacy-first resolves to one object, canonical-first splits.

## Why it matters

Any relocated module holding state has split-brain exposure. The concrete case:

- `startup.py` calls `init_prometheus_remote_write()`, which sets `prometheus_writer` on copy A.
- A consumer importing the canonical path reads `prometheus_writer` from copy B, gets `None`.
- **Metrics are silently never pushed.** No error, no log — just missing data.

The same shape applies to pooled provider clients, caches, and circuit breakers.

Today's visible symptom is three intermittent test failures:

- `tests/services/test_prometheus_remote_write.py::test_init_prometheus_remote_write_enabled`
- `tests/services/test_prometheus_remote_write.py::test_get_prometheus_writer`
- `tests/services/test_nosana_client.py` (one test, varies by run)

They pass in isolation and fail in the full suite, because whether a module splits depends on the import order the suite happens to produce.

## Why it is not fixed yet

The obvious fix is a loader that returns the already-imported module:

```python
class _AliasLoader(importlib.abc.Loader):
    def __init__(self, real_module): self._real_module = real_module
    def create_module(self, spec): return self._real_module
    def exec_module(self, module): pass
```

This **does** fix the legacy-first order — and it fixed the prometheus and nosana failures when run against `tests/services/` alone.

Against the full suite it caused **48 failures**, up from 3. Some code paths depend on the current (broken) resolution, and changing which object wins broke them. The change was reverted.

Fixing this properly means either:

1. Understanding why canonical-first still splits even with a correct loader, and handling the partially-initialised-module case during re-entrant imports; or
2. Removing the aliasing machinery entirely and updating the ~40 legacy import sites to canonical paths — mechanical, larger diff, no import-machinery subtlety.

**Option 2 is the recommendation.** The finder exists to avoid a rename; the rename is less risky than the machinery.

## Interim guidance

- **Import relocated modules by their canonical path** in new code (`src.services.metrics.prometheus_remote_write`, not `src.services.prometheus_remote_write`).
- Do not add new module-level mutable state to a relocated module.
- If metrics stop appearing in Prometheus, check this first: `get_prometheus_writer()` returning `None` after startup succeeded is the signature.
