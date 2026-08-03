"""Sampling params must default to None so the gateway only forwards them when the
caller explicitly sets them. Injecting temperature/top_p/penalties breaks newer
provider models (claude-sonnet-5 rejects top_p; grok-3 rejects presence_penalty)."""

from src.schemas.proxy import ProxyRequest


def test_sampling_params_default_to_none():
    req = ProxyRequest(model="x", messages=[{"role": "user", "content": "hi"}])
    assert req.temperature is None
    assert req.top_p is None
    assert req.frequency_penalty is None
    assert req.presence_penalty is None


def test_explicit_sampling_params_preserved():
    req = ProxyRequest(
        model="x",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.5,
        top_p=0.9,
        presence_penalty=0.2,
        frequency_penalty=0.1,
    )
    assert req.temperature == 0.5
    assert req.top_p == 0.9
    assert req.presence_penalty == 0.2
    assert req.frequency_penalty == 0.1
