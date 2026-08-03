"""Legacy and canonical import paths must resolve to ONE module object.

Modules were relocated into sub-packages (``src.services.metrics.*``,
``src.services.providers.*``, …) and the old import paths are kept working by
one-line re-export shims in ``src/services/``.

Before those shims, a meta-path finder synthesised a spec for the legacy name.
The import machinery ignored the alias it had installed and built its own
module, so the same source file was executed twice and the two copies had
separate module-level globals. In the real application import order,
``init_prometheus_remote_write()`` set ``prometheus_writer`` on one copy while
``get_prometheus_writer()`` read ``None`` from the other — metrics were silently
never pushed, with no error to notice.

Any relocated module holding state — a pooled client, a cache, a circuit
breaker — had the same exposure, which is why this is swept across every
relocation rather than spot-checked.
"""

import importlib
import sys

import pytest

from src.services import _MODULE_TO_SUBPKG

REPRESENTATIVES = [
    ("prometheus_remote_write", "metrics"),
    ("anthropic_client", "providers"),
    ("payments", "billing"),
    ("error_monitor", "monitoring"),
    ("pricing_lookup", "pricing"),
    ("response_cache", "cache"),
]


@pytest.mark.parametrize("module_name, subpkg", REPRESENTATIVES)
def test_legacy_and_canonical_paths_are_the_same_object(module_name, subpkg):
    legacy = importlib.import_module(f"src.services.{module_name}")
    canonical = importlib.import_module(f"src.services.{subpkg}.{module_name}")
    assert legacy is canonical, (
        f"{module_name} loaded twice — module-level state will diverge between "
        f"the legacy and canonical import paths"
    )


@pytest.mark.parametrize("module_name, subpkg", REPRESENTATIVES)
def test_module_globals_are_shared(module_name, subpkg):
    """Setting an attribute via one path must be visible via the other."""
    legacy = importlib.import_module(f"src.services.{module_name}")
    canonical = importlib.import_module(f"src.services.{subpkg}.{module_name}")

    sentinel = object()
    legacy._alias_probe = sentinel
    try:
        assert getattr(canonical, "_alias_probe", None) is sentinel
    finally:
        delattr(legacy, "_alias_probe")


def test_prometheus_writer_global_is_shared():
    """The concrete failure this was found through: set on one path, read on the other."""
    legacy = importlib.import_module("src.services.prometheus_remote_write")
    canonical = importlib.import_module("src.services.metrics.prometheus_remote_write")

    original = canonical.prometheus_writer
    try:
        marker = canonical.PrometheusRemoteWriter(enabled=False)
        canonical.prometheus_writer = marker
        assert legacy.get_prometheus_writer() is marker
    finally:
        canonical.prometheus_writer = original


def test_every_relocated_module_resolves_to_one_object():
    """Sweep the whole relocation table, not just the representatives."""
    split = []
    for module_name, subpkg in sorted(_MODULE_TO_SUBPKG.items()):
        try:
            legacy = importlib.import_module(f"src.services.{module_name}")
            canonical = importlib.import_module(f"src.services.{subpkg}.{module_name}")
        except Exception:
            # A module that cannot import here (missing optional dependency) is
            # out of scope for an identity check.
            continue
        if legacy is not canonical:
            split.append(module_name)

    assert not split, f"modules loaded twice: {split}"


def test_every_relocated_module_has_a_shim_on_disk():
    """The shims are the mechanism; a missing one silently reopens the bug."""
    from pathlib import Path

    missing = [
        name
        for name in sorted(_MODULE_TO_SUBPKG)
        if not Path(f"src/services/{name}.py").exists()
    ]
    assert not missing, f"relocated modules without a back-compat shim: {missing}"


def test_meta_path_finder_is_not_installed():
    """Guard against the fragile finder being reinstated alongside the shims.

    Both mechanisms active at once would reintroduce the double-load, since the
    finder runs before the shim file is ever found.
    """
    assert not any(
        type(f).__name__ == "_RelocatedModuleFinder" for f in sys.meta_path
    ), "the legacy meta-path finder is installed again; it double-loads modules"


def test_canonical_first_import_order_is_safe():
    """The order that used to split. Uses a module unlikely to be pre-imported."""
    canonical = importlib.import_module("src.services.pricing.pricing_validation")
    legacy = importlib.import_module("src.services.pricing_validation")
    assert legacy is canonical
