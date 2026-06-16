"""Conformance tests for the canonical provider-adapter contract."""


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
