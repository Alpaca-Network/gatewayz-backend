# Services package
# Lazy imports for testing - makes modules accessible without importing dependencies
import importlib
import importlib.abc
import importlib.machinery
import sys

_PROVIDER_MODULES = {
    "aimo_client",
    "akash_client",
    "alibaba_cloud_client",
    "alpaca_network_client",
    "anthropic_client",
    "anthropic_transformer",
    "canopywave_client",
    "cerebras_client",
    "chatterbox_tts_client",
    "chutes_client",
    "clarifai_client",
    "cloudflare_workers_ai_client",
    "code_router_client",
    "cohere_client",
    "deepinfra_client",
    "fal_image_client",
    "featherless_client",
    "fireworks_client",
    "google_vertex_client",
    "groq_client",
    "huggingface_client",
    "image_generation_client",
    "modelz_client",
    "morpheus_client",
    "near_client",
    "nebius_client",
    "nosana_client",
    "novita_client",
    "openai_client",
    "openrouter_client",
    "simplismart_client",
    "sybil_client",
    "together_client",
    "xai_client",
    "zai_client",
}

_CACHE_MODULES = {
    "auth_cache",
    "cache_warmer",
    "catalog_response_cache",
    "db_cache",
    "local_memory_cache",
    "model_capabilities_cache",
    "model_catalog_cache",
    "model_mappings_cache",
    "response_cache",
    "simple_health_cache",
    "user_lookup_cache",
}

_MONITORING_MODULES = {
    "autonomous_monitor",
    "connection_pool_monitor",
    "error_monitor",
    "gateway_health_service",
    "health_alerting",
    "health_routing",
    "health_snapshots",
    "intelligent_health_monitor",
    "model_health_monitor",
    "passive_health_monitor",
    "provider_credit_monitor",
}

_METRICS_MODULES = {
    "prometheus_metrics",
    "prometheus_exporter",
    "prometheus_pb2",
    "prometheus_remote_write",
    "grafana_metrics_service",
    "metrics_aggregator",
    "metrics_instrumentation",
    "metrics_parser",
}

_PRICING_MODULES = {
    "pricing",
    "pricing_audit",
    "pricing_lookup",
    "pricing_validation",
}

_BILLING_MODULES = {
    "credit_handler",
    "credit_precheck",
    "daily_usage_limiter",
    "payments",
    "trial_service",
    "trial_validation",
    "partner_trial_service",
}

_ALL_RELOCATED = {
    "providers": _PROVIDER_MODULES,
    "cache": _CACHE_MODULES,
    "monitoring": _MONITORING_MODULES,
    "metrics": _METRICS_MODULES,
    "pricing": _PRICING_MODULES,
    "billing": _BILLING_MODULES,
}

# Build a flat lookup: module_name -> sub-package
# Exclude modules whose name collides with a sub-package directory name
# (e.g. "pricing" is both a module and a sub-package).  For those, the
# sub-package __init__.py is the real package; `from src.services.pricing
# import calculate_cost` resolves naturally because `src/services/pricing/`
# is a real directory with `__init__.py`.
_SUBPKG_NAMES = set(_ALL_RELOCATED.keys())
_MODULE_TO_SUBPKG: dict[str, str] = {}
for _subpkg, _modules in _ALL_RELOCATED.items():
    for _mod in _modules:
        if _mod not in _SUBPKG_NAMES:
            _MODULE_TO_SUBPKG[_mod] = _subpkg


