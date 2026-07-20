"""Provider price-unit normalization contract tests.

The cost router compares ``model_provider_offers.upstream_cost`` across providers,
so every provider's ingested price MUST end up in the same unit. The pipeline is:

    provider client normalize_*()  ->  emits dollars-per-1M-tokens ($/1M)
    transform_normalized_model_to_db_schema()  ->  applies get_provider_format()
                                                    (per_1m => / 1e6) once
    => metadata.pricing_raw.prompt is true dollars-per-TOKEN

novita / deepinfra previously each emitted a different unit (per-token,
a raw dict, or a bogus amount*10^scale), so the single ``per_1m`` division in
transform produced values off by 1e2-1e6 and the router always picked OpenRouter.

These tests pin the client contract ($/1M) so the shared transform stays correct.
"""

from decimal import Decimal

from src.services.providers.deepinfra_client import normalize_deepinfra_model
from src.services.providers.novita_client import _normalize_pricing


def _prompt(pricing: dict) -> float:
    return float(pricing["prompt"])


# --- NOVITA -----------------------------------------------------------------
# Real Novita API: top-level input_token_price_per_m in units of 1e-4 USD/1M,
# so $/1M = value / 10000 (verified vs Together across glm/minimax/kimi).
def test_novita_converts_price_per_m_to_dollars_per_million():
    out = _normalize_pricing({"input_token_price_per_m": 14000, "output_token_price_per_m": 28000})
    assert _prompt(out) == 1.4
    assert float(out["completion"]) == 2.8


def test_novita_zero_price_is_free():
    out = _normalize_pricing({"input_token_price_per_m": 0, "output_token_price_per_m": 0})
    assert out["prompt"] in (None, "0", "0.0")


# --- DEEPINFRA --------------------------------------------------------------
# Real DeepInfra API: cents_per_input_token (cents PER TOKEN) -> $/1M = *1e4.
def test_deepinfra_emits_dollars_per_million():
    m = {
        "model_name": "anthropic/claude-haiku-4-5",
        "type": "text-generation",
        "pricing": {"cents_per_input_token": 0.0001, "cents_per_output_token": 0.0005},
    }
    out = normalize_deepinfra_model(m)
    assert _prompt(out["pricing"]) == 1.0  # 0.0001 cents/tok -> $1/1M
    assert float(out["pricing"]["completion"]) == 5.0


# --- END TO END: client + transform => per-token ----------------------------
def test_pipeline_produces_consistent_per_token_across_providers():
    from src.services.model_catalog_sync import transform_normalized_model_to_db_schema as T

    def _final(model, slug):
        row = T(model, 999, slug)
        raw = (row.get("metadata") or {}).get("pricing_raw") or {}
        return Decimal(str(raw.get("prompt")))

    dinf = normalize_deepinfra_model(
        {
            "model_name": "anthropic/claude-haiku-4-5",
            "type": "text-generation",
            "pricing": {"cents_per_input_token": 0.0001, "cents_per_output_token": 0.0005},
        }
    )
    # deepinfra is per_1m in the providers table, so transform divides by 1e6.
    # $1/1M == 1e-6 per token.
    assert _final(dinf, "deepinfra") == Decimal("0.000001")


# --- SANITY GUARD: garbage prices never win routing -------------------------
def test_implausible_prices_rejected_and_sane_kept():
    from src.services.model_offers_projection import is_plausible_cost_per_1k

    # Sane band (per-1k): $0.001/1M == 1e-6 .. $1000/1M == 1.0
    assert is_plausible_cost_per_1k(5e-5) is True  # a normal cheap model
    assert is_plausible_cost_per_1k(0.003) is True  # claude-sonnet input
    assert is_plausible_cost_per_1k(None) is False  # unpriced
    assert is_plausible_cost_per_1k(3e-9) is False  # near-zero stale/unit-error price
    assert is_plausible_cost_per_1k(170.0) is False  # per-1M written as per-token


def test_build_offer_rows_drops_implausible_priced_offer():
    from src.services.model_offers_projection import build_offer_rows

    providers = {1: {"slug": "near"}, 2: {"slug": "openrouter"}}
    models = [
        {  # garbage near-zero price -> must be dropped
            "id": "1",
            "provider_id": 1,
            "provider_model_id": "anthropic/claude-sonnet-4-6",
            "modality": "text->text",
            "is_active": True,
            "model_pricing": {"price_per_input_token": 3e-12},
        },
        {  # sane openrouter price -> kept
            "id": "2",
            "provider_id": 2,
            "provider_model_id": "anthropic/claude-sonnet-4-6",
            "modality": "text->text",
            "is_active": True,
            "model_pricing": {"price_per_input_token": 3e-6},
        },
    ]
    rows = build_offer_rows(models, providers)
    slugs = {r["provider_slug"] for r in rows}
    assert slugs == {"openrouter"}  # near's garbage offer excluded from routing
