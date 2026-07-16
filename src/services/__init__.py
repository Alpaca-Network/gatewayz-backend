# Services package
# Lazy imports for testing - makes modules accessible without importing dependencies
import importlib
import importlib.abc
import importlib.machinery
import sys

_PROVIDER_MODULES = {
    "alibaba_cloud_client",
    "anthropic_client",
    "anthropic_transformer",
    "cerebras_client",
    "chatterbox_tts_client",
    "code_router_client",
    "deepinfra_catalog",
    "featherless_client",
    "fireworks_client",
    "google_vertex_client",
    "groq_client",
    "novita_client",
    "openai_client",
    "openrouter_client",
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
    "trial_validation",
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
        return importlib.machinery.ModuleSpec(fullname, None)

    def load_module(self, fullname):
        """Called by find_module path — module is already in sys.modules."""
        return sys.modules[fullname]


# Install the finder once at package import time
if not any(isinstance(f, _RelocatedModuleFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _RelocatedModuleFinder())


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