class _AliasLoader(importlib.abc.Loader):
    """Bind an already-imported module to a second name without re-executing it.

    Two things have to be true and neither is the default.

    1. ``create_module`` must hand back the real module. The finder below used
       to return ``ModuleSpec(fullname, None)`` — a spec with no loader — and
       the import machinery responded by building its own module and discarding
       the alias the finder had just put in ``sys.modules``. That produced two
       live copies of the same file, each with its own module-level globals.
       For ``prometheus_remote_write`` that meant ``init_...()`` set
       ``prometheus_writer`` on one copy while ``get_prometheus_writer()`` read
       ``None`` from the other, so metrics were silently never pushed.

    2. ``exec_module`` must put the module's identity back. Before calling it,
       ``_init_module_attrs(spec, module, override=True)`` rewrites
       ``__name__``, ``__spec__`` and ``__loader__`` on the module it was
       handed — which here is the *canonical* module. Left alone, the canonical
       module ends up believing it is the legacy one, which breaks
       ``importlib.reload``, pickling, and pytest's module identity checks. An
       earlier attempt at this fix omitted the restore and took the suite from
       3 failures to 48.
    """

    def __init__(self, real_module):
        self._real_module = real_module
        # Captured before the machinery has a chance to overwrite it.
        self._canonical_spec = getattr(real_module, "__spec__", None)
        self._canonical_name = getattr(real_module, "__name__", None)
        self._canonical_loader = getattr(real_module, "__loader__", None)

    def create_module(self, spec):
        # Reuse the real module. Returning None would let the machinery build
        # an empty one and re-execute the source into it.
        return self._real_module

    def exec_module(self, module):
        # Already executed under its canonical name — re-running it would reset
        # module-level state, which is the thing this whole mechanism exists to
        # preserve. Only restore the identity attributes clobbered above.
        if self._canonical_name is not None:
            module.__name__ = self._canonical_name
        if self._canonical_spec is not None:
            module.__spec__ = self._canonical_spec
        if self._canonical_loader is not None:
            module.__loader__ = self._canonical_loader


class _RelocatedModuleFinder(importlib.abc.MetaPathFinder):
    """Redirect ``import src.services.<moved_module>`` to its sub-package.

    This handles the ``from src.services.prometheus_metrics import X`` pattern
    which bypasses ``__getattr__`` and goes straight to the import machinery.
    """

    _PREFIX = __name__ + "."  # "src.services."

    def find_module(self, fullname, path=None):
        """Python 3.4+ compat — delegates to find_spec but keeps find_module
        for older importlib versions."""
        if self.find_spec(fullname, path) is not None:
            return self
        return None

    # Guard against re-entrant calls during import
    _active: set = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self._active:
            return None
        if not fullname.startswith(self._PREFIX):
            return None
        # e.g. fullname = "src.services.prometheus_metrics"
        remainder = fullname[len(self._PREFIX) :]
        # Only handle single-level names (not "src.services.metrics.prometheus_metrics")
        if "." in remainder:
            return None
        subpkg = _MODULE_TO_SUBPKG.get(remainder)
        if subpkg is None:
            return None
        real_name = f"{__name__}.{subpkg}.{remainder}"
        # Import the real module and alias it under the old name
        self._active.add(fullname)
        try:
            real_module = importlib.import_module(real_name)
        finally:
            self._active.discard(fullname)
        sys.modules[fullname] = real_module
        # A None loader here makes CPython build a second module and throw the
        # alias away — see _AliasLoader.
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(real_module))

    def load_module(self, fullname):
        """Called by find_module path — module is already in sys.modules."""
        return sys.modules[fullname]


# The finder above is NO LONGER INSTALLED.
#
# It aliased legacy import paths by returning a synthesised ModuleSpec, and the
# import machinery responded by building its own module object and discarding
# the alias — leaving two separately-executed copies of the same file, each
# with its own globals. In the real application import order,
# ``src.services.prometheus_remote_write`` and
# ``src.services.metrics.prometheus_remote_write`` were different objects, so
# ``init_prometheus_remote_write()`` set the writer on one while
# ``get_prometheus_writer()`` read ``None`` from the other. Metrics were
# silently dropped, with no error to notice.
#
# Every relocated module now has a real one-line shim on disk
# (``src/services/<name>.py``) that ends with
# ``sys.modules[__name__] = sys.modules["<canonical>"]``. The machinery re-reads
# sys.modules after executing a module, so both paths end up bound to one
# object. Ordinary modules, no import-machinery subtleties.
#
# The class is kept only so that anything referencing it by name keeps
# importing; it does nothing unless someone installs it.


def __getattr__(name):
    # Check all relocated sub-packages for backward compatibility
    for subpkg, modules in _ALL_RELOCATED.items():
        if name in modules:
            module = importlib.import_module(f"{__name__}.{subpkg}.{name}")
            setattr(sys.modules[__name__], name, module)
            return module
    # Modules that remain directly under src/services/
    if name in ("rate_limiting", "huggingface_hub_service"):
        module = importlib.import_module(f"{__name__}.{name}")
        setattr(sys.modules[__name__], name, module)
        return module
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
