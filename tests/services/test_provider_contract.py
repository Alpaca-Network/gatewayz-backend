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
