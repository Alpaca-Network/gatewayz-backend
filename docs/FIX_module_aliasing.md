# Fixed: relocated modules were loading twice

**Status:** fixed. All 75 relocated modules resolve to one object in both import orders.

**Was:** a silent metrics-loss bug in production, plus three intermittent test failures.

---

## The bug

Modules were relocated into sub-packages (`src.services.metrics.*`, `src.services.providers.*`, …). A meta-path finder in `src/services/__init__.py` kept the old import paths working by synthesising a spec:

```python
sys.modules[fullname] = real_module
return importlib.machinery.ModuleSpec(fullname, None)   # loader=None
```

The finder installed the alias correctly. The import machinery then **ignored it** — a spec with no loader makes CPython build its own module object and overwrite the alias, executing the same source file a second time.

Result: two live copies of the module, each with its own module-level globals.

## Why it mattered

`startup.py` imports the legacy path:

```python
from src.services.prometheus_remote_write import init_prometheus_remote_write
```

`init_prometheus_remote_write()` set `prometheus_writer` on copy A. Anything reading through the canonical path got `None` from copy B. **Metrics were silently never pushed** — no exception, no log line, just missing data.

Measured against the real application import order, before the fix:

```
SAME OBJECT in real app import order: False
startup's get_prometheus_writer sees it: False
```

The same exposure applied to any relocated module holding state: pooled provider clients, caches, circuit breakers.

The visible symptom was three tests that passed alone and failed in the full suite, because whether a module split depended on the import order the suite happened to produce.

## The fix

The finder is no longer installed. Every relocated module now has a real shim on disk at `src/services/<name>.py`:

```python
from src.services.<subpkg>.<name> import *
sys.modules[__name__] = sys.modules["src.services.<subpkg>.<name>"]
```

This works where the finder did not because the import machinery **re-reads `sys.modules` after executing a module** — so the rebinding on the last line is picked up, and both names end up bound to one object. No synthesised specs, no import-machinery subtleties; these are ordinary modules.

75 shims, generated mechanically. `_RelocatedModuleFinder` is left in the file but unused, so anything referencing it by name still imports.

## What was tried first, and why it failed

A loader whose `create_module` returned the already-imported module. It fixed the legacy-first order — and took the suite from 3 failures to **48**.

Cause: before calling `exec_module`, `_init_module_attrs(spec, module, override=True)` rewrites `__name__`, `__spec__` and `__loader__` on the module it is handed. Handing it the *canonical* module made that module believe it was the legacy one, breaking `importlib.reload`, pickling, and pytest's module identity checks.

Restoring those attributes inside `exec_module` fixed the corruption, but the canonical-first order still split, and the outcome varied by module and by access pattern (`import x` vs `from x import y`). The finder approach was abandoned rather than tuned further.

## Verification

- All 75 relocated modules resolve to one object, in both import orders.
- The real `create_app()` sequence: legacy and canonical are the same object, and the metrics global is shared.
- Full suite: **2984 passed, 0 failed** (was 2981 passed / 3 failed). The three failures resolved because the root cause is gone, not because assertions moved.
- Regression cover in `tests/services/test_module_aliasing.py`, including a guard that fails if the finder is reinstated alongside the shims — both active at once would reintroduce the double-load.

## Guidance

Prefer the canonical path in new code (`src.services.metrics.prometheus_remote_write`). The shims exist for compatibility, not as the recommended import.

Adding a module to a sub-package? Add a shim too, or `test_every_relocated_module_has_a_shim_on_disk` will fail.
