"""Cross-reference pricing is a universal tier-4 fallback: any provider whose
models OpenRouter also lists gets priced automatically, with an org-alias map for
precise matching (moonshot -> moonshotai)."""

from src.services.pricing import pricing_lookup as pl


def _index():
    # Mimics _build_openrouter_pricing_index output: keyed by full id + base id.
    price = {"prompt": "0.000003", "completion": "0.000015"}
    return {
        "moonshotai/kimi-k3": price,
        "moonshotai/kimi-k3".lower(): price,
        "kimi-k3": price,
    }


def test_alias_precise_match_for_moonshot():
    p = pl._get_cross_reference_pricing("moonshot/kimi-k3", _index(), provider="moonshot")
    assert p is not None
    assert float(p["prompt"]) == 3e-6
    assert float(p["completion"]) == 1.5e-5


def test_base_id_match_without_provider():
    # Still works via base-id even when no provider/alias is supplied.
    p = pl._get_cross_reference_pricing("moonshot/kimi-k3", _index())
    assert p is not None and float(p["prompt"]) == 3e-6


def test_enrich_prices_direct_provider_from_openrouter():
    # A moonshot model with no DB/manual pricing gets cross-referenced automatically.
    model = {"id": "moonshot/kimi-k3", "pricing": {"prompt": None, "completion": None}}
    out = pl.enrich_model_with_pricing(
        model, gateway="moonshot", pricing_batch={}, openrouter_index=_index()
    )
    assert out is not None
    assert out["pricing_source"] == "cross-reference"
    assert float(out["pricing"]["prompt"]) == 3e-6


def test_moonshot_is_gateway_provider_now():
    assert "moonshot" in pl.GATEWAY_PROVIDERS
    assert pl.OPENROUTER_PROVIDER_ALIASES["moonshot"] == "moonshotai"
