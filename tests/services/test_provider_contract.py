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
    and one (sync) stream function name. Env-independent (static source of truth)."""
    from src.handlers.provider_registry import PROVIDER_FUNCTIONS

    assert len(PROVIDER_FUNCTIONS) >= 25, "expected ~30 providers declared"
    for slug, fns in PROVIDER_FUNCTIONS.items():
        has_process = any(f.startswith("process_") for f in fns)
        has_stream = any(f.endswith("_stream") for f in fns)
        # a request fn is a make_* that is not a stream fn
        has_request = any(f.startswith("make_") and not f.endswith("_stream") for f in fns)
        assert has_process, f"{slug}: no process_* function declared"
        assert has_stream, f"{slug}: no *_stream function declared"
        assert has_request, f"{slug}: no non-stream make_* request function declared"


def test_provider_routing_entries_are_shape_consistent(monkeypatch):
    """With all providers enabled, every PROVIDER_ROUTING entry exposes exactly
    request/process/stream keys, each a callable (import-failure sentinels count).

    Note: PROVIDER_ROUTING is filtered to ENABLED_PROVIDERS at import time
    (provider_registry.py), and the default test env enables only 'openrouter'
    (which is the fallback, not a routing key) — so the live registry is empty
    under test. We reload it with all providers enabled to get real coverage,
    then restore the default-filtered module for other tests.
    """
    import importlib

    import src.config.config as config_mod
    import src.handlers.provider_registry as reg

    monkeypatch.setattr(config_mod.Config, "ENABLED_PROVIDERS", None)
    try:
        reg = importlib.reload(reg)
        routing = reg.PROVIDER_ROUTING
        assert len(routing) >= 25, f"expected ~30 providers enabled, got {len(routing)}"
        for slug, entry in routing.items():
            assert set(entry.keys()) == {"request", "process", "stream"}, f"{slug}: wrong keys"
            for key in ("request", "process", "stream"):
                assert callable(entry[key]), f"{slug}.{key} not callable when enabled"
    finally:
        monkeypatch.undo()
        importlib.reload(reg)  # restore default-env (filtered) registry


def test_openai_reference_adapter_conforms_and_delegates(monkeypatch):
    """OpenAIProviderAdapter satisfies the ProviderAdapter protocol and delegates
    to the existing module-level functions without adding behavior."""
    from src.services.providers import openai_client
    from src.services.providers.base import ProviderAdapter

    adapter = openai_client.OpenAIProviderAdapter()
    assert isinstance(adapter, ProviderAdapter)  # runtime_checkable structural check

    monkeypatch.setattr(
        openai_client,
        "make_openai_request",
        lambda messages, model, **kw: ("req", messages, model, kw),
    )
    monkeypatch.setattr(
        openai_client,
        "make_openai_request_stream",
        lambda messages, model, **kw: iter([("chunk", model)]),
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
